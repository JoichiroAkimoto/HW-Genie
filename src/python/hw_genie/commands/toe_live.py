"""Live Titan Arena (ToE) driver.

The 2026-11-26 game update (serverVersion 302) rejects offline instant
simulations for solo ToE battles because the server now validates the
``battleSeed_next`` reseed proof. Raids are unaffected (their battle payload
carries ``effects: []``), but solo battles and walls must be driven through the
real game client.

The Dev userscript exposes a ``live`` job on the existing auth-server queue:

* ``POST /toe/job`` with ``{account, kind:"live", action:"battle", rivalId,
  titans}`` starts a real battle in the account's game tab, lets the client
  submit ``titanArenaEndBattle`` with a valid proof, and reports
  ``{ok, error?, result:{win, stars}}``.
* The userscript reloads the page after each battle to clear the result popup.

This module posts those jobs and drives the tier loop: for each unfinished
rival it tries the team rotation, reads the authoritative server status after
each battle, banks progress, and calls ``titanArenaCompleteTier`` when every
rival reaches the threshold. The game tab must stay visible/rendering while a
battle runs (the login/battle loop needs frames).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from hw_genie.core.client import Emojis, HWClient
from hw_genie.commands.titan_arena import (
    _complete_tier,
    _farm_daily_reward,
    _resolve_team_rotation,
    _select_auto_rivals,
)


def _status(client: HWClient) -> dict[str, Any]:
    res = client.call({"calls": [{"name": "titanArenaGetStatus", "args": {}, "ident": "body"}]})
    if not res.is_success:
        raise RuntimeError(f"titanArenaGetStatus failed: {res.error_name}")
    detail = res.detail if isinstance(res.detail, dict) else {}
    response = detail.get("response", detail)
    return response if isinstance(response, dict) else {}


class LiveJobClient:
    """Post live ToE jobs to the auth server and wait for the tab's result."""

    def __init__(self, auth_server_url: str, user_id: str, *, timeout: float = 240.0, poll: float = 0.5) -> None:
        self.base_url = auth_server_url.rstrip("/")
        self.user_id = str(user_id)
        self.timeout = timeout
        self.poll = poll

    def _post(self, payload: dict[str, Any]) -> str:
        body = dict(payload)
        body["account"] = self.user_id
        body.setdefault("kind", "live")
        req = urllib.request.Request(
            f"{self.base_url}/toe/job",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            job = json.loads(resp.read().decode("utf-8"))
        job_id = job.get("id")
        if not job_id:
            raise RuntimeError("auth server did not return a live job id")
        return str(job_id)

    def _wait(self, job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        account = urllib.parse.quote(self.user_id)
        while time.monotonic() < deadline:
            req = urllib.request.Request(
                f"{self.base_url}/toe/job/{job_id}?account={account}",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                job = json.loads(resp.read().decode("utf-8"))
            if job.get("status") == "done":
                result = job.get("result")
                return result if isinstance(result, dict) else {}
            time.sleep(self.poll)
        return {"ok": False, "error": "live job timeout (no result)"}

    def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
        job_id = self._post({"action": "battle", "rivalId": str(rival_id), "titans": [int(t) for t in titans]})
        return self._wait(job_id)


def _score_for(status: dict[str, Any], rival_id: str) -> int | None:
    rivals = status.get("rivals") or {}
    info = rivals.get(str(rival_id)) if isinstance(rivals, dict) else None
    if not isinstance(info, dict):
        return None
    try:
        return int(info.get("attackScore"))
    except (TypeError, ValueError):
        return None


def _remaining(status: dict[str, Any], threshold: int) -> int:
    rivals = status.get("rivals") or {}
    if not isinstance(rivals, dict):
        return 0
    count = 0
    for info in rivals.values():
        if not isinstance(info, dict):
            continue
        try:
            if int(info.get("attackScore")) < threshold:
                count += 1
        except (TypeError, ValueError):
            count += 1
    return count


def run_live_tier(
    client: HWClient,
    *,
    auth_server_url: str,
    user_id: str,
    titans: list[int] | None = None,
    threshold: int = 250,
    max_team_attempts: int | None = None,
    progress: str = "line",
) -> dict[str, Any]:
    """Drive solo/wall ToE battles through the real game client.

    ``max_team_attempts`` caps the rotation teams tried per rival (``None`` =
    the full rotation). Raid-capable tiers are not handled here: callers should
    clear those with the existing hybrid raid path first.
    """
    summary: dict[str, Any] = {
        "rival_results": [],
        "errors": [],
        "raid_errors": [],
        "completed_tier": False,
        "tier": None,
        "remaining_rivals": None,
        "daily_reward": None,
        "live": True,
    }
    if not user_id:
        summary["errors"].append({"stage": "live", "error": "missing x-auth-user-id"})
        print(f"{Emojis.WARNING}Live ToE: missing x-auth-user-id; cannot target a game tab.", flush=True)
        return summary

    live = LiveJobClient(auth_server_url, user_id)
    rotation = _resolve_team_rotation(client, titans)
    if max_team_attempts is not None:
        rotation = rotation[: max(1, int(max_team_attempts))]

    def emit(msg: str) -> None:
        print(msg, flush=True)

    try:
        while True:
            status = _status(client)
            tier = status.get("tier")
            summary["tier"] = tier
            event_status = str(status.get("status") or "")
            if event_status in ("disabled", "peace_time"):
                emit(f"{Emojis.INFO}Live ToE stopped: status={event_status}")
                break
            targets = _select_auto_rivals(status, threshold)
            if not targets:
                if _complete_tier(client, summary, tier):
                    _farm_daily_reward(client, summary)
                    after = _status(client)
                    if str(after.get("tier")) == str(tier):
                        summary["remaining_rivals"] = 0
                        break
                    continue
                summary["remaining_rivals"] = _remaining(status, threshold)
                break
            if status.get("canRaid"):
                summary["errors"].append({"stage": "live", "error": "canRaid tier needs the hybrid raid path"})
                emit(
                    f"{Emojis.WARNING}Live ToE: Tier {tier} canRaid=True; clear it with the hybrid raid "
                    "(`toe run --engine hybrid`) before the live pass."
                )
                break

            emit(f"{Emojis.STEP}Live tier {tier}: {len(targets)} rival(s) to finish, {len(rotation)} team(s) in rotation")
            wall = False
            for rival_id in targets:
                cleared = False
                for team in rotation:
                    emit(f"  - rival {rival_id} team {team}: starting live battle")
                    result = live.battle(str(rival_id), team)
                    ok = bool(result.get("ok"))
                    error = result.get("error")
                    battle_result = result.get("result") if isinstance(result.get("result"), dict) else {}
                    summary["rival_results"].append(
                        {
                            "rivalId": str(rival_id),
                            "win": bool(battle_result.get("win")),
                            "stars": battle_result.get("stars"),
                            "live": True,
                            "ok": ok,
                            "error": error,
                        }
                    )
                    if not ok:
                        summary["errors"].append({"stage": "live_battle", "rival": str(rival_id), "error": error})
                        emit(f"{Emojis.WARNING}  - rival {rival_id}: live job failed ({error})")
                        wall = True
                        break
                    after = _status(client)
                    score = _score_for(after, str(rival_id))
                    emit(
                        f"  - rival {rival_id}: EndBattle ok win={battle_result.get('win')} "
                        f"stars={battle_result.get('stars')} score={score}"
                    )
                    if score is not None and score >= threshold:
                        cleared = True
                        break
                if not cleared:
                    emit(f"{Emojis.WARNING}  - rival {rival_id}: wall (not cleared after {len(rotation)} team(s))")
                    wall = True
                    break
            if wall:
                break
            if _complete_tier(client, summary, summary.get("tier")):
                _farm_daily_reward(client, summary)
                after = _status(client)
                if str(after.get("tier")) == str(summary.get("tier")):
                    summary["remaining_rivals"] = 0
                    break
                continue
            break
    except (KeyboardInterrupt, InterruptedError):
        summary["interrupted"] = True
        raise
    finally:
        try:
            final = _status(client)
            summary["tier"] = final.get("tier")
            summary["remaining_rivals"] = _remaining(final, threshold)
        except Exception:
            pass
    return summary
