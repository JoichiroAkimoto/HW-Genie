"""Titan Arena (ToE) operations.

Exposes two entry points:

* :func:`run_titan_arena` — single-rival battle (used by ``toe attack`` and as
  a unit helper). Accepts any of ``estimate`` / ``hybrid`` engines.
* :func:`run_titan_arena_tier` — drive a full tier end-to-end. Pulls the
  current ``titanArenaGetStatus``, decides between raid (when ``canRaid``) and
  per-rival fights, and finishes the tier with ``titanArenaCompleteTier`` and
  ``titanArenaFarmDailyReward``. Engine is pluggable so a running userscript
  can supply the exact ``progress/result`` the server validates against.
"""
from __future__ import annotations

import json
import logging
import math
import sys
import threading
import time
from typing import Any

from hw_genie.battle.engine import (
    BattleEngine,
    BattleEstimate,
    BridgeError,
    BridgeDeadError,
    BridgeTimeoutError,
    JsBridgeBattleEngine,
    PythonBattleEngine,
)
from hw_genie.core.client import ApiAction, Emojis, HWClient, ResponseStatus

logger = logging.getLogger(__name__)


def _cancel_requested() -> bool:
    """True when a cooperative Ctrl+C cancellation was requested."""
    try:
        from hw_genie.runner import is_cancelled

        return bool(is_cancelled())
    except Exception:
        return False


def _interrupted_error(partial_results: list | None = None) -> InterruptedError:
    """Build an InterruptedError carrying already-collected partial results."""
    err = InterruptedError("interrupted by user")
    # BridgeDeadError-style payload so tier callers can still report progress.
    err.partial_results = list(partial_results or [])  # type: ignore[attr-defined]
    return err


def _shorten_response(response: Any, limit: int = 300) -> str:
    """One-line preview of an EndBattle response for error output."""
    try:
        text = json.dumps(response, ensure_ascii=False, default=str)
    except Exception:
        text = str(response)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _fmt_duration(seconds: float) -> str:
    """Compact duration for progress lines (e.g. 45s, 1m05s, 2h03m)."""
    if not math.isfinite(seconds):
        return "?"
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


# --- Compact ToE progress output (quiet|line|verbose) ---
# verbose = today's full output (backward compat, function default).
# quiet   = only rival-decided lines + tier summary + errors.
# line    = one updating line per account + rival-decided + summary.
# CLI default is `line` (see main.py parsers); function default stays
# `verbose` so existing callers/tests keep today's output.
VALID_PROGRESS_MODES = ("quiet", "line", "verbose")
PROGRESS_THROTTLE_SECS = 5.0


def _normalize_progress(progress: Any) -> str:
    """Return a valid progress mode, falling back to ``verbose``."""
    if isinstance(progress, str) and progress in VALID_PROGRESS_MODES:
        return progress
    return "verbose"


def _is_tty() -> bool:
    """True when stdout is an interactive terminal."""
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _vprint(progress: str, msg: str) -> None:
    """Print ``msg`` only in verbose mode (per-attempt details)."""
    if _normalize_progress(progress) == "verbose":
        print(msg, flush=True)


def _aprint(progress: str, msg: str) -> None:
    """Print ``msg`` in all modes (rival-decided / summary / errors).

    In ``line`` + TTY mode the in-place status line (``\\r`` without
    newline) is active, so start on a fresh line to avoid overwriting it.
    """
    if _normalize_progress(progress) == "line" and _is_tty():
        try:
            sys.stdout.write("\n")
        except Exception:
            pass
    print(msg, flush=True)


def _emit_status_line(
    account: str | None,
    text: str,
    dashboard: Any,
    state: dict[str, Any],
    progress: str,
    force: bool = False,
) -> None:
    """Emit one throttled per-account status line (``line`` mode only).

    Delegates to the shared multi-account dashboard when one is provided
    (lock-guarded, per-account throttling); otherwise falls back to a
    local throttled print (TTY: ``\\r`` in-place update, non-TTY: at most
    one line per :data:`PROGRESS_THROTTLE_SECS`).
    """
    if _normalize_progress(progress) != "line":
        return
    acc = str(account or "?")
    line = text if text.startswith(f"[{acc}]") else f"[{acc}] {text}"
    if dashboard is not None and hasattr(dashboard, "update"):
        try:
            dashboard.update(acc, line, force=force)
            return
        except Exception:
            pass
    try:
        is_tty = _is_tty()
    except Exception:
        is_tty = False
    if is_tty:
        try:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()
        except Exception:
            pass
        return
    now = time.monotonic()
    try:
        throttle = float(state.get("throttle_secs", PROGRESS_THROTTLE_SECS))
    except Exception:
        throttle = PROGRESS_THROTTLE_SECS
    last = float(state.get("last_emit", 0.0) or 0.0)
    if force or (now - last) >= throttle:
        state["last_emit"] = now
        print(line, flush=True)


def _build_status_text(
    rival_id: str,
    attempt_no: int,
    total: int,
    wins: int,
    last: str,
    elapsed: float,
    *,
    tier: Any | None = None,
    rival_no: int | None = None,
    rival_total: int | None = None,
) -> str:
    """Build the per-account status body (caller adds ``[account]``).

    The tier and rival position are included when the caller provides them,
    so a long tier run shows where it is (``T7 3/9 rival``) rather than only
    the rival ID and attempt counter.
    """
    pace = elapsed / attempt_no if attempt_no > 0 else 0.0
    suffix = f" last={last}" if last else ""
    stage = ""
    if tier is not None:
        stage = f"T{tier} "
        if rival_no is not None and rival_total:
            stage += f"{rival_no}/{rival_total} "
    return (
        f"{stage}rival {rival_id} [{attempt_no}/{total}] "
        f"wins {wins}{suffix} "
        f"pace {_fmt_duration(pace)}/attempt elapsed {_fmt_duration(elapsed)}"
    )


def _finish_status_line(progress: str) -> None:
    """Terminate an active TTY ``\\r`` status line with a newline."""
    if _normalize_progress(progress) == "line" and _is_tty():
        try:
            sys.stdout.write("\n")
            sys.stdout.flush()
        except Exception:
            pass


def _engine_calc(engine: BattleEngine, battle: dict[str, Any], progress: str = "verbose") -> BattleEstimate:
    """Run ``engine.calc``, routing bridge heartbeats to stdout.

    Only :class:`JsBridgeBattleEngine` emits progress callbacks; other
    engines use the plain ``calc(battle)`` call. Heartbeats print only in
    ``verbose`` mode so ``quiet``/``line`` stay compact (suppression here
    keeps :mod:`hw_genie.battle.engine` untouched).
    """
    if isinstance(engine, JsBridgeBattleEngine):
        if _normalize_progress(progress) == "verbose":
            return engine.calc(battle, on_progress=lambda msg: print(msg, flush=True))
        return engine.calc(battle, on_progress=None)
    return engine.calc(battle)


def _summarize_end_battle(rival_id: str, est: BattleEstimate, response: Any) -> str:
    """Concise one-line EndBattle summary (full payload goes to debug log)."""
    score: Any = "?"
    earned: Any = "?"
    stars: Any = "?"
    if isinstance(response, dict):
        raw_score = response.get("attackScore")
        score = raw_score if raw_score is not None else "?"
        raw_earned = response.get("attackScoreEarned")
        if raw_earned is None:
            raw_earned = response.get("scoreEarned")
        earned = raw_earned if raw_earned is not None else "?"
        res_obj = response.get("result")
        if isinstance(res_obj, dict):
            raw_stars = res_obj.get("stars")
            if raw_stars is None:
                raw_stars = getattr(est, "stars", None)
            stars = raw_stars if raw_stars is not None else "?"
        else:
            est_stars = getattr(est, "stars", None)
            stars = est_stars if est_stars is not None else "?"
    return (
        f"rival {rival_id}: {'WIN' if est.win else 'LOSS'} stars={stars} "
        f"attackScore={score} (+{earned})"
    )


def fetch_titan_arena_status(client: HWClient) -> dict[str, Any]:
    """Return the ``response`` of ``titanArenaGetStatus``."""
    res = client.call(
        {"calls": [{"name": ApiAction.TITAN_ARENA_GET_STATUS, "args": {}, "ident": "body"}]}
    )
    if not res.is_success:
        raise RuntimeError(
            f"titanArenaGetStatus failed ({res.error_name or res.status.value}): {res.detail}"
        )
    detail = res.detail if isinstance(res.detail, dict) else {}
    return detail.get("response", {}) if "response" in detail else detail


AUTO_RIVAL_SCORE_THRESHOLD = 250

# Sentinel for "no tier has suppressed the raid yet". ``None`` is a valid tier
# value, so a plain ``None`` cannot encode "unset" (it would never reset).
_NO_TIER = object()

# ``completed_tier_key`` uses a string key so a successful CompleteTier with a
# missing tier is still distinguishable from "not completed yet" (``None``).
_TIER_KEY_NONE = "<none>"


def _tier_key(tier: Any) -> str:
    """Normalize a tier for comparison (``8`` / ``"8"`` / ``8.0`` → ``"8"``).

    A missing tier maps to :data:`_TIER_KEY_NONE`, never ``None``, so a
    completed tier without a tier value is still deduplicated.
    """
    if tier is None:
        return _TIER_KEY_NONE
    text = str(tier)
    try:
        return str(int(float(text)))
    except (TypeError, ValueError, OverflowError):
        return text

# Static team rotation tried in order after the saved team when --titans is
# omitted. Entries are 5 titan IDs; the saved team (teamGetAll.titan_arena)
# always goes first and duplicates are skipped.
MAX_CONSECUTIVE_BRIDGE_TIMEOUTS = 3

# Consecutive raid-tier (canRaid=True) passes with zero new clears before
# stopping. With end_on_loss=False losses bank nothing, so such passes are
# independent fresh draws: a few retries are intentional (seed gacha), but
# grinding them unboundedly wastes hours on 0%-win matchups. Non-raid
# (canRaid=False) tiers do not use this counter: they stop after exactly 1
# zero-win pass via the early return in the tier loop below.
MAX_CONSECUTIVE_NO_PROGRESS_PASSES = 3

# --- Lightweight bridge warnings (display only; battle logic untouched) ---
# The userscript hint below prints once per process so multi-account runs do
# not repeat it per worker/attempt; the Invalid-battle streak warns once per
# account when a hybrid engine keeps getting server-rejected progress (bridge
# likely disconnected and silently falling back to estimate-only). Polling
# intervals and timeouts are intentionally left unchanged.
_BRIDGE_WARN_LOCK = threading.Lock()
_BRIDGE_USERSCRIPT_HINT_SHOWN = False
# Per-account consecutive "Invalid battle" count / warned set. Process-wide
# state is only an approximation under multi-account parallelism: workers
# interleave, so counts are keyed by account label to avoid one account's
# streak tripping (or resetting) another's. Labels come from
# ``run_titan_arena(..., account_label=...)``; unlabelled calls share the ""
# bucket. ``estimate_only`` / ``abandoned`` outcomes never reach
# :func:`_track_invalid_battle` (no EndBattle was sent) and explicitly reset
# their account's streak via :func:`_reset_invalid_battle` so a planned
# non-submit never inherits a stale count.
_INVALID_BATTLE_STREAKS: dict[str, int] = {}
_INVALID_BATTLE_WARNED_ACCOUNTS: set[str] = set()
# Legacy process-wide aliases kept for backward-compatible tests/callers:
# they are the "" (unlabelled) bucket itself; labelled accounts use the dicts.
_INVALID_BATTLE_STREAK = 0
_INVALID_BATTLE_WARNED = False
INVALID_BATTLE_STREAK_WARN_AT = 3


