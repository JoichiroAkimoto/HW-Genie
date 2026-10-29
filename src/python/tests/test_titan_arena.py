from unittest.mock import MagicMock


from hw_genie.battle.engine import (
    BattleEstimate,
    PythonBattleEngine,
    battle_estimate_from_bridge_result,
    get_default_engine,
)


def _ok(detail: dict) -> MagicMock:
    res = MagicMock()
    res.is_success = True
    res.detail = detail
    return res


def _err(name: str = "NotAvailable", detail: dict | None = None) -> MagicMock:
    res = MagicMock()
    res.is_success = False
    res.error_name = name
    res.detail = detail or {}
    return res


# ---------------------------------------------------------------------------
# Battle engine
# ---------------------------------------------------------------------------


def test_python_engine_estimates_win_for_stronger_attackers():
    battle = {
        "attackers": {
            "4003": {"id": 4003, "power": 200_000, "hp": 10_000_000},
        },
        "defenders": [
            {
                "4000": {"id": 4000, "power": 10_000, "hp": 1_000_000},
            }
        ],
    }
    est = PythonBattleEngine().calc(battle)
    assert isinstance(est, BattleEstimate)
    assert est.win is True
    assert est.stars == 3
    assert est.progress[0]["attackers"]["heroes"]["4003"]["isDead"] is False
    assert est.progress[0]["defenders"]["heroes"]["4000"]["isDead"] is True


def test_python_engine_estimates_loss_for_weaker_attackers():
    battle = {
        "attackers": {
            "4003": {"id": 4003, "power": 5_000, "hp": 500_000},
        },
        "defenders": [
            {
                "4000": {"id": 4000, "power": 200_000, "hp": 30_000_000},
            }
        ],
    }
    est = PythonBattleEngine().calc(battle)
    assert est.win is False
    assert est.stars == 0
    assert est.progress[0]["defenders"]["heroes"]["4000"]["isDead"] is False


def test_get_default_engine_returns_python_for_estimate():
    engine = get_default_engine("estimate")
    assert isinstance(engine, PythonBattleEngine)


def test_get_default_engine_returns_js_bridge_for_hybrid():
    # Construct the JS bridge without exercising the network. We only
    # verify that the class is selected; actual HTTP interaction is
    # covered by the auth-server /toe/* tests.
    engine = get_default_engine("hybrid", auth_server_url="http://127.0.0.1:1", user_id="x")
    assert engine.__class__.__name__ == "JsBridgeBattleEngine"


def test_select_auto_rivals_sorts():
    from hw_genie.commands.titan_arena import _select_auto_rivals

    status = {
        "rivals": {
            "-480511": {"attackScore": 167, "power": "1800000"},
            "-480711": {"attackScore": 120, "power": "1800000"},
            "49301300": {"attackScore": 0, "power": "495427"},
            "19913600": {"attackScore": 250, "power": "545611"},
        }
    }
    res = _select_auto_rivals(status)
    assert res == ["49301300", "-480711", "-480511"]  # 0 < 120 < 167, wall優先は同score時


def test_select_auto_rivals_threshold_boundary():
    from hw_genie.commands.titan_arena import _select_auto_rivals

    status = {
        "rivals": {
            "a": {"attackScore": 249, "power": "1"},
            "b": {"attackScore": 250, "power": "1"},
            "c": {"attackScore": 251, "power": "1"},
        }
    }
    assert _select_auto_rivals(status) == ["a"]
    assert _select_auto_rivals(status, threshold=251) == ["a", "b"]


def test_battle_estimate_from_bridge_result_unwraps_envelope():
    """Regression: the userscript posts {progress, result:{win,stars}} and the
    auth server stores it verbatim. The engine must unwrap one level —
    previously it read win/stars off the envelope (always False/0) while
    sending the real progress, which the server rejected as Invalid battle."""
    progress = [{"attackers": {"heroes": {}}, "defenders": {"heroes": {}}}]
    est = battle_estimate_from_bridge_result(
        {"progress": progress, "result": {"win": True, "stars": 2}}
    )
    assert est.win is True
    assert est.stars == 2
    assert est.progress == progress


