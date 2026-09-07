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

    The userscript runs ``Game.BattlePresets/BattleInstantPlay`` in the
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

        try:
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
                    return battle_estimate_from_bridge_result(payload.get("result") or {})
            raise BridgeTimeoutError(f"userscript did not finish battle within {self.timeout}s")
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError(str(exc)) from exc


def battle_estimate_from_bridge_result(envelope: dict[str, Any]) -> BattleEstimate:
    """Unwrap a userscript-submitted bridge result into a :class:`BattleEstimate`.

    The userscript posts ``{"progress": [...], "result": {"win": bool,
    "stars": int}}`` and the auth server stores that object verbatim as the
    job ``result``. Unwrap the inner ``result`` for win/stars and take
    ``progress`` from the envelope top level. A flat ``{"win", "stars",
    "progress"}`` dict is also accepted for backwards compatibility.
    """
    if isinstance(envelope.get("result"), dict):
        inner = envelope["result"]
        progress = envelope.get("progress")
    else:
        inner = envelope
        progress = envelope.get("progress")
    return BattleEstimate(
        win=bool(inner.get("win")),
        stars=int(inner.get("stars", 0)),
        progress=progress or [],
    )


class BridgeError(RuntimeError):
    """Raised when the JS bridge cannot deliver a result."""


class BridgeTimeoutError(BridgeError):
    """Raised when the userscript does not answer the job in time."""


class PlaywrightBattleEngine:
    """Headless browser battle engine via Playwright.

    Launches a headless Chromium, injects the stored ``x-auth-*`` headers,
    navigates to the game, and runs ``Game.BattlePresets/BattleInstantPlay``
    directly in the page context. No manual Titan Arena navigation is required
    – the engine waits for ``window.Game`` to appear and then executes the
    battle. Falls back to :class:`BridgeError` if Playwright is not installed
    or the game does not load within ``timeout``.
    """

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        *,
        headless: bool = True,
        timeout: float = 30.0,
        game_url: str = "https://www.hero-wars.com/",
    ) -> None:
        self.headers = dict(headers or {})
        self.headless = headless
        self.timeout = timeout
        self.game_url = game_url

    def calc(self, battle: dict[str, Any]) -> BattleEstimate:  # pragma: no cover - browser
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise BridgeError(
                "playwright not installed; run `pip install playwright && playwright install chromium`"
            ) from exc

        import json

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                # Isolated context per battle (equiv. to Firefox container)
                context = browser.new_context(extra_http_headers=self.headers or None)
                page = context.new_page()
                page.goto(self.game_url, wait_until="domcontentloaded", timeout=int(self.timeout * 1000))
                # Wait for Haxe Game to appear (poll, cross-origin safe via evaluate)
                try:
                    page.wait_for_function("() => window.Game && window.Game.BattlePresets", timeout=int(self.timeout * 1000))
                except Exception as exc:
                    browser.close()
                    raise BridgeError(f"Game not loaded within {self.timeout}s: {exc}") from exc

                # Run the battle in the page context. The JS mirrors toe-bridge.ts
                # computeBattle (ported from HerowarsHelper's BattleCalc): Haxe
                # signal subscription, NOT DOM addEventListener.
                js_battle = json.dumps(battle)
                result_json = page.evaluate(
                    """(battleJson) => {
                        const getF = (cls, name) => Object.entries(cls.prototype.__properties__ || {}).filter(e => e[1] === name).pop()[0];
                        const getFn = (cls, n) => Object.keys(cls)[n];
                        const getProtoFn = (cls, n) => Object.keys(cls.prototype)[n];
                        const battle = JSON.parse(battleJson);
                        const game = window.Game;
                        if (!game || !game.BattlePresets || !game.BattleInstantPlay || !game.DataStorage) return null;
                        const ds = game.DataStorage;
                        const config = ds[getFn(ds, 25)][getF(game.BattleConfigStorage, 'get_titanPvpManual')]();
                        const presets = new game.BattlePresets(battle.progress || [], false, true, config, false);
                        const instant = new game.BattleInstantPlay(battle, presets);
                        return new Promise((resolve) => {
                            let done = null;
                            instant[getProtoFn(game.BattleInstantPlay, 9)].add((bi) => {
                                const results = bi[getF(game.BattleInstantPlay, 'get_result')]();
                                done = {
                                    progress: results[getF(game.MultiBattleResult, 'get_progress')]() || [],
                                    result: results[getF(game.MultiBattleResult, 'get_result')]() || {win:false, stars:0},
                                };
                                resolve(JSON.stringify(done));
                            });
                            instant.start();
                            setTimeout(() => { if (!done) resolve(null); }, 10000);
                        });
                    }""",
                    js_battle,
                )
                browser.close()
                if not result_json:
                    raise BridgeError("playwright battle engine returned null (engine unavailable or timeout)")
                result = json.loads(result_json) if isinstance(result_json, str) else result_json
                return BattleEstimate(
                    win=bool(result.get("result", {}).get("win", False)),
                    stars=int(result.get("result", {}).get("stars", 0)),
                    progress=result.get("progress") or [],
                )
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError(str(exc)) from exc


def get_default_engine(
    mode: str = "estimate",
    *,
    auth_server_url: str = "http://127.0.0.1:8765",
    user_id: str = "",
    headers: dict[str, str] | None = None,
) -> BattleEngine:
    """Return a battle engine for the given mode.

    * ``estimate``: power-based estimator (no real progress; server will
      reject ``EndBattle`` with ``Invalid battle``).
    * ``hybrid``/``js``/``bridge``: defer to the userscript via the auth
      server job queue. Falls back to the estimator if the bridge fails.
    * ``playwright``: headless Chromium via Playwright, auto-navigates to
      the game and runs the battle. No manual screen operation required.
    """
    if mode in ("estimate", "offline", "python"):
        return PythonBattleEngine()
    if mode in ("playwright", "pw"):
        return PlaywrightBattleEngine(headers=headers)
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