def _warn_userscript_hint_once() -> None:
    """Print the userscript setup hint only for the first bridge failure."""
    global _BRIDGE_USERSCRIPT_HINT_SHOWN
    with _BRIDGE_WARN_LOCK:
        if _BRIDGE_USERSCRIPT_HINT_SHOWN:
            return
        _BRIDGE_USERSCRIPT_HINT_SHOWN = True
    print(
        f"{Emojis.INFO}Hint: ensure `hw-genie auth-server` is running and the browser has the game open with the userscript active (any game page works; the Titan Arena screen is not required). Falling back to estimate-only.",
        flush=True,
    )


def _reset_invalid_battle(account: str | None = None) -> None:
    """Clear one account's Invalid-battle streak (planned non-submit)."""
    global _INVALID_BATTLE_STREAK
    key = str(account or "")
    with _BRIDGE_WARN_LOCK:
        if key == "":
            _INVALID_BATTLE_STREAK = 0
        else:
            _INVALID_BATTLE_STREAKS.pop(key, None)


def _track_invalid_battle(
    outcome: dict[str, Any], engine: Any, account: str | None = None
) -> None:
    """Warn once per account when a hybrid engine keeps hitting ``Invalid battle``.

    The server re-simulates estimate-only progress, so consecutive
    rejections under a bridge engine mean the bridge is likely
    disconnected (results silently unverified). Pure display logic:
    counters and prints only, no polling/timeout changes. Streaks are
    keyed by ``account`` (see module comment); callers that omit it share
    the "" bucket (the legacy ``_INVALID_BATTLE_STREAK`` globals).
    """
    global _INVALID_BATTLE_STREAK, _INVALID_BATTLE_WARNED
    if not isinstance(engine, JsBridgeBattleEngine):
        return
    key = str(account or "")
    if isinstance(outcome, dict) and outcome.get("end_error") == "Invalid battle":
        with _BRIDGE_WARN_LOCK:
            if key == "":
                _INVALID_BATTLE_STREAK += 1
                streak = _INVALID_BATTLE_STREAK
                already_warned = _INVALID_BATTLE_WARNED
                if streak >= INVALID_BATTLE_STREAK_WARN_AT and not already_warned:
                    _INVALID_BATTLE_WARNED = True
                else:
                    return
            else:
                streak = _INVALID_BATTLE_STREAKS.get(key, 0) + 1
                _INVALID_BATTLE_STREAKS[key] = streak
                already_warned = key in _INVALID_BATTLE_WARNED_ACCOUNTS
                if streak >= INVALID_BATTLE_STREAK_WARN_AT and not already_warned:
                    _INVALID_BATTLE_WARNED_ACCOUNTS.add(key)
                else:
                    return
        print(
            f"{Emojis.WARNING}Invalid battle x{streak} with the hybrid engine: "
            "the bridge may be disconnected (progress falls back to "
            "estimate-only and the server rejects it). Ensure "
            "`hw-genie auth-server` is running with the userscript active, "
            "then re-run.",
            flush=True,
        )
        return
    with _BRIDGE_WARN_LOCK:
        if key == "":
            _INVALID_BATTLE_STREAK = 0
        else:
            _INVALID_BATTLE_STREAKS.pop(key, None)


def _is_bridge_timeout(res: dict[str, Any]) -> bool:
    """True when the attempt never reached the userscript (bridge timeout).

    Prefers the structured ``bridge_timeout`` flag set by
    :func:`run_titan_arena`; the message-substring check is a legacy
    fallback for dicts built without it (e.g. older callers/tests).
    """
    if res.get("bridge_timeout") is True:
        return True
    return "did not finish battle within" in str(res.get("bridge_error") or "")


