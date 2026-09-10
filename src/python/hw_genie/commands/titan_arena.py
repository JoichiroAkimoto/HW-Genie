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
import time
from typing import Any

from hw_genie.battle.engine import (
    BattleEngine,
    BattleEstimate,
    BridgeError,
    BridgeDeadError,
    PythonBattleEngine,
)
from hw_genie.core.client import ApiAction, Emojis, HWClient, ResponseStatus

logger = logging.getLogger(__name__)


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
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


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

# Static team rotation tried in order after the saved team when --titans is
# omitted. Entries are 5 titan IDs; the saved team (teamGetAll.titan_arena)
# always goes first and duplicates are skipped.
MAX_CONSECUTIVE_BRIDGE_TIMEOUTS = 3


def _is_bridge_timeout(res: dict[str, Any]) -> bool:
    """True when the attempt never reached the userscript (bridge timeout)."""
    return "did not finish battle within" in str(res.get("bridge_error") or "")


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
    [4033, 4003, 4004, 4001, 4000],
    [4003, 4013, 4004, 4001, 4000],
    [4003, 4023, 4004, 4001, 4000],
    [4033, 4003, 4002, 4004, 4001],
    [4003, 4013, 4002, 4004, 4001],
    [4003, 4023, 4002, 4004, 4001],
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
    """
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
    print(f"\n{Emojis.STEP}Titan Arena {attempt_prefix}rivalId={rival_id_str} titans={titans}", flush=True)
    if dry_run:
        print(f"{Emojis.INFO}Dry-run: verifying titanArenaStartBattle is accepted with arbitrary titans...", flush=True)

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
        print(f"{Emojis.ERROR}Start failed ({start_res.error_name}): {start_res.detail}", flush=True)
        if start_res.error_name == "NotFound":
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
    print(f"{Emojis.SUCCESS}StartBattle accepted (type={battle.get('type')} seed={battle.get('seed')})", flush=True)
    if dry_run:
        print(f"{Emojis.INFO}Dry-run: not calling titanArenaEndBattle.", flush=True)
        return {"status": ResponseStatus.SUCCESS, "battle": battle, "dry_run": True}

    try:
        est = engine.calc(battle)
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
            print(
                f"{Emojis.INFO}Hint: ensure `hw-genie auth-server` is running and the browser has the game open with the userscript active (any game page works; the Titan Arena screen is not required). Falling back to estimate-only.",
                flush=True,
            )
        # Return estimate-only style so the caller can decide; do not call EndBattle with invalid progress
        fallback = PythonBattleEngine().calc(battle)
        return {
            "status": ResponseStatus.SUCCESS,
            "battle": battle,
            "estimate": fallback,
            "estimate_only": True,
            "bridge_error": str(exc),
        }
    print(
        f"{Emojis.STEP}Engine: win={est.win} stars={est.stars} ({type(engine).__name__})",
        flush=True,
    )
    if estimate_only:
        print(f"{Emojis.INFO}Estimate-only: not calling titanArenaEndBattle.", flush=True)
        return {"status": ResponseStatus.SUCCESS, "battle": battle, "estimate": est, "estimate_only": True}

    if not est.win and not end_on_loss:
        print(f"{Emojis.INFO}Loss — abandoning battle without EndBattle (no score banking).", flush=True)
        return {"status": ResponseStatus.SUCCESS, "battle": battle, "estimate": est, "abandoned": True}

    return _end_battle(client, rival_id_str, est)


def _end_battle(client: HWClient, rival_id: str, est: BattleEstimate) -> dict[str, Any]:
    print(f"{Emojis.STEP}Calling titanArenaEndBattle...", flush=True)
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
        logger.debug("EndBattle full response: %s", _shorten_response(response, 100000))
        print(f"{Emojis.ERROR}EndBattle failed ({end_res.error_name}): {_shorten_response(response)}", flush=True)
        return {"status": end_res.status, "estimate": est, "end_detail": response}
    logger.debug("EndBattle full response: %s", _shorten_response(response, 100000))
    print(f"{Emojis.SUCCESS}EndBattle: {_summarize_end_battle(rival_id, est, response)}", flush=True)
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
    seeds_per_team: int = 2,
    max_total_attempts: int | None = None,
) -> dict[str, Any]:
    """Run a ToE tier end-to-end.

    Mirrors ``HerowarsHelper.executeTitanArena``:

    1. ``titanArenaGetStatus`` to read ``status``/``canRaid``/``tier``/``rivals``.
    2. ``canRaid`` → ``titanArenaStartRaid`` (one per remaining un-touched rival),
       each via the engine, then ``titanArenaEndRaid`` to submit results.
    3. otherwise loop ``rivals`` whose ``attackScore < threshold`` and finish
       each via the engine. After every successful finish, recurse into
       ``titanArenaCompleteTier``.
    4. Best-effort ``titanArenaFarmDailyReward`` for the daily chest after
       each pass; early returns (disabled/peace_time, no targets, no
       progress, bridge dead) skip the remaining steps and report via
       ``summary["errors"]``.

    ``stop_on_first_loss`` aborts the per-rival loop after the first losing
    (or abandoned) attempt — including rotation retries — so a weak rotation
    stops fast. This is orthogonal to the per-battle abandon policy: the
    tier loop always calls :func:`run_titan_arena` with
    ``end_on_loss=False``, i.e. losing sims skip ``EndBattle`` (no score
    banking) and advance to the next team/seed instead of banking a loss.

    ``max_total_attempts`` caps total StartBattle attempts per rival
    (``None`` = full pass: ``max_attempts_per_rival`` for a single team,
    otherwise ``len(rotation) × seeds_per_team``). Estimate-engine runs,
    whose EndBattle is rejected as ``Invalid battle``, should pass an
    explicit cap to avoid paying the full sweep per rival.

    The function returns a dict summarising the tier outcome (rival results,
    raid results, errors, completed_tier, daily_reward).
    """
    if isinstance(client_or_headers, dict):
        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    engine = engine or PythonBattleEngine()
    rotation = _resolve_team_rotation(client, titans)
    titans = rotation[0]
    summary: dict[str, Any] = {
        "titans": titans,
        "rotation": rotation,
        "engine": type(engine).__name__,
        "rival_results": [],
        "raid_results": [],
        "errors": [],
        "completed_tier": False,
        "daily_reward": None,
    }

    # Safety cap: each winning pass strictly shrinks the remaining target
    # set (cleared rivals hit 250), so the loop terminates naturally. The cap
    # only guards against pathological server states.
    max_passes = 30
    passes = 0
    while True:
        passes += 1
        if passes > max_passes:
            summary["errors"].append({"stage": "tier_loop", "message": "max passes reached"})
            print(f"{Emojis.WARNING}Tier loop cap reached; stopping.", flush=True)
            return summary
        status = fetch_titan_arena_status(client)
        state = status.get("status")
        if state in (None, "disabled"):
            print(f"{Emojis.WARNING}ToE is disabled. aborting.", flush=True)
            summary["errors"].append({"stage": "status", "message": f"status={state!r}"})
            return summary
        if state == "peace_time":
            print(f"{Emojis.INFO}ToE is in peace_time (threshold={attack_score_threshold}); nothing to do.", flush=True)
            return summary

        tier = status.get("tier")
        print(f"{Emojis.STEP}Tier: {tier} (canRaid={status.get('canRaid')})", flush=True)

        if status.get("canRaid"):
            try:
                raid_summary = _run_raid(client, status, titans, engine, attack_score_threshold)
            except BridgeDeadError as exc:
                summary["raid_results"].append({"stage": "raid", "battles": [], "completed": False, "bridge_dead": True})
                summary["rival_results"].extend(exc.partial_results)
                summary["errors"].append({"stage": "raid", "message": str(exc)})
                print(f"{Emojis.WARNING}Bridge dead during raid ({exc}); stopping tier loop.", flush=True)
                return summary
            summary["raid_results"].append(raid_summary)
            if raid_summary.get("bridge_dead"):
                summary["errors"].append({"stage": "raid", "message": raid_summary.get("error", "bridge dead")})
                print(f"{Emojis.WARNING}Bridge dead during raid; stopping tier loop.", flush=True)
                return summary
            if not raid_summary.get("completed", False):
                # If raid didn't clear the threshold we still try the per-rival
                # path in case the user reconnected mid-tier.
                pass
            else:
                _complete_tier(client, summary, tier=tier)
                continue

        try:
            rival_results = _run_rivals(
                client, status, titans, engine, attack_score_threshold, stop_on_first_loss,
                team_rotation=rotation, seeds_per_team=seeds_per_team, end_on_loss=False,
                max_total_attempts=max_total_attempts,
            )
        except BridgeDeadError as exc:
            summary["rival_results"].extend(exc.partial_results)
            summary["errors"].append({"stage": "rivals", "message": str(exc)})
            print(f"{Emojis.WARNING}Bridge dead ({exc}); stopping tier loop.", flush=True)
            return summary
        # A bridge-dead sentinel is never mixed into rival_results anymore
        # (it raises); an empty list here genuinely means "no targets".
        summary["rival_results"].extend(rival_results)
        if not rival_results:
            print(f"{Emojis.INFO}No rivals to attack (threshold={attack_score_threshold})", flush=True)
            _farm_daily_reward(client, summary)
            return summary
        if not any(r.get("win") for r in rival_results) and not status.get("canRaid"):
            print(f"{Emojis.WARNING}No rival cleared; stopping tier loop.", flush=True)
            return summary
        _complete_tier(client, summary, tier=tier)
        # Best-effort daily chest; the return value no longer ends the run so
        # remaining rivals (or the next tier) are attacked in the next pass.
        _farm_daily_reward(client, summary)
        # Loop again with fresh status until nothing is left to attack or no
        # progress is made.
        continue


def _run_raid(
    client: HWClient,
    status: dict[str, Any],
    titans: list[int],
    engine: BattleEngine,
    threshold: int,
) -> dict[str, Any]:
    rivals = (status.get("rivals") or {}) if isinstance(status.get("rivals"), dict) else {}
    raid_summary: dict[str, Any] = {"stage": "raid", "battles": [], "completed": False}
    print(f"{Emojis.STEP}Raid phase: {len(rivals)} rival(s)", flush=True)
    start = client.call(
        {"calls": [{"name": ApiAction.TITAN_ARENA_START_RAID, "args": {"titans": titans}, "ident": "body"}]}
    )
    if not start.is_success:
        raid_summary["error"] = f"StartRaid failed: {start.error_name}"
        print(f"{Emojis.ERROR}{raid_summary['error']}", flush=True)
        return raid_summary
    detail = start.detail if isinstance(start.detail, dict) else {}
    response = detail.get("response", detail) if isinstance(detail, dict) else {}
    attackers = response.get("attackers") if isinstance(response, dict) else None
    raid_rivals = response.get("rivals") if isinstance(response, dict) else None
    if not isinstance(attackers, dict) or not isinstance(raid_rivals, dict):
        raid_summary["error"] = "StartRaid returned unexpected payload"
        print(f"{Emojis.ERROR}{raid_summary['error']}", flush=True)
        return raid_summary

    results: dict[str, dict[str, Any]] = {}
    bridge_errors = 0
    for rival_id, rival_defenders in raid_rivals.items():
        battle = {
            "userId": str(status.get("userId") or rival_id),
            "typeId": str(rival_id),
            "attackers": attackers,
            "defenders": [rival_defenders] if isinstance(rival_defenders, dict) else [],
            "effects": [],
            "reward": [],
            "startTime": 0,
            "seed": 0,
            "type": "titan_arena",
        }
        try:
            est = engine.calc(battle)
            results[str(rival_id)] = {"progress": est.progress, "result": {"win": est.win, "stars": est.stars}}
            raid_summary["battles"].append({"rivalId": str(rival_id), "win": est.win})
            print(f"  - raid rival {rival_id}: win={est.win}", flush=True)
        except (BridgeError, Exception) as exc:
            if isinstance(engine, PythonBattleEngine):
                raise
            bridge_errors += 1
            raid_summary["battles"].append({"rivalId": str(rival_id), "error": str(exc)})
            print(f"  - raid rival {rival_id}: bridge error {exc} ({bridge_errors}/{MAX_CONSECUTIVE_BRIDGE_TIMEOUTS})", flush=True)
            if bridge_errors >= MAX_CONSECUTIVE_BRIDGE_TIMEOUTS:
                raise BridgeDeadError(
                    f"userscript not answering during raid ({bridge_errors} consecutive bridge errors)",
                    partial_results=[],
                ) from exc
            # Skip fake progress: without a verified engine result we must
            # not fabricate progress for EndRaid. The rival is simply left
            # out of the results so the server scores only verified battles.
            continue

    if not results:
        raid_summary["error"] = "all raid battles failed (bridge errors); EndRaid skipped"
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
        print(f"{Emojis.ERROR}{raid_summary['error']}", flush=True)
        return raid_summary
    end_detail = end.detail if isinstance(end.detail, dict) else {}
    end_response = end_detail.get("response", {}) if isinstance(end_detail, dict) else {}
    end_results = end_response.get("results") if isinstance(end_response, dict) else None
    if isinstance(end_results, dict):
        raid_summary["completed"] = all(
            (v.get("attackScore") or 0) >= threshold for v in end_results.values() if isinstance(v, dict)
        )
    print(
        f"{Emojis.STEP}Raid results completed={raid_summary['completed']}", flush=True,
    )
    return raid_summary


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
    import json as _json

    try:
        detail = _json.dumps(res.get("detail"), default=str).lower()
    except Exception:
        detail = str(res.get("detail")).lower()
    matched = "beaten up already" in detail
    if not matched:
        print(f"{Emojis.WARNING}StartBattle {res.get('error')} with unexpected detail: {detail[:200]}", flush=True)
    return matched


def _is_already_cleared(client: HWClient, rival_id: str, threshold: int) -> bool:
    """Re-fetch status and check whether ``rival_id`` is now cleared."""
    try:
        fresh = fetch_titan_arena_status(client)
    except Exception:
        return False
    rivals = fresh.get("rivals") or {}
    info = rivals.get(str(rival_id)) if isinstance(rivals, dict) else None
    return not isinstance(info, dict) or (info.get("attackScore") or 0) >= threshold


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
    max_total_attempts: int | None = None,
) -> list[dict[str, Any]]:
    finish_targets = _select_auto_rivals(status, threshold)
    if not finish_targets:
        return []
    # Attempt plan per rival: explicit/single-team callers repeat the one team
    # up to max_attempts_per_rival (legacy Invalid retry); a rotation does
    # exactly one full pass (each team × seeds_per_team) so proven winners
    # hiding late in the list (e.g. a win on attempt 9) are still reached.
    # The plan is finite and logged; runaway protection comes from the
    # bridge-dead abort and the tier-level pass cap instead of truncation.
    # With end_on_loss=False, losing sims abandon the battle without
    # EndBattle (no score banking, faster sweeps).
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
    print(
        f"{Emojis.INFO}Attempt plan per rival: {len(attempt_plan)}/{full_pass} "
        f"(teams={len(team_rotation) if team_rotation else 1} seeds={seeds_per_team}"
        + (f" cap={max_total_attempts}" if max_total_attempts is not None else "")
        + ")",
        flush=True,
    )
    results: list[dict[str, Any]] = []
    bridge_timeouts = 0
    for rival_no, rival_id in enumerate(finish_targets, start=1):
        rival_start = time.monotonic()
        print(f"{Emojis.STEP}Rival {rival_id} ({rival_no}/{len(finish_targets)}, {len(attempt_plan)} attempts planned)", flush=True)
        for attempt_no, (team, _seed_no) in enumerate(attempt_plan, start=1):
            try:
                res = run_titan_arena(
                    client, rival_id=rival_id, titans=team, engine=engine, end_on_loss=end_on_loss,
                    attempt_label=f"{attempt_no}/{len(attempt_plan)}",
                )
            except Exception as exc:  # pragma: no cover - defensive
                results.append({"rivalId": str(rival_id), "error": str(exc)})
                print(f"  - rival {rival_id}: exception {exc}", flush=True)
                break
            if _is_bridge_timeout(res):
                bridge_timeouts += 1
                print(
                    f"  - rival {rival_id}: userscript not responding "
                    f"({bridge_timeouts}/{MAX_CONSECUTIVE_BRIDGE_TIMEOUTS})...",
                    flush=True,
                )
                if bridge_timeouts >= MAX_CONSECUTIVE_BRIDGE_TIMEOUTS:
                    msg = (
                        "userscript not answering "
                        f"({bridge_timeouts} consecutive bridge timeouts). Open the game in the browser "
                        "with the userscript active, then re-run. Stopping tier loop."
                    )
                    print(f"{Emojis.WARNING}{msg}", flush=True)
                    raise BridgeDeadError(msg, partial_results=results) from None
                continue
            bridge_timeouts = 0
            if _is_beaten_already(res):
                # Stale snapshot: the rival is actually cleared. Refresh to
                # confirm and count it as cleared so the tier can complete.
                if _is_already_cleared(client, str(rival_id), threshold):
                    print(f"  - rival {rival_id}: already cleared (stale snapshot).", flush=True)
                    results.append({"rivalId": str(rival_id), "win": True, "already_cleared": True})
                else:
                    results.append({"rivalId": str(rival_id), "win": False, "start_error": res.get("error")})
                break
            if res.get("end_error") == "Invalid battle" and attempt_no < len(attempt_plan):
                print(
                    f"  - rival {rival_id}: Invalid battle, retrying ({attempt_no + 1}/{len(attempt_plan)})...",
                    flush=True,
                )
                continue
            est = res.get("estimate")
            # An "Invalid battle" EndBattle banked nothing server-side, so it
            # must not count as a win even when the local estimate says win
            # (estimate engine) — otherwise the tier loop sees progress and
            # spins to max_passes.
            win = bool(est and getattr(est, "win", False)) and res.get("end_error") != "Invalid battle"
            entry: dict[str, Any] = {"rivalId": str(rival_id), "win": win, "team": team}
            if res.get("end_error"):
                entry["end_error"] = res.get("end_error")
            if res.get("abandoned"):
                entry["abandoned"] = True
            results.append(entry)
            if win:
                break
            if stop_on_first_loss:
                return results
            # Loss/abandon with attempts left: next seed/team.
            if attempt_no < len(attempt_plan):
                elapsed = time.monotonic() - rival_start
                pace = elapsed / attempt_no
                reason = "abandoned, next seed" if res.get("abandoned") else "loss, trying next"
                print(
                    f"  - rival {rival_id}: {reason} ({attempt_no + 1}/{len(attempt_plan)}, "
                    f"elapsed {_fmt_duration(elapsed)}, ~{_fmt_duration(pace)}/attempt)...",
                    flush=True,
                )
    return results


def _complete_tier(client: HWClient, summary: dict[str, Any], tier: Any = None) -> None:
    res = client.call(
        {"calls": [{"name": ApiAction.TITAN_ARENA_COMPLETE_TIER, "args": {}, "ident": "body"}]}
    )
    if res.is_success:
        summary["completed_tier"] = True
        print(f"{Emojis.SUCCESS}Tier completed.", flush=True)
        return
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
        return
    summary["errors"].append({"stage": "complete_tier", "error": res.error_name, "detail": res.detail})
    print(f"{Emojis.WARNING}titanArenaCompleteTier failed ({res.error_name}).", flush=True)


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


def _fallback_progress(battle: dict[str, Any], win: bool = False) -> list[dict[str, Any]]:
    """Empty loss progress (no fabricated wins; engine results only).

    Kept for backwards compatibility; the raid path no longer submits
    fabricated progress — bridge failures skip EndRaid entries instead.
    """
    del battle, win
    return [
        {
            "attackers": {"heroes": {}},
            "defenders": {"heroes": {}},
        }
    ]


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
