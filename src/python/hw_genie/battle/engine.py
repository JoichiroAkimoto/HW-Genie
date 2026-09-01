"""Titan battle engine.

Pluggable interface so we can swap implementations:
* :class:`PythonBattleEngine` — pure-Python power-based estimator. Cheap but
  the server rejects its ``progress/result`` as ``Invalid battle`` because it
  re-simulates with its own engine. Use it for tier planning / dry-run only.
* :class:`JsBridgeBattleEngine` — delegates the simulation to the running
  userscript via the auth server job queue. Needs a browser tab open with
  ``hw-genie-toe-bridge.user.js`` loaded.
* :func:`get_default_engine` returns a strategy based on ``mode`` so callers
  can flip between estimate-only and live bridges without code changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class BattleEstimate:
    """Battle outcome with the exact progress payload the server expects."""

    win: bool
    stars: int
    progress: list[dict[str, Any]]


class BattleEngine(Protocol):
    """Simulate a titan arena battle and return the server-valid progress."""

    def calc(self, battle: dict[str, Any]) -> BattleEstimate:  # pragma: no cover - protocol
        ...


class PythonBattleEngine:
    """Deterministic power-based estimator (no real combat sim)."""

    def calc(self, battle: dict[str, Any]) -> BattleEstimate:
        attackers = battle.get("attackers", {})
        defenders = battle.get("defenders", [{}])
        def_team = defenders[0] if defenders else {}
        if not isinstance(attackers, dict):
            attackers = {}
        if not isinstance(def_team, dict):
            def_team = {}

        def team_power(team: dict) -> float:
            total = 0.0
            for v in team.values():
                if not isinstance(v, dict):
                    continue
                p = v.get("power")
                if isinstance(p, (int, float)):
                    total += float(p)
                else:
                    hp = v.get("hp")
                    if isinstance(hp, (int, float)):
                        total += float(hp) / 60.0
            return total

        atk = team_power(attackers)
        dfn = team_power(def_team)
        win = dfn == 0 or atk > dfn * 0.85
        return BattleEstimate(
            win=win,
            stars=3 if win else 0,
            progress=_build_progress(battle, win),
        )


def _build_progress(battle: dict[str, Any], win: bool) -> list[dict[str, Any]]:
    attackers = battle.get("attackers", {}) if isinstance(battle.get("attackers"), dict) else {}
    defenders = battle.get("defenders", [{}])
    def_team = defenders[0] if defenders and isinstance(defenders[0], dict) else {}

    def hero_state(h: dict, alive: bool) -> dict[str, Any]:
        hp = h.get("hp", 10000)
        try:
            hp_val = float(hp)
        except Exception:
            hp_val = 10000.0
        return {
            "hp": hp_val * (0.5 if alive else 0.0),
            "energy": 800 if alive else 0,
            "isDead": not alive,
        }

    return [
        {
            "attackers": {"heroes": {k: hero_state(v, win) for k, v in attackers.items() if isinstance(v, dict)}},
            "defenders": {"heroes": {k: hero_state(v, not win) for k, v in def_team.items() if isinstance(v, dict)}},
        }
    ]


class JsBridgeBattleEngine:
    """Delegate the simulation to the userscript via the auth server job queue.

    The userscript runs ``Game.BattleCalc(battle, get_titanPvpManual)`` in the
    browser and posts the exact ``progress/result`` back. Until that job
    comes back we raise :class:`BridgeTimeoutError` so the caller can fall
    back to the Python estimator or abort the tier.
    """

    def __init__(self, auth_server_url: str, user_id: str, poll_interval: float = 1.0, timeout: float = 30.0):
        self.auth_server_url = auth_server_url.rstrip("/")
        self.user_id = user_id
        self.poll_interval = poll_interval
        self.timeout = timeout

    def calc(self, battle: dict[str, Any]) -> BattleEstimate:  # pragma: no cover - network
        import json
        import time
        import urllib.error
        import urllib.request

        job_body = json.dumps({"account": self.user_id, "battle": battle}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.auth_server_url}/toe/job",
            data=job_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            job = json.loads(resp.read().decode("utf-8"))
        job_id = job.get("id")
        if not job_id:
            raise BridgeError("auth server did not return a job id")

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            poll_req = urllib.request.Request(
                f"{self.auth_server_url}/toe/job/{job_id}?account={self.user_id}",
                method="GET",
            )
            with urllib.request.urlopen(poll_req, timeout=5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("status") == "done":
                result = payload.get("result") or {}
                return BattleEstimate(
                    win=bool(result.get("win")),
                    stars=int(result.get("stars", 0)),
                    progress=result.get("progress") or [],
                )
        raise BridgeTimeoutError(f"userscript did not finish battle within {self.timeout}s")


class BridgeError(RuntimeError):
    """Raised when the JS bridge cannot deliver a result."""


class BridgeTimeoutError(BridgeError):
    """Raised when the userscript does not answer the job in time."""


def get_default_engine(mode: str = "estimate", *, auth_server_url: str = "http://127.0.0.1:8765", user_id: str = "") -> BattleEngine:
    """Return a battle engine for the given mode.

    * ``estimate``: power-based estimator (no real progress; server will
      reject ``EndBattle`` with ``Invalid battle``).
    * ``hybrid``/``js``/``bridge``: defer to the userscript via the auth
      server job queue. Falls back to the estimator if the bridge fails.
    """
    if mode in ("estimate", "offline", "python"):
        return PythonBattleEngine()
    return JsBridgeBattleEngine(auth_server_url=auth_server_url, user_id=user_id)


# Backwards-compatible helper used by the single-rival `toe attack` flow.
def estimate_battle(battle: dict[str, Any]) -> dict[str, Any]:
    """Return the dict shape the previous implementation exposed."""
    est = PythonBattleEngine().calc(battle)
    return {
        "win": est.win,
        "stars": est.stars,
        "progress": est.progress,
    }