def _loss_stars(est: Any) -> int:
    """Best-effort ``stars`` of a losing estimate for best-loss ranking.

    Bridge/Mock estimates may carry non-int stars; anything unparsable
    ranks as 0 so ranking never raises.
    """
    try:
        return int(getattr(est, "stars", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _attack_score(value: Any) -> int:
    """Best-effort integer ``attackScore`` from an EndRaid result entry.

    The server may echo the score as a string; non-dict or unparseable
    entries rank as 0 so completion checks never raise.
    """
    if not isinstance(value, dict):
        return 0
    try:
        return int(value.get("attackScore") or 0)
    except (TypeError, ValueError):
        return 0


def _has_attack_score(value: Any) -> bool:
    """True when ``value`` carries a parseable ``attackScore``.

    A complete-looking EndRaid echo must not be trusted on key presence
    alone: a stub entry without the score would read as 0 and silently mark
    the tier incomplete (or, worse, complete once the threshold is 0).
    """
    if not isinstance(value, dict) or "attackScore" not in value:
        return False
    try:
        int(value.get("attackScore"))
    except (TypeError, ValueError):
        return False
    return True


STATIC_TEAM_ROTATION: list[list[int]] = [
    [4044, 4012, 4013, 4043, 4010],
    [4044, 4013, 4043, 4014, 4010],
    [4042, 4013, 4043, 4014, 4010],
    [4012, 4042, 4013, 4043, 4010],
    [4044, 4013, 4043, 4011, 4010],
    # --- docs/superpowers/ToE.md の組み合わせ（光水） ---
    [4044, 4003, 4043, 4002, 4004],
    [4044, 4003, 4043, 4002, 4001],
    [4044, 4003, 4043, 4002, 4000],
    [4044, 4003, 4043, 4004, 4001],
    [4044, 4003, 4043, 4004, 4000],
    [4044, 4003, 4043, 4001, 4000],
    [4003, 4042, 4043, 4002, 4004],
    [4003, 4042, 4043, 4002, 4001],
    [4003, 4042, 4043, 4002, 4000],
    [4003, 4042, 4043, 4004, 4001],
    [4003, 4042, 4043, 4004, 4000],
    [4003, 4042, 4043, 4001, 4000],
    # --- 水 ---
    [4003, 4002, 4004, 4001, 4000],
    # --- 水+1 ---
    [4033, 4003, 4023, 4004, 4000],
    [4033, 4003, 4004, 4001, 4000],
    [4003, 4013, 4004, 4001, 4000],
    [4003, 4023, 4004, 4001, 4000],
    [4033, 4003, 4002, 4004, 4001],
    [4003, 4013, 4002, 4004, 4001],
    [4003, 4023, 4002, 4004, 4001],
    # --- 水2火3 ---
    [4003, 4013, 4004, 4014, 4010],
    # --- 水3火2（Tier8 の光壁 -480913 に 15 seeds で WIN の実績あり） ---
    [4003, 4004, 4014, 4001, 4010],
    # --- 大地+光 ---
    [4023, 4043, 4024, 4022, 4040],
]


def _resolve_team_rotation(client: HWClient, titans: list[int] | None) -> list[list[int]]:
    """Build the per-rival team rotation.

    Explicit ``--titans`` → that team only (legacy single-team behavior).
    Omitted → saved team first, then :data:`STATIC_TEAM_ROTATION` in order,
    skipping duplicates of the saved team.
    """
    if titans is not None:
        validated = _resolve_titans(client, titans)
        return [validated]
    saved = _resolve_titans(client, None)
    rotation = [saved]
    for team in STATIC_TEAM_ROTATION:
        if [int(t) for t in team] not in rotation:
            rotation.append([int(t) for t in team])
    return rotation


def _select_auto_rivals(status: dict, threshold: int = AUTO_RIVAL_SCORE_THRESHOLD) -> list[str]:
    rivals = status.get("rivals") or {}
    if not isinstance(rivals, dict):
        return []
    candidates = []
    for rid, info in rivals.items():
        if not isinstance(info, dict):
            continue
        score = info.get("attackScore")
        try:
            s = int(score) if score is not None else 0
        except Exception:
            s = 0
        if s < threshold:
            try:
                p = int(str(info.get("power", "0")))
            except Exception:
                p = 0
            is_wall = 0 if str(rid).startswith("-") else 1
            candidates.append((s, is_wall, p, str(rid)))
    candidates.sort()
    return [rid for _, _, _, rid in candidates]


# ---------------------------------------------------------------------------
# Single rival
# ---------------------------------------------------------------------------


def _format_valid_rivals(rivals: dict[str, Any]) -> str:
    """Format rivals dict into a human-readable list for error messages."""
    parts: list[str] = []
    for rid, info in sorted(rivals.items(), key=lambda x: str(x[0])):
        if not isinstance(info, dict):
            parts.append(str(rid))
            continue
        score = info.get("attackScore")
        power = info.get("power", "?")
        # Wall rivals are negative IDs with high power
        label = "wall" if str(rid).startswith("-") else "player"
        if score is not None:
            parts.append(f"{rid} ({label} power={power} score={score})")
        else:
            parts.append(f"{rid} ({label} power={power})")
    return ", ".join(parts) if parts else "(none)"


def _suggest_rival_fix(rival_id_str: str, rivals: dict[str, Any]) -> str | None:
    """Suggest a close valid rivalId when NotFound (e.g. missing minus sign)."""
    candidates: list[str] = []
    # Missing minus sign?
    if not rival_id_str.startswith("-") and f"-{rival_id_str}" in rivals:
        candidates.append(f"-{rival_id_str}")
    # Wrong sign?
    if rival_id_str.startswith("-") and rival_id_str[1:] in rivals:
        candidates.append(rival_id_str[1:])
    # Also try the opposite sign with off-by-tier tolerance (compare last 4 digits)
    stripped = rival_id_str.lstrip("-")
    for suffix_len in (4, 3):
        if len(stripped) < suffix_len:
            continue
        suffix = stripped[-suffix_len:]
        for rid in rivals:
            rid_str = str(rid)
            if rid_str == rival_id_str or rid_str in candidates:
                continue
            if rid_str.lstrip("-").endswith(suffix):
                candidates.append(rid_str)
                if len(candidates) >= 3:
                    break
        if candidates:
            break
    if candidates:
        return f"Did you mean {', '.join(candidates)}? Check `hw-genie toe status -a <account>` for valid IDs."
    return None


def run_titan_arena(
    client_or_headers,
    rival_id: str | int | None = None,
    titans: list[int] | None = None,
    *,
    engine: BattleEngine | None = None,
    dry_run: bool = False,
    estimate_only: bool = False,
    end_on_loss: bool = True,
    attempt_label: str | None = None,
    account_label: str | None = None,
    progress: str = "verbose",
    dashboard: Any = None,
) -> dict[str, Any]:
    """Start a single-rival battle, simulate via ``engine``, and EndBattle.

    ``engine`` defaults to the Python estimator. Provide a
    :class:`JsBridgeBattleEngine` for server-valid progress/result.

    When ``rival_id`` is ``None``, the best un-cleared rival is picked
    automatically via ``titanArenaGetStatus`` and
    :func:`_select_auto_rivals` (``attackScore < threshold``). When
    ``titans`` is ``None``, the saved team is fetched via ``teamGetAll``.

    With ``end_on_loss=False``, a losing simulation abandons the battle
    without calling EndBattle (no score banking, but faster tier sweeps).

    ``progress`` selects output compactness: ``verbose`` (today's full
    output), ``line`` (per-attempt details suppressed; the tier loop owns
    the updating status line), ``quiet`` (only rival-decided + errors).
    ``dashboard`` is accepted for signature symmetry with the tier loop
    and currently unused here.
    """
    progress = _normalize_progress(progress)
    if isinstance(client_or_headers, dict):
        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    engine = engine or PythonBattleEngine()
    # Validate titans first so invalid titans raise ValueError before any fetch
    # (spec Error Handling: fetchは行わない). For titans=None this calls
    # teamGetAll, which per spec Data Flow 1 is before fetch for toe attack.
    titans = _resolve_titans(client, titans)
    if rival_id is None:
        auto_status = fetch_titan_arena_status(client)
        auto = _select_auto_rivals(auto_status)
        if not auto:
            print(f"{Emojis.INFO}No rivals to attack (threshold={AUTO_RIVAL_SCORE_THRESHOLD})", flush=True)
            return {"status": ResponseStatus.SKIPPED, "reason": "no_rivals"}
        rival_id_str = auto[0]
        rivals = auto_status.get("rivals") or {}
        info: dict[str, Any] = {}
        if isinstance(rivals, dict):
            cand = rivals.get(rival_id_str)
            if isinstance(cand, dict):
                info = cand
        score = info.get("attackScore", "?")
        power = info.get("power", "?")
        is_wall = str(rival_id_str).startswith("-")
        print(
            f"{Emojis.INFO}Auto-selected rival {rival_id_str} (score={score} wall={is_wall} power={power})  titans={titans}",
            flush=True,
        )
    else:
        rival_id_str = str(rival_id)

    attempt_prefix = f"[{attempt_label}] " if attempt_label else ""
    account_prefix = f"[{account_label}] " if account_label else ""
    _vprint(progress, f"\n{Emojis.STEP}Titan Arena: {account_prefix}{attempt_prefix}rivalId={rival_id_str} titans={titans}")
    if dry_run:
        _vprint(progress, f"{Emojis.INFO}Dry-run: verifying titanArenaStartBattle is accepted with arbitrary titans...")

    start_payload = {
        "calls": [
            {
                "name": ApiAction.TITAN_ARENA_START_BATTLE,
                "args": {"rivalId": rival_id_str, "titans": titans},
                "ident": "body",
            }
        ]
    }
    start_res = client.call(start_payload)
    if not start_res.is_success:
        beaten_already = _is_beaten_up_detail(
            start_res.error_name, start_res.detail
        )
        if beaten_already:
            # Expected stale snapshot: the caller (_run_rivals) reports it as
            # a win after confirming the current status. Do not print it as an
            # error; a busy tier run would otherwise look like it is failing.
            _vprint(
                progress,
                f"{Emojis.INFO}Rival {rival_id_str} already cleared; skipping StartBattle.",
            )
        else:
            print(f"{Emojis.ERROR}Start failed ({start_res.error_name}): {start_res.detail}", flush=True)
        if not beaten_already and start_res.error_name == "NotFound":
            try:
                status = fetch_titan_arena_status(client)
                rivals = status.get("rivals") or {}
                if isinstance(rivals, dict) and rivals:
                    print(f"{Emojis.INFO}Valid rivals (threshold={AUTO_RIVAL_SCORE_THRESHOLD} count={len(rivals)}): {_format_valid_rivals(rivals)}", flush=True)
                    suggestion = _suggest_rival_fix(rival_id_str, rivals)
                    if suggestion:
                        print(f"{Emojis.INFO}{suggestion}", flush=True)
                    else:
                        print(
                            f"{Emojis.INFO}Tip: run `hw-genie toe status -a <account>` to see current valid rival IDs. Wall IDs change per tier.",
                            flush=True,
                        )
                else:
                    print(f"{Emojis.INFO}No rivals available (status={status.get('status')} threshold={AUTO_RIVAL_SCORE_THRESHOLD}). Try `hw-genie toe status`.", flush=True)
            except Exception as exc:  # pragma: no cover - best-effort
                print(f"{Emojis.WARNING}Could not fetch valid rivals: {exc}", flush=True)
        return {"status": start_res.status, "error": start_res.error_name, "detail": start_res.detail}

    battle: dict[str, Any] = {}
    if isinstance(start_res.detail, dict):
        battle = start_res.detail.get("response", {}).get("battle", {})
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "StartBattle battle: %s",
            json.dumps(battle, ensure_ascii=False, default=str)[:8000],
        )
    _vprint(progress, f"{Emojis.SUCCESS}StartBattle accepted (type={battle.get('type')} seed={battle.get('seed')})")
    if dry_run:
        _vprint(progress, f"{Emojis.INFO}Dry-run: not calling titanArenaEndBattle.")
        return {"status": ResponseStatus.SUCCESS, "battle": battle, "dry_run": True}

    try:
        est = _engine_calc(engine, battle, progress)
    except (KeyboardInterrupt, InterruptedError):
        raise
    except (BridgeError, Exception) as exc:
        if isinstance(engine, PythonBattleEngine):
            raise
        print(f"{Emojis.WARNING}Engine {type(engine).__name__} failed: {exc}", flush=True)
        if type(engine).__name__ == "PlaywrightBattleEngine":
            print(
                f"{Emojis.INFO}Hint: install Playwright (`pip install playwright && playwright install chromium`) and ensure headers are valid. Falling back to estimate-only.",
                flush=True,
            )
        else:
            _warn_userscript_hint_once()
        # Return estimate-only style so the caller can decide; do not call EndBattle with invalid progress.
        # bridge_timeout is the structured signal for _is_bridge_timeout (the
        # message-substring check there is only a legacy fallback).
        fallback = PythonBattleEngine().calc(battle)
        return {
            "status": ResponseStatus.SUCCESS,
            "battle": battle,
            "estimate": fallback,
            "estimate_only": True,
            "bridge_error": str(exc),
            "bridge_timeout": isinstance(exc, BridgeTimeoutError),
        }
    _vprint(progress, f"{Emojis.STEP}Engine: win={est.win} stars={est.stars} ({type(engine).__name__})")
    if estimate_only:
        _vprint(progress, f"{Emojis.INFO}Estimate-only: not calling titanArenaEndBattle.")
        _reset_invalid_battle(account_label)
        return {"status": ResponseStatus.SUCCESS, "battle": battle, "estimate": est, "estimate_only": True}

    if not est.win and not end_on_loss:
        _vprint(progress, f"{Emojis.INFO}Loss — abandoning battle without EndBattle (no score banking).")
        _reset_invalid_battle(account_label)
        return {"status": ResponseStatus.SUCCESS, "battle": battle, "estimate": est, "abandoned": True}

    outcome = _end_battle(client, rival_id_str, est, progress)
    _track_invalid_battle(outcome, engine, account_label)
    return outcome


def _end_battle(client: HWClient, rival_id: str, est: BattleEstimate, progress: str = "verbose") -> dict[str, Any]:
    progress = _normalize_progress(progress)
    _vprint(progress, f"{Emojis.STEP}Calling titanArenaEndBattle...")
    end_payload = {
        "calls": [
            {
                "name": ApiAction.TITAN_ARENA_END_BATTLE,
                "args": {
                    "rivalId": rival_id,
                    "result": {"win": est.win, "stars": est.stars},
                    "progress": est.progress,
                },
                "ident": "body",
            }
        ]
    }
    end_res = client.call(end_payload)
    detail = end_res.detail if isinstance(end_res.detail, dict) else {}
    response = detail.get("response", detail) if isinstance(detail, dict) else detail
    if isinstance(response, dict) and response.get("error") == "Invalid battle":
        print(
            f"{Emojis.WARNING}EndBattle: Invalid battle (serverVersion {response.get('serverVersion')}). "
            "Server re-simulates progress/result. Use a JS bridge engine for exact progress.",
            flush=True,
        )
        return {"status": ResponseStatus.SUCCESS, "estimate": est, "end_error": "Invalid battle"}
    if not end_res.is_success:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("EndBattle full response: %s", _shorten_response(response, 100000))
        # A failed EndBattle banked nothing: never let the local estimate turn
        # it into a win. NotFound/NotAvailable are often transient (the server
        # already closed the battle, or a retry raced the first response), so
        # surface them as a warning rather than an account failure.
        emoji = (
            Emojis.WARNING
            if end_res.error_name in ("NotFound", "NotAvailable")
            else Emojis.ERROR
        )
        print(
            f"{emoji}EndBattle failed ({end_res.error_name}): "
            f"{_shorten_response(response)}",
            flush=True,
        )
        return {
            "status": end_res.status,
            "estimate": est,
            "end_error": end_res.error_name or "EndBattle failed",
            "end_detail": response,
        }
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("EndBattle full response: %s", _shorten_response(response, 100000))
    end_emoji = Emojis.VICTORY if est.win else Emojis.INFO
    _aprint(progress, f"{end_emoji}EndBattle: {_summarize_end_battle(rival_id, est, response)}")
    return {"status": ResponseStatus.SUCCESS, "estimate": est, "end_detail": response}


# ---------------------------------------------------------------------------
# Full tier (raid + rivals + complete)
# ---------------------------------------------------------------------------


def run_titan_arena_tier(
    client_or_headers,
    titans: list[int] | None = None,
    *,
    engine: BattleEngine | None = None,
    attack_score_threshold: int = AUTO_RIVAL_SCORE_THRESHOLD,
    stop_on_first_loss: bool = False,
    end_on_loss: bool = True,
    seeds_per_team: int = 2,
    bank_best_loss: bool = True,
    max_total_attempts: int | None = None,
    account_label: str | None = None,
    progress: str = "verbose",
    dashboard: Any = None,
) -> dict[str, Any]:
    """Run a ToE tier end-to-end.

    Mirrors ``HerowarsHelper.executeTitanArena``:

    1. ``titanArenaGetStatus`` to read ``status``/``canRaid``/``tier``/``rivals``.
    2. ``canRaid`` → ``titanArenaStartRaid`` (one per remaining un-touched rival),
       each via the engine, then ``titanArenaEndRaid`` to submit results.
       The raid runs first; if it yields no verifiable result, that tier falls
       back to individual battles and skips further raid attempts for that
       tier only — a new tier retries the raid.
    3. otherwise (or after a fallback) loop ``rivals`` whose
       ``attackScore < threshold`` and finish each via the engine. After a
       pass with wins, attempt ``titanArenaCompleteTier`` once.
    4. Best-effort ``titanArenaFarmDailyReward`` for the daily chest after
       each pass; early returns (disabled/peace_time, no targets, no
       progress, bridge dead) skip the remaining steps and report via
       ``summary["errors"]``.

    ``stop_on_first_loss`` aborts the per-rival loop after the first losing
    (or abandoned) attempt — including rotation retries — so a weak rotation
    stops fast. Unverified attempts (bridge failure, nothing banked) never
    trigger the abort since they carry no signal about team strength.
    This is orthogonal to the per-battle abandon policy: the
    tier loop calls :func:`run_titan_arena` with ``end_on_loss``
    (default ``True``), i.e. losing sims also send ``EndBattle`` so partial
    attackScore is banked on every attempt; pass ``end_on_loss=False``
    (CLI ``--no-end-on-loss``) to skip ``EndBattle`` on losses and advance
    to the next team/seed instead (faster sweeps, no score banking).
    When the plan is exhausted with no win, ``bank_best_loss`` (default
    ``True``) re-runs the highest-stars losing team once with
    ``end_on_loss=True`` so partial attackScore is banked; the estimate
    engine and unverified-only rivals skip this (their EndBattle would be
    rejected or empty). ``--stop-on-loss`` also skips it (fast abort).

    ``max_total_attempts`` caps total StartBattle attempts per rival
    (``None`` = full pass: ``max_attempts_per_rival`` for a single team,
    otherwise ``len(rotation) × seeds_per_team``). Estimate-engine runs,
    whose EndBattle is rejected as ``Invalid battle``, should pass an
    explicit cap to avoid paying the full sweep per rival. The banking
    attempt is one extra StartBattle outside this cap (the cap targets
    estimate planning). A pass with no wins but banked-loss entries counts
    as progress: completion is attempted and the loop continues with fresh
    status instead of stopping early.

    The function returns a dict summarising the tier outcome (rival results,
    raid results, errors, ``raid_errors`` for API/payload failures,
    completed_tier, daily_reward, and ``raid_fallback`` when the raid was
    suppressed for the latest tier). ``raid_fallback`` is cleared whenever the
    tier changes (and set again only if the new tier's raid is suppressed).
    Tier changes are compared by a numeric key (``8`` / ``"8"`` / ``8.0``) so
    an API type flip does not lift the suppression.

    ``progress`` selects output compactness (see :func:`run_titan_arena`).
    ``dashboard`` is the shared multi-account status display (see
    ``hw_genie.runner.ToeProgressDashboard``); when ``None`` and
    ``progress="line"``, a local throttled line is used.
    """
    progress = _normalize_progress(progress)
    if isinstance(client_or_headers, dict):
        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    engine = engine or PythonBattleEngine()
    rotation = _resolve_team_rotation(client, titans)
    titans = rotation[0]
    if account_label:
        print(f"{Emojis.START}Titan Arena tier run — account: {account_label}", flush=True)
    summary: dict[str, Any] = {
        "titans": titans,
        "rotation": rotation,
        "engine": type(engine).__name__,
        "rival_results": [],
        "raid_results": [],
        "errors": [],
        "raid_errors": [],
        "completed_tier": False,
        "completed_tier_key": None,
        "daily_reward": None,
        "final_tier": None,
        "remaining_rivals": None,
        "raid_fallback": None,
        "interrupted": False,
    }

    def _at_max(status_snapshot: dict[str, Any], tier_value: Any) -> bool:
        """True when ``tier_value`` is the account's top tier (no next tier)."""
        max_tier = status_snapshot.get("maxTier")
        return max_tier is not None and _tier_key(tier_value) == _tier_key(max_tier)

    def _mark_interrupted(
        summary: dict[str, Any], partial: list | None = None
    ) -> dict[str, Any]:
        summary["interrupted"] = True
        if partial:
            summary["rival_results"].extend(partial)
        summary["errors"].append(
            {"stage": "interrupted", "message": "interrupted by user"}
        )
        print(f"{Emojis.WARNING}Interrupted by user; stopping tier loop.", flush=True)
        return summary

    def _refresh_final_remaining(
        summary: dict[str, Any],
        tier_value: Any,
        rival_results: list[dict[str, Any]] | None = None,
    ) -> None:
        """Refresh the final ``Left`` count after a winning pass.

        ``remaining_rivals`` is set from the status fetched at the top of the
        pass, before any battles. At the max tier the loop returns right
        after ``CompleteTier``, so without this refresh the summary can show
        rivals that this pass just cleared (e.g. ``Left 3`` when everything
        is actually done). Best-effort: a failed fetch falls back to
        subtracting this pass's wins from the last known count.
        """
        fresh = None
        try:
            fresh = fetch_titan_arena_status(client)
        except Exception as exc:  # best-effort: keep the last known count
            logger.debug("Final status fetch failed: %s", exc)
        if isinstance(fresh, dict):
            status_val = fresh.get("status")
            if status_val == "battle":
                summary["final_tier"] = fresh.get("tier", tier_value)
                summary["remaining_rivals"] = len(
                    _select_auto_rivals(fresh, attack_score_threshold)
                )
                return
            if status_val == "peace_time":
                summary["final_tier"] = fresh.get("tier", tier_value)
                summary["remaining_rivals"] = 0
                return
        if rival_results and summary.get("remaining_rivals") is not None:
            cleared = sum(1 for r in rival_results if r.get("win"))
            summary["remaining_rivals"] = max(
                0, summary["remaining_rivals"] - cleared
            )

    # Safety cap: each winning pass strictly shrinks the remaining target
    # set (cleared rivals hit 250), so the loop terminates naturally. The cap
    # only guards against pathological server states.
    max_passes = 30
    passes = 0
    no_progress_passes = 0
    # Set when a raid attempt produced nothing the server could verify. Retrying
    # within the same tier would just replay the same broken path, so the
    # remaining rivals are fought individually; the suppression is scoped to
    # ``raid_disabled_tier`` so a new tier retries the raid.
    raid_disabled_reason: str | None = None
    raid_disabled_tier: Any = _NO_TIER
    while True:
        if _cancel_requested():
            return _mark_interrupted(summary)
        passes += 1
        if passes > max_passes:
            summary["errors"].append({"stage": "tier_loop", "message": "max passes reached"})
            print(f"{Emojis.WARNING}Tier loop cap reached; stopping.", flush=True)
            return summary
        status = fetch_titan_arena_status(client)
        # Track the latest tier / remaining targets for the final summary.
        summary["final_tier"] = status.get("tier")
        summary["remaining_rivals"] = len(_select_auto_rivals(status, attack_score_threshold))
        state = status.get("status")
        if state in (None, "disabled"):
            print(f"{Emojis.WARNING}ToE is disabled. aborting.", flush=True)
            summary["errors"].append({"stage": "status", "message": f"status={state!r}"})
            return summary
        if state == "peace_time":
            print(f"{Emojis.INFO}ToE is in peace_time (threshold={attack_score_threshold}); nothing to do.", flush=True)
            return summary

        tier = status.get("tier")
        if (
            raid_disabled_tier is not _NO_TIER
            and _tier_key(tier) != _tier_key(raid_disabled_tier)
        ):
            # New tier: the previous tier's broken raid says nothing about this
            # one, so allow another raid attempt. Compare as strings because
            # the API has reported ``tier`` as both ``8`` and ``"8"``; a type
            # flip inside one tier must not lift the suppression. The sentinel
            # keeps this working when the suppressing tier was missing.
            raid_disabled_reason = None
            raid_disabled_tier = _NO_TIER
            summary["raid_fallback"] = None
        print(f"{Emojis.STEP}Tier: {tier} (canRaid={status.get('canRaid')})", flush=True)

        tier_completed = (
            summary.get("completed_tier_key") is not None
            and summary.get("completed_tier_key") == _tier_key(tier)
        )
        raid_won = False
        if (
            status.get("canRaid")
            and raid_disabled_tier is _NO_TIER
            and not tier_completed
        ):
            try:
                raid_summary = _run_raid(client, status, titans, engine, attack_score_threshold, progress, dashboard)
            except BridgeDeadError as exc:
                summary["raid_results"].append({"stage": "raid", "battles": [], "completed": False, "bridge_dead": True})
                summary["rival_results"].extend(exc.partial_results)
                summary["errors"].append({"stage": "raid", "message": str(exc)})
                print(f"{Emojis.WARNING}Bridge dead during raid ({exc}); stopping tier loop.", flush=True)
                return summary
            except (KeyboardInterrupt, InterruptedError) as exc:
                partial = getattr(exc, "partial_results", None)
                return _mark_interrupted(summary, partial if isinstance(partial, list) else None)
            summary["raid_results"].append(raid_summary)
            if raid_summary.get("bridge_dead"):
                summary["errors"].append({"stage": "raid", "message": raid_summary.get("error", "bridge dead")})
                print(f"{Emojis.WARNING}Bridge dead during raid; stopping tier loop.", flush=True)
                return summary
            if raid_summary.get("api_error") or raid_summary.get("payload_error"):
                # A broken raid must stay visible even though the run continues
                # with individual battles. Keep it out of ``errors`` so a
                # working fallback does not fail the account, but surface a
                # WARNING and keep a machine-readable record.
                kind = "API" if raid_summary.get("api_error") else "payload"
                summary["raid_errors"].append(
                    {
                        "stage": "raid",
                        "tier": tier,
                        "message": raid_summary.get("error", f"raid {kind} error"),
                    }
                )
                _aprint(
                    progress,
                    f"{Emojis.WARNING}Raid {kind} error for tier {tier}: "
                    f"{raid_summary.get('error', 'unknown')}; "
                    "continuing with individual battles.",
                )
            if raid_summary.get("no_verified"):
                raid_disabled_reason = str(
                    raid_summary.get("error") or "no verified raid results"
                )
                raid_disabled_tier = tier
                # Expected fallback, not a failure: keep it out of
                # summary["errors"] so summarize_toe does not fail the account.
                summary["raid_fallback"] = raid_disabled_reason
                if not (
                    raid_summary.get("api_error") or raid_summary.get("payload_error")
                ):
                    # Avoid a second near-identical line after the WARNING above.
                    print(
                        f"{Emojis.INFO}Raid unavailable for tier {tier} ({raid_disabled_reason}); "
                        "continuing with individual battles.",
                        flush=True,
                    )
            else:
                # The latest tier's raid was not suppressed, so a reason from
                # an earlier tier is stale. ``summary`` describes the run.
                summary["raid_fallback"] = None
            if not raid_summary.get("completed", False):
                # If raid didn't clear the threshold we still try the per-rival
                # path in case the user reconnected mid-tier. Banked raid wins
                # count as progress for the no-progress counter below.
                raid_won = any(b.get("win") for b in raid_summary.get("battles", []) or [])
                if raid_won:
                    # The pre-raid snapshot still lists the rivals the raid
                    # just cleared. Refresh once so the fallback does not
                    # StartBattle every already-beaten rival (the server
                    # answers "beaten up already" and each one costs a
                    # wasted call plus a confirmation fetch).
                    fresh = None
                    try:
                        fresh = fetch_titan_arena_status(client)
                    except Exception as exc:  # best-effort: stale snapshot stands
                        logger.debug("Post-raid status fetch failed: %s", exc)
                    if isinstance(fresh, dict):
                        if fresh.get("status") != "battle":
                            # The tier ended (peace_time/disabled); the next
                            # pass handles it instead of attacking stale IDs.
                            continue
                        fresh_tier = fresh.get("tier", tier)
                        if _tier_key(fresh_tier) != _tier_key(tier):
                            # A concurrent clear advanced the tier; process
                            # the new tier on the next pass.
                            continue
                        status = fresh
                        summary["final_tier"] = fresh_tier
                        summary["remaining_rivals"] = len(
                            _select_auto_rivals(fresh, attack_score_threshold)
                        )
            else:
                if _cancel_requested():
                    return _mark_interrupted(summary)
                completed = _complete_tier(client, summary, tier=tier)
                # Best-effort daily chest, mirroring the per-rival path: a
                # raid-only final-tier clear must not skip the reward.
                _farm_daily_reward(client, summary)
                if completed:
                    if _at_max(status, tier):
                        if raid_summary.get("completed_basis") == "status":
                            # The authoritative status showed no remaining
                            # rivals; there is no next tier to advance to.
                            summary["final_tier"] = raid_summary.get(
                                "final_tier", tier
                            )
                            summary["remaining_rivals"] = raid_summary.get(
                                "remaining_rivals", 0
                            )
                            return summary
                        # Non-authoritative EndRaid echo: let the next pass
                        # verify the fresh status instead of
                        # stopping on a subset of the status rivals. The
                        # completed_tier_key guard prevents a re-raid.
                        continue
                    no_progress_passes = 0
                    continue
                # CompleteTier failed despite a raid that claimed completion.
                # Suppress the raid for this tier and fall through to the
                # individual path instead of replaying StartRaid/EndRaid.
                raid_disabled_tier = tier
                raid_disabled_reason = "CompleteTier failed after raid"
                summary["raid_fallback"] = raid_disabled_reason

        try:
            rival_results = _run_rivals(
                client, status, titans, engine, attack_score_threshold, stop_on_first_loss,
                team_rotation=rotation, seeds_per_team=seeds_per_team, end_on_loss=end_on_loss,
                bank_best_loss=bank_best_loss,
                max_total_attempts=max_total_attempts,
                account_label=account_label,
                progress=progress,
                dashboard=dashboard,
                tier=tier,
            )
        except BridgeDeadError as exc:
            summary["rival_results"].extend(exc.partial_results)
            summary["errors"].append({"stage": "rivals", "message": str(exc)})
            print(f"{Emojis.WARNING}Bridge dead ({exc}); stopping tier loop.", flush=True)
            return summary
        except (KeyboardInterrupt, InterruptedError) as exc:
            partial = getattr(exc, "partial_results", None)
            return _mark_interrupted(summary, partial if isinstance(partial, list) else None)
        # A bridge-dead sentinel is never mixed into rival_results anymore
        # (it raises); an empty list here genuinely means "no targets".
        summary["rival_results"].extend(rival_results)
        if _cancel_requested():
            return _mark_interrupted(summary)
        if not rival_results:
            # The snapshot may predate a partial raid (or a concurrent clear).
            # Re-check before concluding there is nothing to do: all rivals
            # can already be at/above the threshold while ``completed`` stayed
            # False because EndRaid omitted/partially echoed ``results``.
            fresh = None
            try:
                fresh = fetch_titan_arena_status(client)
            except Exception as exc:  # best-effort: the stale snapshot stands
                logger.debug("Post-pass status fetch failed: %s", exc)
            if isinstance(fresh, dict) and fresh.get("status") == "battle":
                fresh_tier = fresh.get("tier", tier)
                summary["final_tier"] = fresh_tier
                summary["remaining_rivals"] = len(
                    _select_auto_rivals(fresh, attack_score_threshold)
                )
                if not summary["remaining_rivals"]:
                    same_tier = (
                        summary.get("completed_tier_key") is not None
                        and summary.get("completed_tier_key") == _tier_key(fresh_tier)
                    )
                    if same_tier:
                        # This tier was already completed successfully in an
                        # earlier pass; do not send CompleteTier/Farm twice.
                        return summary
                    completed = _complete_tier(
                        client, summary, tier=fresh_tier
                    )
                    _farm_daily_reward(client, summary)
                    if not completed or _at_max(fresh, fresh_tier):
                        # A failed CompleteTier (or the top tier) has no next
                        # state to advance to; returning avoids a re-complete
                        # loop on every pass.
                        return summary
                    no_progress_passes = 0
                    continue
                # The snapshot was stale: targets exist after all. Let the
                # next pass attack them, with a bounded no-progress guard.
                no_progress_passes += 1
                if no_progress_passes >= MAX_CONSECUTIVE_NO_PROGRESS_PASSES:
                    print(
                        f"{Emojis.INFO}Status kept changing without targets cleared "
                        f"in {no_progress_passes} passes; stopping tier loop.",
                        flush=True,
                    )
                    return summary
                continue
            print(f"{Emojis.INFO}No rivals to attack (threshold={attack_score_threshold})", flush=True)
            _farm_daily_reward(client, summary)
            return summary
        if not any(r.get("win") for r in rival_results) and not status.get("canRaid"):
            if any(
                r.get("banked_loss") and not r.get("unverified")
                for r in rival_results
            ):
                # Banked partial score counts as progress: the rival is
                # touched, so attempt completion and continue with fresh
                # status (bounded by max_passes; a later pass with no wins
                # and no banked entries still stops).
                completed = _complete_tier(client, summary, tier=tier)
                _farm_daily_reward(client, summary)
                if completed and _at_max(status, tier):
                    _refresh_final_remaining(summary, tier, rival_results)
                    return summary
                no_progress_passes = 0
                continue
            print(f"{Emojis.WARNING}No rival cleared; stopping tier loop.", flush=True)
            return summary
        completed = _complete_tier(client, summary, tier=tier)
        # Best-effort daily chest; the return value no longer ends the run so
        # remaining rivals (or the next tier) are attacked in the next pass.
        _farm_daily_reward(client, summary)
        if completed and _at_max(status, tier):
            _refresh_final_remaining(summary, tier, rival_results)
            return summary
        if any(r.get("win") for r in rival_results) or raid_won:
            no_progress_passes = 0
        else:
            no_progress_passes += 1
            if no_progress_passes >= MAX_CONSECUTIVE_NO_PROGRESS_PASSES:
                print(
                    f"{Emojis.INFO}No new clears in {no_progress_passes} consecutive passes; stopping tier loop.",
                    flush=True,
                )
                return summary
        # Loop again with fresh status until nothing is left to attack or no
        # progress is made.
        continue


def _has_any_heroes(progress: Any) -> bool:
    """True when at least one progress round carries hero entries.

    The userscript submits an all-empty progress
    (``[{attackers: {heroes: {}}, defenders: {heroes: {}}}]``) when its
    in-page calc fails. Such dummy losses must be excluded from EndRaid:
    if the server validates raid results like EndBattle, one dummy could
    void the whole raid including genuine wins.
    """
    if not isinstance(progress, list):
        return False
    for round_ in progress:
        if not isinstance(round_, dict):
            continue
        for side in ("attackers", "defenders"):
            heroes = round_.get(side, {})
            if isinstance(heroes, dict) and heroes.get("heroes"):
                return True
    return False


_TITAN_TEAM_CONTAINER_KEYS = ("titans", "heroes", "defenders", "team")


def _is_titan_team(value: Any) -> bool:
    """True when ``value`` looks like a real ``{titanId: heroDict}`` roster.

    Every key must be numeric and every value a dict. When a hero carries an
    ``id`` it must match its key, but ``id`` itself is optional: a live roster
    may omit it. Each hero must still expose at least one battle-stat key
    (``id`` / ``level`` / ``power`` / ``hp`` / ``type``) so numeric-keyed
    non-rosters such as ``{"1": {"attackScore": 0}}`` are rejected.
    """
    if not isinstance(value, dict) or not value:
        return False
    plausibility_keys = {"id", "level", "power", "hp", "type"}
    for key, hero in value.items():
        if not str(key).lstrip("-").isdigit() or not isinstance(hero, dict):
            return False
        hero_id = hero.get("id")
        if hero_id is not None and str(hero_id) != str(key):
            return False
        if not plausibility_keys & set(hero):
            return False
    return True


def _strip_hero_state(team: dict[str, Any]) -> dict[str, Any]:
    """Copy a titan team, dropping each hero's ``state`` block.

    ``titanArenaGetStatus`` decorates roster entries with ``state`` (current
    hp / energy / isDead). A real ``titanArenaStartRaid`` battle as recorded
    by ``battleGetReplay`` carries the roster *without* ``state``, and stale
    state would leak earlier fights into the simulation.
    """
    return {
        str(titan_id): {k: v for k, v in hero.items() if k != "state"}
        for titan_id, hero in team.items()
    }


def _extract_titan_team(value: Any) -> dict[str, Any] | None:
    """Normalize a payload fragment into ``{titanId: hero}``.

    Accepts the plain shape and the wrappers the Titan Arena API uses
    elsewhere (``{titans: {...}}`` / ``{heroes: {...}}`` /
    ``{defenders: [{...}]}``, plus a list of hero dicts). Returns ``None``
    when no team is found so callers can fall back to another source
    instead of simulating a malformed battle.
    """
    if isinstance(value, list):
        if value and all(isinstance(item, dict) and item.get("id") is not None for item in value):
            team = {str(item["id"]): dict(item) for item in value}
            return _strip_hero_state(team)
        # Only a single-element list wrapping one team (``defenders: [team]``)
        # is unwrapped; a mixed/multi-element list must not yield its first
        # team, which would simulate a different battle than the server.
        if len(value) == 1 and isinstance(value[0], dict):
            return _extract_titan_team(value[0])
        return None
    if not isinstance(value, dict):
        return None
    for key in _TITAN_TEAM_CONTAINER_KEYS:
        if key in value:
            team = _extract_titan_team(value.get(key))
            if team is not None:
                return team
    if _is_titan_team(value):
        return _strip_hero_state(value)
    return None


def _normalize_raid_seed(value: Any) -> int | None:
    """Return a usable raid seed as an int, or ``None``.

    A live ``titanArenaStartBattle`` battle carries ``seed`` as a JSON
    *number* (e.g. ``2245015540``); ``battleGetReplay`` re-serializes it as
    a string, which is why both forms are accepted. Seeds are 32-bit:
    ``titanArenaGetStatus.rivals[].seed`` may be reported unsigned
    (``> 2**31``) while ``progress[].seed`` shows the signed view.

    Unusable seeds are treated as missing (``None``) so callers fall back to
    ``titanArenaGetStatus``: booleans, non-integers, ``0`` (the StartRaid
    stub behind the original bug), and values outside
    ``[-2**31, 0xFFFFFFFF]``.
    """
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n == 0 or n < -(2**31) or n > 0xFFFFFFFF:
        return None
    return n


def _raid_start_time(*sources: Any) -> int | None:
    """Return a usable raid ``startTime`` epoch from any source.

    A live ``titanArenaStartBattle`` battle carries ``startTime`` as the
    client-clock epoch *number* at request time; ``battleGetReplay``
    re-serializes it as a string. ``titanArenaStartRaid`` omits the field
    entirely, so the caller fills in the current epoch (mirroring what the
    game client does for StartBattle).
    """
    for source in sources:
        if not isinstance(source, dict):
            continue
        raw = source.get("startTime")
        if raw is None or isinstance(raw, bool):
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        return n
    return None


def _build_raid_battle(
    rival_id: str,
    attackers: dict[str, Any],
    defenders: dict[str, Any],
    *,
    seed: int,
    start_time: int,
    user_id: Any,
) -> dict[str, Any]:
    """Build the battle object the in-page engine expects for one rival.

    The shape mirrors a live ``titanArenaStartBattle`` battle (captured via
    ``toe attack --debug``): ``seed`` / ``startTime`` are numbers,
    ``defenders`` is a single-element list of team dicts, and the per-rival
    seed is the rival's own ``seed`` from ``titanArenaStartRaid`` /
    ``titanArenaGetStatus``. Raise-style payloads (``seed: 0``, guessed
    defenders) make the in-page calc fail, which the userscript reports as
    a dummy loss.
    """
    return {
        "userId": str(user_id or ""),
        "typeId": str(rival_id),
        "attackers": attackers,
        "defenders": [defenders],
        "effects": [],
        "reward": [],
        "startTime": start_time,
        "seed": seed,
        "type": "titan_arena",
    }


def _run_raid(
    client: HWClient,
    status: dict[str, Any],
    titans: list[int],
    engine: BattleEngine,
    threshold: int,
    progress: str = "verbose",
    dashboard: Any = None,
) -> dict[str, Any]:
    """Simulate every raid rival and submit only verified results.

    Completion is judged from the EndRaid echo when it covers every rival; a
    missing/partial echo falls back to a fresh ``titanArenaGetStatus`` (the
    authoritative remaining-rival view). If neither is usable, completion is
    left unverified (``completed_basis="unverified"``, ``no_verified=True``) so
    the caller retries with individual battles instead of guessing a
    CompleteTier. API failures set ``api_error`` and payload mismatches
    ``payload_error`` so the caller can surface them without treating the
    fallback as an account failure.
    """
    progress = _normalize_progress(progress)
    rivals = (status.get("rivals") or {}) if isinstance(status.get("rivals"), dict) else {}
    raid_summary: dict[str, Any] = {
        "stage": "raid",
        "battles": [],
        "completed": False,
        "completed_basis": None,
        "skipped": 0,
        "no_verified": False,
        "api_error": False,
        "payload_error": False,
    }
    _vprint(progress, f"{Emojis.STEP}Raid phase: {len(rivals)} rival(s)")
    start = client.call(
        {"calls": [{"name": ApiAction.TITAN_ARENA_START_RAID, "args": {"titans": titans}, "ident": "body"}]}
    )
    if not start.is_success:
        raid_summary["error"] = f"StartRaid failed: {start.error_name}"
        raid_summary["api_error"] = True
        raid_summary["no_verified"] = True
        print(f"{Emojis.ERROR}{raid_summary['error']}", flush=True)
        return raid_summary
    detail = start.detail if isinstance(start.detail, dict) else {}
    response = detail.get("response", detail) if isinstance(detail, dict) else {}
    if not isinstance(response, dict):
        response = {}
    # Keep the raw payload reachable via ``--debug``: the successful shape is
    # not documented and StartRaid carries no ``startTime``, unlike a
    # StartBattle battle or a battle replay.
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "StartRaid response: %s",
            json.dumps(response, ensure_ascii=False, default=str)[:4000],
        )
    raid_rivals = response.get("rivals")
    attackers = _extract_titan_team(response.get("attackers"))
    if attackers is None:
        # Some payloads wrap the roster under a different key.
        attackers = _extract_titan_team(response.get("team"))
    if attackers is None:
        # Last resort: our own defense team; validated against the requested
        # lineup below before it is ever simulated.
        attackers = _extract_titan_team(status.get("defenders"))
    expected_attackers = {str(t) for t in titans}
    if attackers is not None and set(attackers) != expected_attackers:
        # A truncated/different roster cannot reproduce the raid battle the
        # server started, so never simulate it or report it to EndRaid.
        raid_summary["error"] = (
            "StartRaid attackers do not match the requested titans "
            f"({sorted(attackers)} != {sorted(expected_attackers)})"
        )
        raid_summary["no_verified"] = True
        raid_summary["payload_error"] = True
        print(f"{Emojis.ERROR}{raid_summary['error']}", flush=True)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "StartRaid payload: %s",
                json.dumps(response, ensure_ascii=False, default=str)[:2000],
            )
        return raid_summary
    if attackers is None or not isinstance(raid_rivals, dict) or not raid_rivals:
        raid_summary["error"] = "StartRaid returned unexpected payload"
        raid_summary["no_verified"] = True
        raid_summary["payload_error"] = True
        print(f"{Emojis.ERROR}{raid_summary['error']}", flush=True)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "StartRaid payload: %s",
                json.dumps(response, ensure_ascii=False, default=str)[:2000],
            )
        return raid_summary

    # A live StartBattle battle stamps ``startTime`` with the client clock;
    # StartRaid omits it, so mirror that convention. The per-rival battle
    # uses the rival's own seed, never a constant.
    start_time = _raid_start_time(response) or int(time.time())
    user_id = (
        response.get("userId")
        or status.get("userId")
        or client.headers.get("x-auth-user-id")
        or ""
    )
    results: dict[str, dict[str, Any]] = {}
    bridge_errors = 0
    for rival_id, raw_rival in raid_rivals.items():
        if _cancel_requested():
            raise _interrupted_error([])
        rid = str(rival_id)
        status_rival = rivals.get(rid) if isinstance(rivals, dict) else None
        expected_defenders = _extract_titan_team(status_rival)
        defender_team = _extract_titan_team(raw_rival)
        if defender_team is None:
            # StartRaid may echo only a stub; GetStatus has the full roster.
            defender_team = expected_defenders
        elif expected_defenders is not None and set(defender_team) != set(expected_defenders):
            # Stale/truncated echo: never simulate a roster the server did not
            # start. The per-rival path retries this rival with StartBattle.
            defender_team = None
        if defender_team is not None and len(defender_team) != len(titans):
            # Refuse a truncated roster outright, even when the raid echo and
            # GetStatus agree on the same (short) id set.
            defender_team = None
        seed = _normalize_raid_seed(
            raw_rival.get("seed") if isinstance(raw_rival, dict) else None
        )
        if seed is None and isinstance(status_rival, dict):
            seed = _normalize_raid_seed(status_rival.get("seed"))
        # ``startTime`` is not part of StartRaid's payload; it is filled from
        # the client clock above, exactly like a live StartBattle battle.
        missing = [
            name
            for name, value in (("defenders", defender_team), ("seed", seed))
            if value is None
        ]
        if missing:
            # Without the real roster + seed the in-page calc can only fail
            # (dummy loss), so keep it out of EndRaid and let the per-rival
            # path retry this rival with StartBattle.
            raid_summary["battles"].append(
                {
                    "rivalId": rid,
                    "skipped": True,
                    "reason": f"missing {'/'.join(missing)}",
                }
            )
            _vprint(progress, f"  - raid rival {rid}: skipped (missing {'/'.join(missing)})")
            continue
        battle = _build_raid_battle(
            rid,
            attackers,
            defender_team,
            seed=seed,
            start_time=start_time,
            user_id=user_id,
        )
        try:
            est = _engine_calc(engine, battle, progress)
            if not _has_any_heroes(est.progress):
                # Userscript dummy loss (in-page calc failed): unverified, so
                # keep it out of EndRaid. The per-rival path retries it later.
                # A dummy proves the bridge answered, so it resets the
                # consecutive-error count like any successful contact.
                bridge_errors = 0
                raid_summary["battles"].append({"rivalId": rid, "win": False, "dummy": True})
                _vprint(progress, f"  - raid rival {rid}: dummy loss skipped (unverified calc)")
                continue
            bridge_errors = 0
            results[rid] = {"progress": est.progress, "result": {"win": est.win, "stars": est.stars}}
            raid_summary["battles"].append({"rivalId": rid, "win": est.win})
            _vprint(progress, f"  - raid rival {rid}: win={est.win}")
        except (KeyboardInterrupt, InterruptedError):
            raise
        except (BridgeError, Exception) as exc:
            if isinstance(engine, PythonBattleEngine):
                raise
            bridge_errors += 1
            raid_summary["battles"].append({"rivalId": rid, "error": str(exc)})
            _vprint(progress, f"  - raid rival {rid}: bridge error {exc} ({bridge_errors}/{MAX_CONSECUTIVE_BRIDGE_TIMEOUTS})")
            if bridge_errors >= MAX_CONSECUTIVE_BRIDGE_TIMEOUTS:
                raise BridgeDeadError(
                    f"userscript not answering during raid ({bridge_errors} consecutive bridge errors)",
                    partial_results=[],
                ) from exc
            # Skip fake progress: without a verified engine result we must
            # not fabricate progress for EndRaid. The rival is simply left
            # out of the results so the server scores only verified battles.
            continue

    missing_skips = [b for b in raid_summary["battles"] if b.get("skipped")]
    error_skips = [
        b
        for b in raid_summary["battles"]
        if b.get("error") and not b.get("skipped")
    ]
    raid_summary["skipped"] = len(missing_skips)
    if missing_skips:
        _aprint(
            progress,
            f"{Emojis.WARNING}Raid skipped {len(missing_skips)} rival(s) "
            "(missing/invalid battle data); individual battles will retry them.",
        )
    if error_skips:
        _aprint(
            progress,
            f"{Emojis.WARNING}Raid engine/bridge errors for {len(error_skips)} "
            "rival(s); individual battles will retry them.",
        )

    if _cancel_requested():
        raise _interrupted_error([])
    if not results:
        raid_summary["error"] = "no verified raid results; EndRaid skipped"
        raid_summary["no_verified"] = True
        print(f"{Emojis.WARNING}{raid_summary['error']}", flush=True)
        return raid_summary

    end = client.call(
        {
            "calls": [
                {
                    "name": ApiAction.TITAN_ARENA_END_RAID,
                    "args": {"results": results},
                    "ident": "body",
                }
            ]
        }
    )
    if not end.is_success:
        raid_summary["error"] = f"EndRaid failed: {end.error_name}"
        # Nothing was banked, and a same-tier retry would replay the same
        # request, so the caller switches to individual battles.
        raid_summary["api_error"] = True
        raid_summary["no_verified"] = True
        print(f"{Emojis.ERROR}{raid_summary['error']}", flush=True)
        return raid_summary
    end_detail = end.detail if isinstance(end.detail, dict) else {}
    end_response = end_detail.get("response", {}) if isinstance(end_detail, dict) else {}
    end_results = end_response.get("results") if isinstance(end_response, dict) else None
    scored = (
        {str(k): v for k, v in end_results.items() if isinstance(v, dict)}
        if isinstance(end_results, dict)
        else {}
    )
    # The live EndRaid echo shape is not verified yet (no canRaid tier has been
    # available since the raid path shipped). Trust a complete echo, but when
    # it is missing/partial ask GetStatus for the authoritative remaining
    # rivals: a skipped rival may already be at/above the threshold, and a
    # fully-cleared tier must still reach CompleteTier.
    echo_full = (
        bool(scored)
        and len(results) == len(raid_rivals)
        and set(results) <= set(scored)
        and all(_has_attack_score(scored[rid]) for rid in results)
    )
    if echo_full:
        completed = all(_attack_score(scored[rid]) >= threshold for rid in results)
        basis = "endraid"
    else:
        fresh = None
        try:
            fresh = fetch_titan_arena_status(client)
        except Exception as exc:  # best-effort: fall back to unverified
            logger.debug("Post-raid status fetch failed: %s", exc)
        fresh_rivals = fresh.get("rivals") if isinstance(fresh, dict) else None
        if (
            isinstance(fresh, dict)
            and fresh.get("status") == "battle"
            and isinstance(fresh_rivals, dict)
        ):
            completed = all(
                _attack_score(info) >= threshold for info in fresh_rivals.values()
            )
            basis = "status"
            raid_summary["final_tier"] = fresh.get("tier", status.get("tier"))
            raid_summary["remaining_rivals"] = sum(
                1
                for info in fresh_rivals.values()
                if _attack_score(info) < threshold
            )
        else:
            # No authoritative view: do not guess that the tier is complete.
            # The next pass's status/recheck decides; suppress a same-tier
            # raid retry and let individual battles handle any leftovers.
            completed = False
            basis = "unverified"
            raid_summary["no_verified"] = True
            raid_summary["error"] = (
                "raid completion unverified: no usable EndRaid echo and "
                "titanArenaGetStatus unavailable; falling back to individual battles"
            )
    raid_summary["completed"] = completed
    raid_summary["completed_basis"] = basis
    below = (
        sum(1 for rid in results if _attack_score(scored[rid]) < threshold)
        if basis == "endraid"
        else None
    )
    below_text = f", below_threshold={below}" if below is not None else ""
    print(
        f"{Emojis.STEP}Raid results completed={completed} "
        f"(basis={basis}, verified {len(results)}/{len(raid_rivals)}{below_text})",
        flush=True,
    )
    return raid_summary


