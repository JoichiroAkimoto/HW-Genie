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

from typing import Any

from hw_genie.battle.engine import (
    BattleEngine,
    BattleEstimate,
    BridgeError,
    PythonBattleEngine,
)
from hw_genie.core.client import ApiAction, Emojis, HWClient, ResponseStatus


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
) -> dict[str, Any]:
    """Start a single-rival battle, simulate via ``engine``, and EndBattle.

    ``engine`` defaults to the Python estimator. Provide a
    :class:`JsBridgeBattleEngine` for server-valid progress/result.

    When ``rival_id`` is ``None``, the best un-cleared rival is picked
    automatically via ``titanArenaGetStatus`` and
    :func:`_select_auto_rivals` (``attackScore < threshold``). When
    ``titans`` is ``None``, the saved team is fetched via ``teamGetAll``.
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

    print(f"\n{Emojis.STEP}Titan Arena: rivalId={rival_id_str} titans={titans}", flush=True)
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
        print(
            f"{Emojis.INFO}Hint: ensure `hw-genie auth-server` is running and the browser is on the Titan Arena screen with the userscript active. Falling back to estimate-only.",
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
        print(f"{Emojis.ERROR}EndBattle failed ({end_res.error_name}): {response}", flush=True)
        return {"status": end_res.status, "estimate": est, "end_detail": response}
    print(f"{Emojis.SUCCESS}EndBattle: {response}", flush=True)
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
) -> dict[str, Any]:
    """Run a ToE tier end-to-end.

    Mirrors ``HerowarsHelper.executeTitanArena``:

    1. ``titanArenaGetStatus`` to read ``status``/``canRaid``/``tier``/``rivals``.
    2. ``canRaid`` → ``titanArenaStartRaid`` (one per remaining un-touched rival),
       each via the engine, then ``titanArenaEndRaid`` to submit results.
    3. otherwise loop ``rivals`` whose ``attackScore < threshold`` and finish
       each via the engine. After every successful finish, recurse into
       ``titanArenaCompleteTier``.
    4. Always end with ``titanArenaFarmDailyReward`` for the daily chest.

    The function returns a dict summarising the tier outcome (rival results,
    raid results, errors, completed_tier, daily_reward).
    """
    if isinstance(client_or_headers, dict):
        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    engine = engine or PythonBattleEngine()
    titans = _resolve_titans(client, titans)
    summary: dict[str, Any] = {
        "titans": titans,
        "engine": type(engine).__name__,
        "rival_results": [],
        "raid_results": [],
        "errors": [],
        "completed_tier": False,
        "daily_reward": None,
    }

    while True:
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
            raid_summary = _run_raid(client, status, titans, engine, attack_score_threshold)
            summary["raid_results"].append(raid_summary)
            if not raid_summary.get("completed", False):
                # If raid didn't clear the threshold we still try the per-rival
                # path in case the user reconnected mid-tier.
                pass
            else:
                _complete_tier(client, summary)
                continue

        rival_results = _run_rivals(client, status, titans, engine, attack_score_threshold, stop_on_first_loss)
        summary["rival_results"].extend(rival_results)
        if not rival_results:
            print(f"{Emojis.INFO}No rivals to attack (threshold={attack_score_threshold})", flush=True)
            _farm_daily_reward(client, summary)
            return summary
        if not any(r.get("win") for r in rival_results) and not status.get("canRaid"):
            print(f"{Emojis.WARNING}No rival cleared; stopping tier loop.", flush=True)
            return summary
        _complete_tier(client, summary)
        # Re-enter loop: getStatus may now report peace_time or completed_tier
        if _farm_daily_reward(client, summary):
            # The daily reward only fires once per day; subsequent iterations
            # would be a no-op so we exit cleanly.
            return summary
        # Loop again to check the new tier / status
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
            raid_summary["battles"].append({"rivalId": str(rival_id), "error": str(exc)})
            results[str(rival_id)] = {
                "progress": _fallback_progress(battle, win=False),
                "result": {"win": False, "stars": 0},
            }
            print(f"  - raid rival {rival_id}: bridge error {exc}", flush=True)

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


def _run_rivals(
    client: HWClient,
    status: dict[str, Any],
    titans: list[int],
    engine: BattleEngine,
    threshold: int,
    stop_on_first_loss: bool,
) -> list[dict[str, Any]]:
    finish_targets = _select_auto_rivals(status, threshold)
    if not finish_targets:
        return []
    results: list[dict[str, Any]] = []
    for rival_id in finish_targets:
        try:
            res = run_titan_arena(client, rival_id=rival_id, titans=titans, engine=engine)
        except Exception as exc:  # pragma: no cover - defensive
            results.append({"rivalId": str(rival_id), "error": str(exc)})
            print(f"  - rival {rival_id}: exception {exc}", flush=True)
            continue
        est = res.get("estimate")
        win = bool(est and getattr(est, "win", False))
        results.append({"rivalId": str(rival_id), "win": win, "end_error": res.get("end_error")})
        if not win and stop_on_first_loss:
            break
    return results


def _battle_from_rival(
    status: dict[str, Any],
    rival_id: str,
    info: dict[str, Any],
    titans: list[int],
) -> dict[str, Any]:
    """Reconstruct a battle payload for a rival after the fact.

    The freshest payload is the one we just got from ``StartBattle``; but
    since the script runs sequentially we already saved it via :func:`run_titan_arena`.
    If we get here without that, build a minimal payload with the only field
    the engine needs (attackers/defenders).
    """
    return {
        "userId": str(info.get("userId") or rival_id),
        "typeId": str(rival_id),
        "attackers": {},  # populated by run_titan_arena from StartBattle
        "defenders": [{"seed": int(rival_id)}],  # placeholder; engine ignores defenders
        "effects": [],
        "reward": [],
        "startTime": 0,
        "seed": int(rival_id),
        "type": "titan_arena",
    }


def _complete_tier(client: HWClient, summary: dict[str, Any]) -> None:
    res = client.call(
        {"calls": [{"name": ApiAction.TITAN_ARENA_COMPLETE_TIER, "args": {}, "ident": "body"}]}
    )
    if res.is_success:
        summary["completed_tier"] = True
        print(f"{Emojis.SUCCESS}Tier completed.", flush=True)
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


def _fallback_progress(battle: dict[str, Any], win: bool) -> list[dict[str, Any]]:
    """Use the Python estimator as a fallback when the bridge fails."""
    est = PythonBattleEngine().calc(battle)
    # Force the desired win flag so the EndRaid request still goes through
    # with a "loss" outcome (server accepts losses even if progress is fake).
    if not win:
        return [
            {
                "attackers": {"heroes": {}},
                "defenders": {"heroes": {}},
            }
        ]
    return est.progress


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
