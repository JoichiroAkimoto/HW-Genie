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


def test_fallback_progress_is_empty_loss():
    from hw_genie.commands.titan_arena import _fallback_progress

    assert _fallback_progress({}, win=False) == [
        {"attackers": {"heroes": {}}, "defenders": {"heroes": {}}}
    ]


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