def _is_beaten_up_detail(error_name: Any, detail: Any) -> bool:
    """True for the server's "beaten up already" NotAvailable/NotFound reply."""
    if error_name not in ("NotAvailable", "NotFound"):
        return False
    try:
        text = json.dumps(detail, default=str).lower()
    except Exception:
        text = str(detail).lower()
    return "beaten up already" in text


def _is_beaten_already(res: dict[str, Any]) -> bool:
    """True when StartBattle says the rival is already beaten.

    Happens with a stale status snapshot: an earlier attempt in the same run
    (or another client) cleared the rival after we listed targets.
    The server message is matched literally (``beaten up already``); a
    NotAvailable/NotFound error with different text is logged so a server
    message change is visible instead of silently misclassified.
    """
    if res.get("error") not in ("NotAvailable", "NotFound"):
        return False
    if _is_beaten_up_detail(res.get("error"), res.get("detail")):
        return True
    try:
        detail = json.dumps(res.get("detail"), default=str).lower()
    except Exception:
        detail = str(res.get("detail")).lower()
    print(f"{Emojis.WARNING}StartBattle {res.get('error')} with unexpected detail: {detail[:200]}", flush=True)
    return False


def _is_already_cleared(client: HWClient, rival_id: str, threshold: int) -> bool:
    """Re-fetch status and check whether ``rival_id`` is now cleared."""
    try:
        fresh = fetch_titan_arena_status(client)
    except Exception:
        return False
    rivals = fresh.get("rivals") or {}
    info = rivals.get(str(rival_id)) if isinstance(rivals, dict) else None
    return not isinstance(info, dict) or (info.get("attackScore") or 0) >= threshold