def test_battle_estimate_from_bridge_result_accepts_flat_dict():
    est = battle_estimate_from_bridge_result(
        {"win": True, "stars": 3, "progress": [{"r": 1}]}
    )
    assert (est.win, est.stars, est.progress) == (True, 3, [{"r": 1}])


def test_battle_estimate_from_bridge_result_safe_int():
    """stars=None/str never raises; non-list progress coerces to []."""
    est = battle_estimate_from_bridge_result({"win": True, "stars": None, "progress": [{"r": 1}]})
    assert est.stars == 0
    est2 = battle_estimate_from_bridge_result({"win": False, "stars": "3", "progress": "nope"})
    assert est2.stars == 3
    assert est2.progress == []
    est3 = battle_estimate_from_bridge_result({"progress": [], "result": {"win": True, "stars": None}})
    assert est3.stars == 0


def test_bridge_dead_error_carries_partial_results():
    from hw_genie.battle.engine import BridgeDeadError

    err = BridgeDeadError("dead", partial_results=[{"rivalId": "1"}])
    assert err.partial_results == [{"rivalId": "1"}]
    assert isinstance(err, Exception)


def test_fmt_duration():
    from hw_genie.commands.titan_arena import _fmt_duration

    assert _fmt_duration(5) == "5s"
    assert _fmt_duration(59) == "59s"
    assert _fmt_duration(65) == "1m05s"
    assert _fmt_duration(600) == "10m00s"
    assert _fmt_duration(-3) == "0s"
    assert _fmt_duration(float("inf")) == "?"
    assert _fmt_duration(float("-inf")) == "?"
    assert _fmt_duration(float("nan")) == "?"


