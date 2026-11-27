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
* ``action:"reload"`` reloads the tab once after ``titanArenaCompleteTier`` so
  the in-page ToE model picks up the new tier. Battles themselves do not reload
  the page (the result popup is left to the game).

This module posts those jobs and drives the tier loop: for each unfinished
rival it tries the team rotation, reads the authoritative server status after
each battle, banks progress, and calls ``titanArenaCompleteTier`` when every
rival reaches the threshold. Raid-capable tiers are not handled here (run the
hybrid raid first, then re-run live for any remaining walls). The game tab must
stay visible/rendering while a battle runs (the login/battle loop needs frames).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from hw_genie.core.client import Emojis, HWClient, load_session_headers
from hw_genie.commands.titan_arena import (
    _complete_tier,
    _farm_daily_reward,
    _resolve_team_rotation,
    _select_auto_rivals,
    _tier_key,
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
        last_transient: str | None = None
        while time.monotonic() < deadline:
            req = urllib.request.Request(
                f"{self.base_url}/toe/job/{job_id}?account={account}",
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    job = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # 4xx/5xx is an authoritative answer (e.g. 404 after TTL).
                return {"ok": False, "error": f"live job poll failed: HTTP {exc.code}"}
            except (urllib.error.URLError, OSError, ValueError) as exc:
                # Transient network/parse trouble: keep polling until the
                # deadline instead of abandoning a battle that may still be
                # running in the tab.
                last_transient = str(exc)
                time.sleep(self.poll)
                continue
            if job.get("status") == "done":
                result = job.get("result")
                return result if isinstance(result, dict) else {}
            time.sleep(self.poll)
        if last_transient:
            return {
                "ok": False,
                "error": f"live job timeout (no result); last poll error: {last_transient}",
            }
        return {"ok": False, "error": "live job timeout (no result)"}

    def battle(self, rival_id: str, titans: list[int]) -> dict[str, Any]:
        job_id = self._post({"action": "battle", "rivalId": str(rival_id), "titans": [int(t) for t in titans]})
        return self._wait(job_id)

    def reload(self) -> dict[str, Any]:
        """Reload the account's game tab so it picks up the advanced tier.

        Called once after ``titanArenaCompleteTier``; the tab's in-page ToE model
        otherwise keeps the just-completed tier's rivals.
        """
        job_id = self._post({"action": "reload"})
        return self._wait(job_id)


# Errors that are transient right after the userscript reloads the page (1.5s
# after a tier transition): the model/rival list may not be populated yet.
# Re-posting the job a moment later lands on the now-loaded page and succeeds.
_RETRYABLE_LIVE_ERRORS = ("rival not found", "toe model unavailable")

# Fatal live errors abort the whole tier (wrong tab / dead engine); everything
# else is per-team or per-rival. A battle/job timeout only kills that rival.
_FATAL_LIVE_ERRORS = ("account mismatch", "rival not found", "no engine", "toe model unavailable")


def _battle_with_retry(
    live: LiveJobClient,
    rival_id: str,
    team: list[int],
    *,
    attempts: int = 2,
    delay: float = 4.0,
) -> dict[str, Any]:
    result = live.battle(str(rival_id), team)
    for _ in range(1, max(1, attempts)):
        if result.get("ok"):
            break
        err = str(result.get("error") or "").lower()
        if not any(marker in err for marker in _RETRYABLE_LIVE_ERRORS):
            break
        time.sleep(delay)
        result = live.battle(str(rival_id), team)
    return result


def _score_for(status: dict[str, Any], rival_id: str) -> tuple[bool, int | None]:
    """Return ``(rival_present, attack_score)``.

    ``(False, None)`` means the rival key is absent (cleared / tier advanced).
    ``(True, None)`` means the key is present but the score is missing or
    unparsable — the caller must not treat that as cleared.
    """
    rivals = status.get("rivals") or {}
    if not isinstance(rivals, dict):
        return False, None
    key = str(rival_id)
    if key not in rivals:
        return False, None
    info = rivals.get(key)
    if not isinstance(info, dict):
        return True, None
    try:
        return True, int(info.get("attackScore"))
    except (TypeError, ValueError):
        return True, None


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
    rounds: int = 1,
    account_alias: str | None = None,
) -> dict[str, Any]:
    """Drive solo/wall ToE battles through the real game client.

    ``max_team_attempts`` caps the rotation teams tried per rival per round
    (``None`` = the full rotation). ``rounds`` repeats the full rotation while
    any rival is still below ``threshold`` (each battle re-rolls the seed, and
    attackScore is max-held, so extra rounds are harmless and can push a
    near-miss over 250). Raid-capable tiers are not handled here: the driver
    stops with an explicit instruction to run the hybrid raid first, because
    the live path cannot produce raid results.
    """
    summary: dict[str, Any] = {
        "rival_results": [],
        "errors": [],
        "raid_errors": [],
        "completed_tier": False,
        "tier": None,
        "final_tier": None,
        "remaining_rivals": None,
        "daily_reward": None,
        # Non-failing marker for the expected two-step raid → live workflow.
        "action_required": [],
        "live": True,
    }
    if not user_id:
        summary["errors"].append({"stage": "live", "error": "missing x-auth-user-id"})
        print(f"{Emojis.WARNING}Live ToE: missing x-auth-user-id; cannot target a game tab.", flush=True)
        return summary

    live = LiveJobClient(auth_server_url, user_id)

    def resolve_rotation() -> list[list[int]]:
        try:
            return _resolve_team_rotation(client, titans)
        except RuntimeError:
            # Fall back only for a transient teamGetAll failure. A successful
            # (but empty) team list means the account has no saved team; the
            # static rotation would be unowned guesses, so re-raise.
            probe = client.call(
                {"calls": [{"name": "teamGetAll", "args": {}, "ident": "body"}]},
                max_retries=0,
            )
            if probe.is_success:
                raise
            from hw_genie.commands.titan_arena import STATIC_TEAM_ROTATION

            return [[int(t) for t in team] for team in STATIC_TEAM_ROTATION]

    rotation = resolve_rotation()
    if max_team_attempts is not None:
        rotation = rotation[: max(1, int(max_team_attempts))]
    retry_rounds = max(1, int(rounds))
    rounds_done = 0
    no_tier = object()
    last_tier: Any = no_tier

    def emit(msg: str) -> None:
        print(msg, flush=True)

    def refresh_auth() -> None:
        """Pick up the userscript-refreshed token before each game API call.

        The game rotates ``x-auth-token`` every few minutes while the tab is
        active; a sweep that holds the process-start token fails its first
        status read after a rotation. Re-reading the DB right before the call
        makes the CLI follow the rotation instead of racing it.
        """
        if not account_alias:
            return
        try:
            fresh = load_session_headers(account_alias, fresh=True)
        except Exception:
            return
        if fresh:
            client.update_headers(fresh)

    def read_status() -> dict[str, Any]:
        refresh_auth()
        return _status(client)

    def reload_tab() -> None:
        """Reload the game tab once after CompleteTier so it sees the new tier."""
        try:
            result = live.reload()
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        if not result.get("ok"):
            error = str(result.get("error") or "reload failed")
            summary["errors"].append({"stage": "live_reload", "error": error})
            emit(
                f"{Emojis.WARNING}Live ToE: tab reload failed ({error}); "
                "the tab may still show the previous tier."
            )

    try:
        while True:
            status = read_status()
            tier = status.get("tier")
            tier_key = _tier_key(tier)
            if last_tier is no_tier or str(tier) != str(last_tier):
                rounds_done = 0
                last_tier = tier
            summary["tier"] = tier
            event_status = str(status.get("status") or "")
            if event_status in ("disabled", "peace_time"):
                emit(f"{Emojis.INFO}Live ToE stopped: status={event_status}")
                break
            targets = _select_auto_rivals(status, threshold)
            if not targets:
                refresh_auth()
                if summary.get("completed_tier") and summary.get("completed_tier_key") == tier_key:
                    # CompleteTier already succeeded for this exact tier and
                    # the authoritative status has not advanced yet. Do not
                    # send a duplicate: reload once and let the status catch up.
                    reload_tab()
                    after = read_status()
                    if str(after.get("tier")) == str(tier):
                        summary["remaining_rivals"] = 0
                        break
                    continue
                if _complete_tier(client, summary, tier):
                    reload_tab()
                    _farm_daily_reward(client, summary)
                    after = read_status()
                    if str(after.get("tier")) == str(tier):
                        summary["remaining_rivals"] = 0
                        break
                    continue
                summary["remaining_rivals"] = _remaining(status, threshold)
                break
            if status.get("canRaid"):
                # The live path cannot produce a raid result. Do it with the
                # hybrid engine first (it clears the raid and leaves only
                # walls), then re-run live for whatever remains.
                message = (
                    f"Tier {tier} has a raid available (canRaid=True). Run "
                    f"`hw-genie toe run -a <account> --engine hybrid` first, then re-run "
                    "`--engine live` for any remaining walls."
                )
                # Not an error: this is the documented two-step workflow, so it
                # must not make `multi toe` exit 1.
                summary["action_required"].append({"stage": "live", "message": message, "tier": tier})
                emit(f"{Emojis.WARNING}Live ToE: {message}")
                summary["remaining_rivals"] = _remaining(status, threshold)
                break

            emit(
                f"{Emojis.STEP}Live tier {tier}: {len(targets)} rival(s) to finish, "
                f"{len(rotation)} team(s) in rotation (round {rounds_done + 1}/{retry_rounds})"
            )
            wall = False
            fatal_stop = False
            for rival_id in targets:
                cleared = False
                for team in rotation:
                    emit(f"  - rival {rival_id} team {team}: starting live battle")
                    result = _battle_with_retry(live, str(rival_id), team)
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
                        err_text = str(error or "")
                        lowered = err_text.lower()
                        if "live battle timeout" in lowered or "live job timeout" in lowered:
                            # A battle that never finished, or a job the tab
                            # never picked up (closed/hidden tab), only kills
                            # this rival. Record it once and stop retrying it
                            # with the remaining teams — otherwise a dead tab
                            # burns 240s per team for the whole rotation.
                            summary["errors"].append(
                                {"stage": "live_battle", "rival": str(rival_id), "error": error}
                            )
                            emit(
                                f"{Emojis.WARNING}  - rival {rival_id}: live job did not finish "
                                "(timeout); skipping this rival"
                            )
                            wall = True
                            break
                        fatal = any(marker in lowered for marker in _FATAL_LIVE_ERRORS)
                        if fatal:
                            summary["errors"].append({"stage": "live_battle", "rival": str(rival_id), "error": error})
                            emit(f"{Emojis.WARNING}  - rival {rival_id}: live job failed ({error})")
                            wall = True
                            fatal_stop = True
                            break
                        # StartBattle errors (unowned/invalid team, transient server
                        # rejection) are per-team: keep rotating instead of
                        # treating the rival as a wall.
                        emit(f"{Emojis.INFO}  - rival {rival_id}: team skipped ({err_text or 'no error text'})")
                        continue
                    after = read_status()
                    present, score = _score_for(after, str(rival_id))
                    emit(
                        f"  - rival {rival_id}: EndBattle ok win={battle_result.get('win')} "
                        f"stars={battle_result.get('stars')} score={score}"
                    )
                    if not present:
                        # The rival is gone: either it was cleared or the
                        # authoritative status advanced. Stop firing teams at it.
                        cleared = True
                        break
                    if score is None:
                        # Present but unreadable: do not treat it as cleared.
                        summary["errors"].append(
                            {
                                "stage": "live_score",
                                "rival": str(rival_id),
                                "error": "attackScore missing/unparsable",
                            }
                        )
                        emit(
                            f"{Emojis.WARNING}  - rival {rival_id}: attackScore missing/unparsable; "
                            "not treating as cleared"
                        )
                        continue
                    if score >= threshold:
                        cleared = True
                        break
                if fatal_stop:
                    break
                if not cleared:
                    emit(f"{Emojis.WARNING}  - rival {rival_id}: wall (not cleared after {len(rotation)} team(s))")
                    wall = True
                    # Do not break: sweep every under-threshold rival each round
                    # instead of stopping at the first wall. attackScore is
                    # max-held, so trying all candidates per round is harmless.
                    continue
            if wall:
                # Retry the whole rotation while rounds remain. attackScore is
                # max-held and each battle re-rolls the seed, so persisting can
                # push a near-miss (e.g. 249) over the threshold harmlessly.
                if fatal_stop or rounds_done + 1 >= retry_rounds:
                    break
                rounds_done += 1
                emit(
                    f"{Emojis.INFO}Live ToE: round {rounds_done}/{retry_rounds} ended with uncleared "
                    "rivals; retrying the full rotation."
                )
                time.sleep(3)
                continue
            refresh_auth()
            # CompleteTier is always the advancing call; completeness is
            # confirmed with the real tier key, never with an invented status.
            if _complete_tier(client, summary, summary.get("tier")):
                reload_tab()
                _farm_daily_reward(client, summary)
                after = read_status()
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
            final = read_status()
            summary["tier"] = final.get("tier")
            summary["final_tier"] = final.get("tier")
            summary["remaining_rivals"] = _remaining(final, threshold)
        except Exception:
            pass
    return summary