def _decide_attempt_action(
    res: dict[str, Any],
    *,
    attempt_no: int,
    plan_len: int,
    stop_on_first_loss: bool = False,
) -> dict[str, Any]:
    """Classify one attempt result into a single action descriptor.

    Shared by the plan loop and the best-loss fallback in
    :func:`_run_rivals` so both paths interpret results identically.
    Pure (no side effects): the caller executes the action, owning
    ``bridge_errors`` counting, ``BridgeDeadError`` raises,
    ``rival_reported`` updates, prints and status-line updates.
    """
    if res.get("bridge_error") or _is_bridge_timeout(res):
        return {"action": "bridge_error", "timeout": _is_bridge_timeout(res)}
    if _is_beaten_already(res):
        return {"action": "beaten_already"}
    end_error = res.get("end_error")
    start_error = res.get("error")
    if end_error == "Invalid battle" and attempt_no < plan_len:
        return {"action": "retry"}
    est = res.get("estimate")
    # A failed EndBattle (Invalid battle, NotFound, NotAvailable, ...) banked
    # nothing server-side, so it must not count as a win even when the local
    # estimate says win. Estimate-only fallbacks and StartBattle errors also
    # never bank a score.
    unverified = bool(res.get("estimate_only") or end_error or start_error)
    win = (
        bool(est and getattr(est, "win", False))
        and not end_error
        and not start_error
        and not unverified
    )
    return {
        "action": "record",
        "win": win,
        "unverified": unverified,
        "abort": bool(stop_on_first_loss and not unverified),
    }


