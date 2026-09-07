from unittest.mock import MagicMock


from hw_genie.battle.engine import (
    BattleEstimate,
    PythonBattleEngine,
    battle_estimate_from_bridge_result,
    estimate_battle,
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


def test_estimate_battle_backward_compat_dict_shape():
    # Old callers used the dict shape; ensure it still works.
    res = estimate_battle({"attackers": {}, "defenders": [{}]})
    assert set(res) == {"win", "stars", "progress"}


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
