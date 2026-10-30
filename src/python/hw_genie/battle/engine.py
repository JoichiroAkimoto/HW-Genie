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
from typing import Any, Callable, Protocol


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


def _cancel_requested() -> bool:
    """True when a cooperative Ctrl+C cancellation was requested."""
    try:
        from hw_genie.runner import is_cancelled

        return bool(is_cancelled())
    except Exception:
        return False


@dataclass
class BridgeTiming:
    """Wall-clock breakdown of one bridge job (all values in seconds).

    ``queue_wait`` / ``sim`` / ``poll_lag`` come from the auth server's job
    timestamps, so they need no userscript-side instrumentation.
    """

    post_rtt: float
    queue_wait: float | None
    sim: float | None
    poll_lag: float | None
    total: float


class JsBridgeBattleEngine:
    """Delegate the simulation to the userscript via the auth server job queue.

    The userscript runs ``Game.BattlePresets/BattleInstantPlay`` in the
    browser and posts the exact ``progress/result`` back. Until that job
    comes back we raise :class:`BridgeTimeoutError` so the caller can fall
    back to the Python estimator or abort the tier.

    ``timings`` accumulates one :class:`BridgeTiming` per finished job so
    callers can report where the wall clock actually goes; see
    :meth:`timing_summary`.
    """

    def __init__(self, auth_server_url: str, user_id: str, poll_interval: float = 0.1, timeout: float = 30.0,
                 heartbeat_interval: float = 10.0):
        self.auth_server_url = auth_server_url.rstrip("/")
        self.user_id = user_id
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.heartbeat_interval = heartbeat_interval
        self.timings: list[BridgeTiming] = []

    # -- job plumbing ----------------------------------------------------

    def _post_job(self, battle: dict[str, Any]) -> tuple[str, float, float]:
        """POST one battle; return ``(job_id, posted_at, post_rtt)``."""
        import json
        import time
        import urllib.request

        posted_at = time.time()
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
        return str(job_id), posted_at, time.time() - posted_at

    def _fetch_job(self, job_id: str) -> dict[str, Any]:
        import json
        import urllib.request

        poll_req = urllib.request.Request(
            f"{self.auth_server_url}/toe/job/{job_id}?account={self.user_id}",
            method="GET",
        )
        with urllib.request.urlopen(poll_req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _record_timing(self, payload: dict[str, Any], post_rtt: float, total: float) -> None:
        """Store one job's breakdown from the auth server timestamps."""
        import time

        created = _safe_float(payload.get("created_at"))
        claimed = _safe_float(payload.get("claimed_at"))
        finished = _safe_float(payload.get("finished_at"))
        queue_wait = claimed - created if created is not None and claimed is not None else None
        sim = finished - claimed if claimed is not None and finished is not None else None
        poll_lag = time.time() - finished if finished is not None else None
        self.timings.append(
            BridgeTiming(
                post_rtt=post_rtt,
                queue_wait=queue_wait,
                sim=sim,
                poll_lag=poll_lag,
                total=total,
            )
        )

    def timing_summary(self) -> str | None:
        """One-line average breakdown of this engine's bridge jobs."""
        if not self.timings:
            return None

        def avg(values: list[float | None]) -> str:
            known = [v for v in values if v is not None]
            if not known:
                return "-"
            return f"{sum(known) / len(known):.2f}s"

        n = len(self.timings)
        return (
            f"ToE bridge timing ({n} job{'s' if n != 1 else ''}, avg): "
            f"post {avg([t.post_rtt for t in self.timings])} | "
            f"queue {avg([t.queue_wait for t in self.timings])} | "
            f"sim {avg([t.sim for t in self.timings])} | "
            f"poll {avg([t.poll_lag for t in self.timings])} | "
            f"total {avg([t.total for t in self.timings])}"
        )

    # -- public API ------------------------------------------------------

    def calc_batch(
        self,
        battles: list[dict[str, Any]],
        on_progress: Callable[[str], None] | None = None,
    ) -> list[BattleEstimate | BaseException]:  # pragma: no cover - network
        """Simulate several battles with all jobs queued up front.

        The userscript drains its queue in one tick, so submitting every raid
        battle before polling removes the per-rival poll interval from the
        critical path. Per-battle failures are returned in place (as
        :class:`BridgeError` / :class:`BridgeTimeoutError`) so the caller can
        skip just those rivals, exactly like the sequential path.
        """
        if not battles:
            return []
        import time

        try:
            results: list[BattleEstimate | BaseException | None] = [None] * len(battles)
            pending: dict[str, tuple[int, float, float]] = {}
            for index, battle in enumerate(battles):
                if _cancel_requested():
                    raise InterruptedError("interrupted by user")
                try:
                    job_id, posted_at, post_rtt = self._post_job(battle)
                except (KeyboardInterrupt, InterruptedError):
                    raise
                except BridgeError as exc:
                    results[index] = exc
                    continue
                except Exception as exc:
                    results[index] = BridgeError(str(exc))
                    continue
                pending[job_id] = (index, posted_at, post_rtt)

            now = time.time()
            next_heartbeat = now + self.heartbeat_interval if self.heartbeat_interval > 0 else float("inf")
            # Jobs are posted up front, so an unclaimed job has no meaningful
            # clock (the userscript drains at most a few per tick, each sim can
            # take seconds). Instead of a per-job deadline, track when the
            # queue last made progress and fail the whole queue only when it
            # stalls for ``timeout``. A claimed job stays bounded by
            # ``claimed_at + timeout`` in case it never finishes.
            last_progress_at = now
            claimed_seen: set[str] = set()
            while pending:
                if _cancel_requested():
                    raise InterruptedError("interrupted by user")
                time.sleep(self.poll_interval)
                if _cancel_requested():
                    raise InterruptedError("interrupted by user")
                now = time.time()
                if now >= next_heartbeat:
                    if on_progress is not None:
                        on_progress(self._heartbeat_text(pending, now))
                    next_heartbeat = now + self.heartbeat_interval
                for job_id, (index, posted_at, post_rtt) in list(pending.items()):
                    try:
                        payload = self._fetch_job(job_id)
                        if not isinstance(payload, dict):
                            raise BridgeError("auth server returned a malformed job payload")
                        if payload.get("status") == "done":
                            detected_at = time.time()
                            self._record_timing(payload, post_rtt, detected_at - posted_at)
                            results[index] = battle_estimate_from_bridge_result(
                                payload.get("result") or {}
                            )
                            pending.pop(job_id, None)
                            last_progress_at = detected_at
                        else:
                            claimed_at = _safe_float(payload.get("claimed_at"))
                            if claimed_at is None:
                                # Still queued: no per-job clock until the
                                # userscript claims it (see above).
                                continue
                            if job_id not in claimed_seen:
                                # A claim refreshes queue progress once; a
                                # claimed job that never finishes is still
                                # bounded by its own deadline below.
                                claimed_seen.add(job_id)
                                last_progress_at = time.time()
                            if time.time() - claimed_at >= self.timeout:
                                results[index] = BridgeTimeoutError(
                                    f"userscript did not finish battle within {self.timeout}s"
                                )
                                pending.pop(job_id, None)
                                last_progress_at = time.time()
                    except (KeyboardInterrupt, InterruptedError):
                        raise
                    except Exception as exc:
                        # A failed fetch, malformed payload or decode error is
                        # this job's problem only; the rest still finish.
                        results[index] = (
                            exc if isinstance(exc, BridgeError) else BridgeError(str(exc))
                        )
                        pending.pop(job_id, None)
                        last_progress_at = time.time()
                if pending and time.time() - last_progress_at >= self.timeout:
                    # No claim, finish or failure for a full timeout: the
                    # queue itself is stalled (bridge dead), not merely slow.
                    for index, _posted_at, _post_rtt in pending.values():
                        results[index] = BridgeTimeoutError(
                            f"userscript did not finish battle within {self.timeout}s "
                            "(job queue stalled)"
                        )
                    pending.clear()
            return [r if r is not None else BridgeError("job finished without a result") for r in results]
        except (KeyboardInterrupt, InterruptedError):
            raise
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError(str(exc)) from exc

    def _heartbeat_text(self, pending: dict[str, tuple[int, float, float]], now: float) -> str:
        """Progress line for a stalled bridge (single job keeps the legacy text)."""
        if len(pending) == 1:
            job_id, (_, posted_at, _) = next(iter(pending.items()))
            return (
                f"  … waiting for userscript job {job_id[:8]} "
                f"({now - posted_at:.0f}s elapsed, timeout {self.timeout:.0f}s)…"
            )
        oldest = min(posted_at for _, posted_at, _ in pending.values())
        return (
            f"  … waiting for {len(pending)} userscript jobs "
            f"({now - oldest:.0f}s elapsed, timeout {self.timeout:.0f}s)…"
        )

    def calc(
        self,
        battle: dict[str, Any],
        on_progress: Callable[[str], None] | None = None,
    ) -> BattleEstimate:  # pragma: no cover - network
        """Simulate one battle, re-raising the batch result's failure."""
        result = self.calc_batch([battle], on_progress=on_progress)[0]
        if isinstance(result, BaseException):
            raise result
        return result


def battle_estimate_from_bridge_result(envelope: dict[str, Any]) -> BattleEstimate:
    """Unwrap a userscript-submitted bridge result into a :class:`BattleEstimate`.

    The userscript posts ``{"progress": [...], "result": {"win": bool,
    "stars": int}}`` and the auth server stores that object verbatim as the
    job ``result``. Unwrap the inner ``result`` for win/stars and take
    ``progress`` from the envelope top level. A flat ``{"win", "stars",
    "progress"}`` dict is also accepted for backwards compatibility.
    """
    inner = envelope.get("result") if isinstance(envelope.get("result"), dict) else envelope
    if not isinstance(inner, dict):
        inner = {}
    progress = envelope.get("progress")
    return BattleEstimate(
        win=bool(inner.get("win")),
        stars=_safe_int(inner.get("stars")),
        progress=progress if isinstance(progress, list) else [],
    )


def _safe_float(value: Any) -> float | None:
    """Best-effort float parse for bridge job timestamps (None on failure)."""
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    """Best-effort int parse for bridge ``stars`` (None/str → 0, never raises)."""
    try:
        if value is None:
            return 0
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class BridgeError(RuntimeError):
    """Raised when the JS bridge cannot deliver a result."""


class BridgeTimeoutError(BridgeError):
    """Raised when the userscript does not answer the job in time."""


class BridgeDeadError(BridgeError):
    """Raised when the JS bridge is considered dead (consecutive timeouts).

    Carries the partial per-rival results collected before the outage so
    callers can still report progress while aborting the tier loop.
    """

    def __init__(self, message: str, partial_results: list | None = None) -> None:
        super().__init__(message)
        self.partial_results: list = list(partial_results or [])


#: Battle sim snippet evaluated in the headless page by
#: :meth:`PlaywrightBattleEngine.calc`. Kept module-level so tests can pin
#: its error handling. The ``getF``/``getFn``/``getProtoFn`` helpers resolve
#: Haxe-minified names and must throw a descriptive error (naming the missing
#: key) when resolution fails — otherwise a game update surfaces only as
#: ``TypeError: Cannot read properties of undefined`` inside ``evaluate``.
PLAYWRIGHT_BATTLE_JS = """(arg) => {
                        const battleJson = typeof arg === "string" ? arg : arg.battle;
                        const configGetter = (typeof arg === "string" ? null : arg.configGetter) || "get_titanPvpManual";
                        const getF = (cls, name) => {
                            const found = Object.entries(cls.prototype.__properties__ || {}).filter(e => e[1] === name).pop();
                            if (!found) throw new Error("Property " + name + " not found in Haxe class prototype");
                            return found[0];
                        };
                        const getFn = (cls, n) => {
                            const key = Object.keys(cls)[n];
                            if (key === undefined) throw new Error("Ordinal " + n + " out of range in Haxe class keys");
                            return key;
                        };
                        const getProtoFn = (cls, n) => {
                            const key = Object.keys(cls.prototype)[n];
                            if (key === undefined) throw new Error("Ordinal " + n + " out of range in Haxe prototype keys");
                            return key;
                        };
                        const battle = JSON.parse(battleJson);
                        const game = window.Game;
                        if (!game || !game.BattlePresets || !game.BattleInstantPlay || !game.DataStorage) return null;
                        const ds = game.DataStorage;
                        const config = ds[getFn(ds, 25)][getF(game.BattleConfigStorage, configGetter)]();
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
                    }"""


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
                try:
                    # Isolated context per battle (equiv. to Firefox container)
                    context = browser.new_context(extra_http_headers=self.headers or None)
                    page = context.new_page()
                    page.goto(self.game_url, wait_until="domcontentloaded", timeout=int(self.timeout * 1000))
                    # Wait for Haxe Game to appear (poll, cross-origin safe via evaluate)
                    try:
                        page.wait_for_function("() => window.Game && window.Game.BattlePresets", timeout=int(self.timeout * 1000))
                    except Exception as exc:
                        raise BridgeError(f"Game not loaded within {self.timeout}s: {exc}") from exc

                    # Run the battle in the page context. The JS mirrors toe-bridge.ts
                    # computeBattle (ported from HerowarsHelper's BattleCalc): Haxe
                    # signal subscription, NOT DOM addEventListener.
                    # NOTE: Haxe minified ordinals below (DataStorage key 25,
                    # BattleInstantPlay signal 9) are brittle across game builds.
                    # They were ported from HerowarsHelper's working BattleCalc;
                    # re-validate against that source when resolution throws
                    # (the page.evaluate error below carries the failing key).
                    js_battle = json.dumps(battle)
                    result_json = page.evaluate(
                        PLAYWRIGHT_BATTLE_JS,
                        js_battle,
                    )
                    if not result_json:
                        raise BridgeError("playwright battle engine returned null (engine unavailable or timeout)")
                    result = json.loads(result_json) if isinstance(result_json, str) else result_json
                    return BattleEstimate(
                        win=bool(result.get("result", {}).get("win", False)),
                        stars=_safe_int(result.get("result", {}).get("stars", 0)),
                        progress=result.get("progress") or [],
                    )
                finally:
                    browser.close()
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError(str(exc)) from exc


CONFIG_GETTERS_JS = """() => {
                        const g = window.Game;
                        if (!g || !g.BattleConfigStorage) return null;
                        const cls = g.BattleConfigStorage;
                        const props = cls.prototype && cls.prototype.__properties__
                            ? Object.entries(cls.prototype.__properties__).map(([k, v]) => k + "=" + v)
                            : [];
                        return JSON.stringify({staticKeys: Object.keys(cls), properties: props});
                    }"""


class PersistentPlaywrightBattleEngine:
    """Reuse one headless game page across many battles.

    ``PlaywrightBattleEngine`` launches and closes Chromium for every battle,
    which is slow and fragments memory. This engine keeps a single page alive,
    recycles it every ``recycle_every`` battles, and must be closed by the
    caller. It is not thread-safe.
    """

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        *,
        headless: bool = True,
        timeout: float = 30.0,
        game_url: str = "https://www.hero-wars.com/",
        recycle_every: int = 100,
        config_getter: str = "get_titanPvpManual",
    ) -> None:
        self.headers = dict(headers or {})
        self.headless = headless
        self.timeout = timeout
        self.game_url = game_url
        self.recycle_every = max(0, int(recycle_every))
        self.config_getter = config_getter
        self.engine_name = "oracle-playwright"
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        # Counts every calc() call, including failures: a failed attempt may
        # leave the page in an unknown state, so it must advance the recycle
        # schedule exactly like a success.
        self._attempts = 0

    def _ensure_page(self):  # pragma: no cover - browser
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError as exc:
            raise BridgeError(
                "playwright not installed; run `pip install playwright && playwright install chromium`"
            ) from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                extra_http_headers=self.headers or None
            )
            self._page = self._context.new_page()
            self._page.goto(
                self.game_url,
                wait_until="domcontentloaded",
                timeout=int(self.timeout * 1000),
            )
            self._page.wait_for_function(
                "() => window.Game && window.Game.BattlePresets",
                timeout=int(self.timeout * 1000),
            )
        except Exception as exc:
            self.close()
            raise BridgeError(
                f"failed to start persistent Playwright page: {exc}"
            ) from exc
        return self._page

    def calc(self, battle: dict[str, Any]) -> BattleEstimate:  # pragma: no cover - browser
        import json

        self._attempts += 1
        page = self._ensure_page()
        try:
            result_json = page.evaluate(
                PLAYWRIGHT_BATTLE_JS,
                {"battle": json.dumps(battle), "configGetter": self.config_getter},
            )
        except Exception as exc:
            self.close()
            raise BridgeError(str(exc)) from exc
        if not result_json:
            # A null result usually means the game failed to load or the sim
            # timed out; the page may be suspect, so drop it before raising.
            self.close()
            raise BridgeError(
                "playwright battle engine returned null (engine unavailable or timeout)"
            )
        try:
            result = (
                json.loads(result_json)
                if isinstance(result_json, str)
                else result_json
            )
        except Exception as exc:
            self.close()
            raise BridgeError(f"invalid oracle response: {exc}") from exc
        estimate = BattleEstimate(
            win=bool(result.get("result", {}).get("win", False)),
            stars=_safe_int(result.get("result", {}).get("stars", 0)),
            progress=result.get("progress") or [],
        )
        if self.recycle_every and self._attempts % self.recycle_every == 0:
            self.close()
        return estimate

    def close(self) -> None:
        for obj in (self._context, self._browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    def list_config_getters(self) -> dict[str, Any]:  # pragma: no cover - browser
        """Return ``BattleConfigStorage`` getter names for the loaded build."""
        import json

        page = self._ensure_page()
        try:
            raw = page.evaluate(CONFIG_GETTERS_JS)
            if not raw:
                raise BridgeError(
                    "could not read BattleConfigStorage from the game page"
                )
            return json.loads(raw) if isinstance(raw, str) else raw
        except BridgeError:
            raise
        except Exception as exc:
            raise BridgeError(str(exc)) from exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


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

    The persistent (``oracle``) engine is not exposed here: ``battle
    simulate`` instantiates :class:`PersistentPlaywrightBattleEngine` itself
    so it can close the long-lived page deterministically.
    """
    if mode in ("estimate", "offline", "python"):
        return PythonBattleEngine()
    if mode == "playwright":
        return PlaywrightBattleEngine(headers=headers)
    return JsBridgeBattleEngine(auth_server_url=auth_server_url, user_id=user_id)