def _run_rivals(
    client: HWClient,
    status: dict[str, Any],
    titans: list[int],
    engine: BattleEngine,
    threshold: int,
    stop_on_first_loss: bool,
    max_attempts_per_rival: int = 3,
    team_rotation: list[list[int]] | None = None,
    seeds_per_team: int = 2,
    end_on_loss: bool = False,
    bank_best_loss: bool = True,
    max_total_attempts: int | None = None,
    account_label: str | None = None,
    progress: str = "verbose",
    dashboard: Any = None,
    tier: Any | None = None,
) -> list[dict[str, Any]]:
    progress = _normalize_progress(progress)
    finish_targets = _select_auto_rivals(status, threshold)
    if not finish_targets:
        return []
    status_rivals = status.get("rivals")
    all_rivals = (
        [str(rid) for rid in status_rivals]
        if isinstance(status_rivals, dict)
        else []
    )
    # Attempt plan per rival: explicit/single-team callers repeat the one team
    # up to max_attempts_per_rival (legacy Invalid retry); a rotation does
    # exactly one full pass (each team × seeds_per_team) so proven winners
    # hiding late in the list (e.g. a win on attempt 9) are still reached.
    # The plan is finite and logged; runaway protection comes from the
    # bridge-dead abort and the tier-level pass cap instead of truncation.
    # With end_on_loss=False, losing sims abandon the battle without
    # EndBattle (no score banking, faster sweeps). When the whole plan
    # still yields no win, bank_best_loss submits one extra EndBattle with
    # the highest-stars losing team (end_on_loss=True) so partial
    # attackScore is banked instead of leaving the rival untouched.
    # max_total_attempts caps the plan (None = full pass). Estimate-engine
    # runs should pass an explicit cap to bound per-rival StartBattle cost.
    if team_rotation is None:
        attempt_plan = [(titans, s) for s in range(max(1, max_attempts_per_rival))]
    else:
        teams = list(team_rotation) or [titans]
        attempt_plan = [(team, s) for team in teams for s in range(max(1, seeds_per_team))]
    full_pass = len(attempt_plan)
    if max_total_attempts is not None:
        attempt_plan = attempt_plan[: max(1, max_total_attempts)]
    _vprint(
        progress,
        f"{Emojis.INFO}Attempt plan per rival: {len(attempt_plan)}/{full_pass} "
        f"(teams={len(team_rotation) if team_rotation else 1} seeds={seeds_per_team}"
        + (f" cap={max_total_attempts}" if max_total_attempts is not None else "")
        + ")",
    )
    results: list[dict[str, Any]] = []
    bridge_errors = 0
    acc_key = str(account_label or "?")
    line_state: dict[str, Any] = {"last_emit": 0.0, "throttle_secs": PROGRESS_THROTTLE_SECS}

    def _update_line(rival_id: str, attempt_no: int, last: str, rival_start: float, total: int | None = None) -> None:
        wins = sum(1 for r in results if r.get("win"))
        elapsed = time.monotonic() - rival_start
        # The banking attempt is one extra StartBattle outside the plan cap,
        # so its updates pass total=len+1 explicitly; plan attempts use the default.
        body = _build_status_text(
            str(rival_id),
            attempt_no,
            len(attempt_plan) if total is None else total,
            wins,
            last,
            elapsed,
            tier=tier,
            rival_no=display_no,
            rival_total=display_total,
        )
        _emit_status_line(acc_key, body, dashboard, line_state, progress)

    for rival_no, rival_id in enumerate(finish_targets, start=1):
        if _cancel_requested():
            raise _interrupted_error(results)
        try:
            display_no = all_rivals.index(str(rival_id)) + 1
        except ValueError:
            display_no = rival_no
        display_total = len(all_rivals) or len(finish_targets)
        rival_start = time.monotonic()
        tier_prefix = f"Tier {tier} " if tier is not None else ""
        _vprint(progress, f"{Emojis.STEP}{tier_prefix}Rival {rival_id} ({display_no}/{display_total}, {len(attempt_plan)} attempts planned)")
        rival_reported = False
        # Verified losing estimates, ranked for the best-loss fallback:
        # (stars, first_seen_order, team). Abandoned battles cannot be
        # submitted retroactively (the server validates progress against the
        # pending StartBattle seed), so the fallback re-runs the best team
        # once more with end_on_loss=True instead.
        loss_candidates: list[tuple[int, int, list[int]]] = []
        for attempt_no, (team, _seed_no) in enumerate(attempt_plan, start=1):
            if _cancel_requested():
                raise _interrupted_error(results)
            try:
                res = run_titan_arena(
                    client, rival_id=rival_id, titans=team, engine=engine, end_on_loss=end_on_loss,
                    attempt_label=f"{attempt_no}/{len(attempt_plan)}",
                    account_label=account_label,
                    progress=progress,
                    dashboard=dashboard,
                )
            except (KeyboardInterrupt, InterruptedError):
                raise _interrupted_error(results)
            except Exception as exc:  # pragma: no cover - defensive
                results.append({"rivalId": str(rival_id), "error": str(exc)})
                rival_reported = True
                _aprint(progress, f"  - rival {rival_id}: exception {exc}")
                _update_line(str(rival_id), attempt_no, "error", rival_start)
                break
            # Any bridge failure (timeout or fast-fail like 404/connection
            # refused, i.e. any bridge_error) counts toward the dead-bridge
            # abort, mirroring _run_raid. Pure estimate-only without
            # bridge_error (explicit --estimate-only) is verified contact
            # and resets the counter.
            action = _decide_attempt_action(
                res, attempt_no=attempt_no, plan_len=len(attempt_plan),
                stop_on_first_loss=stop_on_first_loss,
            )
            if action["action"] == "bridge_error":
                bridge_errors += 1
                if action["timeout"]:
                    _vprint(
                        progress,
                        f"  - rival {rival_id}: userscript not responding "
                        f"({bridge_errors}/{MAX_CONSECUTIVE_BRIDGE_TIMEOUTS})...",
                    )
                else:
                    _vprint(
                        progress,
                        f"  - rival {rival_id}: bridge error {res.get('bridge_error')} "
                        f"({bridge_errors}/{MAX_CONSECUTIVE_BRIDGE_TIMEOUTS})...",
                    )
                _update_line(str(rival_id), attempt_no, "bridge-error", rival_start)
                if bridge_errors >= MAX_CONSECUTIVE_BRIDGE_TIMEOUTS:
                    msg = (
                        "userscript not answering "
                        f"({bridge_errors} consecutive bridge errors). Open the game in the browser "
                        "with the userscript active, then re-run. Stopping tier loop."
                    )
                    _aprint(progress, f"{Emojis.WARNING}{msg}")
                    raise BridgeDeadError(msg, partial_results=results) from None
                continue
            bridge_errors = 0
            if action["action"] == "beaten_already":
                # Stale snapshot: the rival is actually cleared. Refresh to
                # confirm and count it as cleared so the tier can complete.
                rival_reported = True
                if _is_already_cleared(client, str(rival_id), threshold):
                    _aprint(progress, f"  - rival {rival_id}: already cleared (stale snapshot).")
                    results.append({"rivalId": str(rival_id), "win": True, "already_cleared": True})
                    _aprint(progress, f"{Emojis.VICTORY}rival {rival_id}: WIN (already cleared)")
                    _update_line(str(rival_id), attempt_no, "WIN", rival_start)
                else:
                    results.append({"rivalId": str(rival_id), "win": False, "start_error": res.get("error")})
                    _aprint(progress, f"{Emojis.INFO}rival {rival_id}: no win (start error {res.get('error')})")
                    _update_line(str(rival_id), attempt_no, "loss", rival_start)
                break
            if action["action"] == "retry":
                _vprint(
                    progress,
                    f"  - rival {rival_id}: Invalid battle, retrying ({attempt_no + 1}/{len(attempt_plan)})...",
                )
                _update_line(str(rival_id), attempt_no, "retry", rival_start)
                continue
            est = res.get("estimate")
            unverified = bool(action["unverified"])
            win = bool(action["win"])
            entry: dict[str, Any] = {"rivalId": str(rival_id), "win": win, "team": team}
            if unverified:
                entry["unverified"] = True
            if res.get("error"):
                entry["start_error"] = res.get("error")
            if res.get("end_error"):
                entry["end_error"] = res.get("end_error")
            if res.get("abandoned"):
                entry["abandoned"] = True
            results.append(entry)
            if not win and not unverified and res.get("abandoned") and est is not None:
                # Verified loss with nothing banked: rankable for the
                # best-loss fallback below (stars desc, earliest first).
                loss_candidates.append((_loss_stars(est), len(loss_candidates), list(team)))
            if win:
                rival_reported = True
                _aprint(progress, f"{Emojis.VICTORY}rival {rival_id}: WIN ({attempt_no}/{len(attempt_plan)})")
                _update_line(str(rival_id), attempt_no, "WIN", rival_start)
                break
            _update_line(str(rival_id), attempt_no, "unverified" if unverified else "loss", rival_start)
            # Unverified attempts (bridge failed, nothing banked) never
            # trigger the abort: they carry no signal about team strength,
            # so the rotation keeps trying the next team/seed.
            if stop_on_first_loss and not unverified:
                rival_reported = True
                _aprint(progress, f"{Emojis.INFO}rival {rival_id}: loss, stopping tier loop.")
                _finish_status_line(progress)
                return results
            # Loss/abandon with attempts left: next seed/team.
            if attempt_no < len(attempt_plan):
                elapsed = time.monotonic() - rival_start
                pace = elapsed / attempt_no
                reason = "abandoned, next seed" if res.get("abandoned") else "loss, trying next"
                _vprint(
                    progress,
                    f"  - rival {rival_id}: {reason} ({attempt_no + 1}/{len(attempt_plan)}, "
                    f"elapsed {_fmt_duration(elapsed)}, ~{_fmt_duration(pace)}/attempt)...",
                )
        if not rival_reported and not stop_on_first_loss and bank_best_loss:
            rival_entries = [r for r in results if str(r.get("rivalId")) == str(rival_id)]
            if (
                not any(r.get("win") for r in rival_entries)
                and loss_candidates
                and not isinstance(engine, PythonBattleEngine)
            ):
                # Best-loss fallback: no win after the full plan, so bank
                # partial attackScore with the highest-stars losing team.
                # The abandoned battles above cannot be submitted
                # retroactively (the server checks progress against the
                # pending StartBattle seed), hence one fresh attempt with
                # end_on_loss=True. Estimate-engine progress is always
                # rejected as "Invalid battle", so it is skipped.
                if _cancel_requested():
                    raise _interrupted_error(results)
                best_stars, _, best_team = max(loss_candidates, key=lambda c: (c[0], -c[1]))
                _aprint(
                    progress,
                    f"{Emojis.STEP}rival {rival_id}: no win after {len(attempt_plan)} attempts — "
                    f"banking best loss (stars={best_stars} team={best_team})...",
                )
                try:
                    bank_res = run_titan_arena(
                        client, rival_id=rival_id, titans=best_team, engine=engine, end_on_loss=True,
                        attempt_label=f"bank/{len(attempt_plan) + 1}",
                        account_label=account_label,
                        progress=progress,
                        dashboard=dashboard,
                    )
                except (KeyboardInterrupt, InterruptedError):
                    raise _interrupted_error(results)
                except Exception as exc:  # pragma: no cover - defensive
                    results.append({"rivalId": str(rival_id), "win": False, "team": best_team, "banked_loss": True, "error": str(exc)})
                    rival_reported = True
                    _aprint(progress, f"  - rival {rival_id}: best-loss banking failed: {exc}")
                    _update_line(str(rival_id), len(attempt_plan) + 1, "error", rival_start, total=len(attempt_plan) + 1)
                else:
                    bank_action = _decide_attempt_action(
                        bank_res, attempt_no=len(attempt_plan) + 1,
                        plan_len=len(attempt_plan) + 1,
                    )
                    bank_total = len(attempt_plan) + 1
                    if bank_action["action"] == "bridge_error":
                        bridge_errors += 1
                        results.append({"rivalId": str(rival_id), "win": False, "team": best_team, "banked_loss": True, "unverified": True, "bridge_error": bank_res.get("bridge_error")})
                        rival_reported = True
                        _update_line(str(rival_id), bank_total, "bridge-error", rival_start, total=bank_total)
                        if bridge_errors >= MAX_CONSECUTIVE_BRIDGE_TIMEOUTS:
                            msg = (
                                "userscript not answering "
                                f"({bridge_errors} consecutive bridge errors). Open the game in the browser "
                                "with the userscript active, then re-run. Stopping tier loop."
                            )
                            _aprint(progress, f"{Emojis.WARNING}{msg}")
                            raise BridgeDeadError(msg, partial_results=results) from None
                    elif bank_action["action"] == "beaten_already":
                        rival_reported = True
                        if _is_already_cleared(client, str(rival_id), threshold):
                            _aprint(progress, f"  - rival {rival_id}: already cleared (stale snapshot).")
                            results.append({"rivalId": str(rival_id), "win": True, "already_cleared": True})
                            _aprint(progress, f"{Emojis.VICTORY}rival {rival_id}: WIN (already cleared)")
                            _update_line(str(rival_id), bank_total, "WIN", rival_start, total=bank_total)
                        else:
                            results.append({"rivalId": str(rival_id), "win": False, "team": best_team, "banked_loss": True, "start_error": bank_res.get("error")})
                            _aprint(progress, f"{Emojis.INFO}rival {rival_id}: no win (start error {bank_res.get('error')})")
                            _update_line(str(rival_id), bank_total, "loss", rival_start, total=bank_total)
                    else:
                        bank_est = bank_res.get("estimate")
                        bank_unverified = bool(bank_action["unverified"])
                        bank_win = bool(bank_action["win"])
                        bank_entry: dict[str, Any] = {"rivalId": str(rival_id), "win": bank_win, "team": best_team, "banked_loss": True}
                        if bank_unverified:
                            bank_entry["unverified"] = True
                        if bank_res.get("end_error"):
                            bank_entry["end_error"] = bank_res.get("end_error")
                        if bank_res.get("abandoned"):
                            bank_entry["abandoned"] = True
                        results.append(bank_entry)
                        rival_reported = True
                        if bank_win:
                            _aprint(progress, f"{Emojis.VICTORY}rival {rival_id}: WIN (best-loss banking)")
                            _update_line(str(rival_id), bank_total, "WIN", rival_start, total=bank_total)
                        else:
                            _aprint(progress, f"{Emojis.INFO}rival {rival_id}: best loss banked (stars={_loss_stars(bank_est)})")
                            _update_line(str(rival_id), bank_total, "banked-loss", rival_start, total=bank_total)
        if not rival_reported:
            entries = [r for r in results if str(r.get("rivalId")) == str(rival_id)]
            if entries and not any(r.get("win") for r in entries) and len(entries) >= len(attempt_plan):
                _aprint(progress, f"{Emojis.INFO}rival {rival_id}: no win after {len(entries)} attempts")
                _update_line(str(rival_id), len(attempt_plan), "loss", rival_start)
    _finish_status_line(progress)
    return results