def test_js_bridge_heartbeat_while_waiting(monkeypatch, capsys):
    """Bridge waits print periodic heartbeats so long calcs don't look hung."""
    import json as _json

    from hw_genie.battle.engine import JsBridgeBattleEngine

    polls = {"n": 0}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return _json.dumps(self._payload).encode()

    def fake_urlopen(req, timeout=5):
        if req.full_url.endswith("/toe/job"):
            return FakeResp({"id": "abcdef1234567890"})
        polls["n"] += 1
        if polls["n"] < 3:
            return FakeResp({"status": "pending"})
        return FakeResp({"status": "done", "result": {"progress": [], "result": {"win": True, "stars": 3}}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    eng = JsBridgeBattleEngine("http://127.0.0.1:1", "u", poll_interval=0.01, timeout=5, heartbeat_interval=0.02)
    msgs: list = []
    est = eng.calc({"attackers": {}, "defenders": [{}]}, on_progress=msgs.append)
    assert est.win is True and est.stars == 3
    assert any("waiting for userscript job abcdef12" in m for m in msgs)
    # Default (no on_progress) stays silent.
    polls["n"] = 0
    est2 = eng.calc({"attackers": {}, "defenders": [{}]})
    assert est2.win is True
    out = capsys.readouterr().out
    assert "waiting for userscript job" not in out


def test_playwright_battle_js_helpers_throw_descriptive_errors():
    """Embedded Playwright JS must name the missing key on resolution failure.

    Regression: an unguarded `.pop()[0]` surfaced only as
    `TypeError: Cannot read properties of undefined` after a game update.
    """
    from hw_genie.battle.engine import PLAYWRIGHT_BATTLE_JS

    assert "not found in Haxe class prototype" in PLAYWRIGHT_BATTLE_JS
    assert "out of range in Haxe class keys" in PLAYWRIGHT_BATTLE_JS
    assert "out of range in Haxe prototype keys" in PLAYWRIGHT_BATTLE_JS
    # The old unguarded one-liner must not come back.
    assert "filter(e => e[1] === name).pop()[0]" not in PLAYWRIGHT_BATTLE_JS


def _fake_bridge_fetch(monkeypatch, *, done_after=2, timeout_jobs=(), now=None):
    """Install a fake urlopen that records POST/poll order for the job queue."""
    import json as _json
    import time as _time

    stamp = now if now is not None else _time.time()
    events: list[tuple[str, str]] = []
    jobs: dict[str, dict] = {}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return _json.dumps(self._payload).encode()

    def fake_urlopen(req, timeout=5):
        if req.full_url.endswith("/toe/job"):
            job_id = f"job{len(jobs) + 1}"
            jobs[job_id] = {"polls": 0}
            events.append(("post", job_id))
            return FakeResp({"id": job_id})
        job_id = req.full_url.split("/toe/job/")[1].split("?")[0]
        events.append(("poll", job_id))
        state = jobs[job_id]
        state["polls"] += 1
        if state["polls"] < done_after:
            return FakeResp({"status": "pending", "id": job_id})
        if job_id in timeout_jobs:
            return FakeResp({"status": "pending", "id": job_id})
        return FakeResp(
            {
                "status": "done",
                "id": job_id,
                "result": {"progress": [], "result": {"win": True, "stars": 3}},
                "created_at": stamp - 1.0,
                "claimed_at": stamp - 0.5,
                "finished_at": stamp,
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return events, jobs


def test_js_bridge_calc_batch_posts_all_jobs_before_polling(monkeypatch):
    """calc_batch queues every battle first so the browser can drain them."""
    from hw_genie.battle.engine import JsBridgeBattleEngine

    events, _jobs = _fake_bridge_fetch(monkeypatch, done_after=2)
    eng = JsBridgeBattleEngine("http://127.0.0.1:1", "u", poll_interval=0.001, timeout=5)
    out = eng.calc_batch([{"seed": 1}, {"seed": 2}, {"seed": 3}])
    assert [e.win for e in out] == [True, True, True]
    assert [e for e in events if e[0] == "post"] == [
        ("post", "job1"),
        ("post", "job2"),
        ("post", "job3"),
    ]
    # No poll may interleave with the submissions.
    assert events.index(("poll", "job1")) > events.index(("post", "job3"))

    summary = eng.timing_summary()
    assert summary is not None
    assert summary.startswith("ToE bridge timing (3 jobs, avg)")
    # queue_wait = claimed_at - created_at, sim = finished_at - claimed_at
    assert "queue 0.50s" in summary
    assert "sim 0.50s" in summary


def test_js_bridge_calc_batch_reports_per_job_timeout(monkeypatch):
    """A stalled (never claimed) job times out via the queue watchdog.

    The unclaimed job has no per-job clock; completions of the other jobs
    keep the queue alive until the whole queue goes quiet for ``timeout``.
    """
    from hw_genie.battle.engine import BridgeTimeoutError, JsBridgeBattleEngine

    _fake_bridge_fetch(monkeypatch, done_after=2, timeout_jobs=("job2",))
    eng = JsBridgeBattleEngine("http://127.0.0.1:1", "u", poll_interval=0.001, timeout=0.01)
    out = eng.calc_batch([{"seed": 1}, {"seed": 2}])
    assert out[0].win is True
    assert isinstance(out[1], BridgeTimeoutError)
    assert "did not finish battle within" in str(out[1])
    # Only the finished job contributes a timing sample.
    assert len(eng.timings) == 1


def test_js_bridge_calc_batch_queued_job_does_not_time_out(monkeypatch):
    """A job waiting in the queue has no per-job clock while others progress."""
    import time

    from hw_genie.battle.engine import JsBridgeBattleEngine

    eng = JsBridgeBattleEngine("http://127.0.0.1:1", "u", poll_interval=0.005, timeout=0.5)
    start = time.monotonic()
    monkeypatch.setattr(
        eng, "_post_job", lambda battle: (f"job{battle['seed']}", time.time(), 0.0)
    )
    done = {"status": "done", "result": {"progress": [], "result": {"win": True, "stars": 3}}}
    claimed: dict[str, float] = {}

    def fake_fetch(job_id):
        elapsed = time.monotonic() - start
        if job_id == "job1":
            if elapsed >= 0.15:
                return done
            return {"status": "pending", "claimed_at": claimed.setdefault(job_id, time.time())}
        if job_id == "job2":
            if elapsed >= 0.45:
                return done
            return {"status": "pending", "claimed_at": claimed.setdefault(job_id, time.time())}
        # job3 sits in the queue for longer than the 0.5s timeout; jobs 1-2
        # finishing refresh the watchdog until it is finally claimed.
        if elapsed < 0.75:
            return {"status": "pending"}
        return {"status": "pending", "claimed_at": claimed.setdefault(job_id, time.time())} \
            if elapsed < 0.85 else done

    monkeypatch.setattr(eng, "_fetch_job", fake_fetch)
    out = eng.calc_batch([{"seed": 1}, {"seed": 2}, {"seed": 3}])
    assert [item.win for item in out] == [True, True, True]
    assert "job3" in claimed


def test_js_bridge_timing_summary_empty_without_jobs():
    from hw_genie.battle.engine import JsBridgeBattleEngine

    assert JsBridgeBattleEngine("http://127.0.0.1:1", "u").timing_summary() is None


def test_js_bridge_calc_batch_empty_list_makes_no_request(monkeypatch):
    from hw_genie.battle.engine import JsBridgeBattleEngine

    def fake_urlopen(req, timeout=5):
        raise AssertionError("calc_batch([]) must not touch the network")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    eng = JsBridgeBattleEngine("http://127.0.0.1:1", "u")
    assert eng.calc_batch([]) == []


def test_js_bridge_calc_batch_timeout_uses_claimed_at(monkeypatch):
    """A long-queued job's clock starts when claimed, not when posted."""
    import time

    from hw_genie.battle.engine import BridgeTimeoutError, JsBridgeBattleEngine

    eng = JsBridgeBattleEngine("http://127.0.0.1:1", "u", poll_interval=0.001, timeout=5)
    posted_at = time.time()
    monkeypatch.setattr(
        eng, "_post_job", lambda battle: (f"job{battle['seed']}", posted_at, 0.0)
    )

    def fake_fetch(job_id):
        if job_id == "job1":
            # Queued for a while, claimed 10s ago → already past the 5s clock.
            return {"status": "pending", "claimed_at": time.time() - 10}
        return {
            "status": "done",
            "result": {"progress": [], "result": {"win": True, "stars": 3}},
        }

    monkeypatch.setattr(eng, "_fetch_job", fake_fetch)
    start = time.monotonic()
    out = eng.calc_batch([{"seed": 1}, {"seed": 2}])
    elapsed = time.monotonic() - start
    assert isinstance(out[0], BridgeTimeoutError)
    assert out[1].win is True
    assert elapsed < 2.0


def test_js_bridge_calc_batch_decode_error_is_per_job(monkeypatch):
    """A malformed result fails only that job, never the whole batch."""
    import time

    import hw_genie.battle.engine as engine_module
    from hw_genie.battle.engine import BattleEstimate, BridgeError, JsBridgeBattleEngine

    eng = JsBridgeBattleEngine("http://127.0.0.1:1", "u", poll_interval=0.001, timeout=5)
    monkeypatch.setattr(
        eng, "_post_job", lambda battle: (f"job{battle['seed']}", time.time(), 0.0)
    )
    monkeypatch.setattr(
        eng,
        "_fetch_job",
        lambda job_id: {
            "status": "done",
            "result": {"progress": [], "result": {"win": True, "stars": 3}},
        },
    )
    calls = []

    def fake_decode(envelope):
        calls.append(envelope)
        if len(calls) == 2:
            raise ValueError("boom")
        return BattleEstimate(True, 3, [])

    monkeypatch.setattr(engine_module, "battle_estimate_from_bridge_result", fake_decode)
    out = eng.calc_batch([{"seed": 1}, {"seed": 2}])
    assert isinstance(out[0], BattleEstimate)
    assert isinstance(out[1], BridgeError)