def _complete_tier(client: HWClient, summary: dict[str, Any], tier: Any = None) -> bool:
    res = client.call(
        {"calls": [{"name": ApiAction.TITAN_ARENA_COMPLETE_TIER, "args": {}, "ident": "body"}]}
    )
    if res.is_success:
        summary["completed_tier"] = True
        summary["completed_tier_key"] = _tier_key(tier)
        print(f"{Emojis.SUCCESS}Tier completed.", flush=True)
        return True
    if res.error_name == "NotAvailable":
        # Normal mid-run state: rivals remain, so there is nothing to
        # complete yet. Not an error — the loop continues with them.
        detail_preview = _shorten_response(res.detail)
        tier_label = f" tier={tier}" if tier is not None else ""
        raid_completed = bool(
            summary.get("raid_results") and summary["raid_results"][-1].get("completed")
        )
        if raid_completed:
            # Contradiction: the raid claimed the threshold yet the tier is
            # not completeable — surface loudly so a stuck tier is visible.
            print(
                f"{Emojis.WARNING}Tier{tier_label} raid reported completed "
                f"but CompleteTier NotAvailable: {detail_preview}",
                flush=True,
            )
        else:
            print(
                f"{Emojis.INFO}Tier not yet completeable (rivals remain){tier_label}: {detail_preview}",
                flush=True,
            )
        return False
    summary["errors"].append({"stage": "complete_tier", "error": res.error_name, "detail": res.detail})
    print(f"{Emojis.WARNING}titanArenaCompleteTier failed ({res.error_name}).", flush=True)
    return False


def _farm_daily_reward(client: HWClient, summary: dict[str, Any]) -> bool:
    res = client.call(
        {"calls": [{"name": ApiAction.TITAN_ARENA_FARM_DAILY_REWARD, "args": {}, "ident": "body"}]}
    )
    if res.is_success:
        summary["daily_reward"] = "claimed"
        print(f"{Emojis.SUCCESS}Daily reward claimed.", flush=True)
        return True
    if res.error_name in ("NotAvailable", "alreadyClaimed"):
        summary["daily_reward"] = "already-claimed"
        print(f"{Emojis.INFO}Daily reward already claimed today.", flush=True)
        return True
    summary["errors"].append({"stage": "daily_reward", "error": res.error_name, "detail": res.detail})
    print(f"{Emojis.WARNING}Daily reward failed ({res.error_name}).", flush=True)
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_titans(client: HWClient, titans: list[int] | None) -> list[int]:
    if titans is not None:
        titans = [int(t) for t in titans]
        if len(titans) != 5:
            raise ValueError(f"titans must be 5 ids, got {titans}")
        return titans
    team_res = client.call({"calls": [{"name": "teamGetAll", "args": {}, "ident": "body"}]})
    if team_res.is_success and isinstance(team_res.detail, dict):
        resp = team_res.detail.get("response", {})
        if "titan_arena" in resp:
            return [int(x) for x in resp["titan_arena"]]
    raise RuntimeError("Could not resolve titan team; pass --titans explicitly")
