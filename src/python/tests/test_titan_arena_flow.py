from unittest.mock import MagicMock

import pytest

from hw_genie.battle.engine import PythonBattleEngine
from hw_genie.commands.titan_arena import run_titan_arena, run_titan_arena_tier


def _ok(detail: dict) -> MagicMock:
    res = MagicMock()
    res.is_success = True
    res.detail = detail
    return res


def _err(name: str, detail: dict | None = None) -> MagicMock:
    res = MagicMock()
    res.is_success = False
    res.error_name = name
    res.detail = detail or {}
    return res


def test_run_titan_arena_dry_run_does_not_call_end_battle(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(
            {
                "response": {
                    "battle": {
                        "type": "titan_arena",
                        "seed": 1,
                        "userId": "1",
                        "typeId": "-470711",
                        "attackers": {"4003": {"id": 4003, "power": 100, "hp": 1000}},
                        "defenders": [{"4000": {"id": 4000, "power": 1, "hp": 100}}],
                        "effects": [],
                        "reward": [],
                        "startTime": 0,
                    }
                }
            }
        )
    ]
    res = run_titan_arena(
        client,
        rival_id="-470711",
        titans=[4003, 4023, 4004, 4001, 4000],
        dry_run=True,
    )
    assert res["dry_run"] is True
    assert mock_call.call_count == 1


def test_run_titan_arena_estimate_only_returns_estimate(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok(
            {
                "response": {
                    "battle": {
                        "type": "titan_arena",
                        "seed": 2,
                        "userId": "1",
                        "typeId": "-1",
                        "attackers": {"4003": {"id": 4003, "power": 100, "hp": 1000}},
                        "defenders": [{"4000": {"id": 4000, "power": 1, "hp": 100}}],
                        "effects": [],
                        "reward": [],
                        "startTime": 0,
                    }
                }
            }
        )
    ]
    res = run_titan_arena(
        client,
        rival_id="-1",
        titans=[4003, 4023, 4004, 4001, 4000],
        estimate_only=True,
    )
    assert res["estimate_only"] is True
    assert res["estimate"].win is True
    assert mock_call.call_count == 1  # no EndBattle call


def test_run_titan_arena_tier_handles_peace_time(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok({"response": {"status": "peace_time", "tier": 7, "rivals": {}}}),
    ]
    summary = run_titan_arena_tier(
        client,
        titans=[4003, 4023, 4004, 4001, 4000],
        engine=PythonBattleEngine(),
    )
    assert summary["errors"] == []
    assert summary["rival_results"] == []
    # getStatus was called exactly once
    assert mock_call.call_count == 1


def test_run_titan_arena_tier_runs_rivals_then_completes(mock_client, mock_sleep):
    """All rivals have attackScore<250; engine estimates win; tier completes."""
    client, mock_call = mock_client

    battle = {
        "type": "titan_arena",
        "seed": 7,
        "userId": "1",
        "typeId": "-1",
        "attackers": {
            "4003": {"id": 4003, "power": 200_000, "hp": 10_000_000},
        },
        "defenders": [
            {"4000": {"id": 4000, "power": 10, "hp": 100}},
        ],
        "effects": [],
        "reward": [],
        "startTime": 0,
    }
    rivals = {
        "100": {"userId": "100", "power": "100", "titans": {}, "attackScore": 0, "seed": "0", "defenceScore": "0"},
        "101": {"userId": "101", "power": "100", "titans": {}, "attackScore": 100, "seed": "0", "defenceScore": "0"},
    }
    # Sequence: getStatus -> StartBattle -> EndBattle (per rival x2) ->
    # CompleteTier -> FarmDailyReward -> getStatus (all cleared) -> Farm -> return.
    # The loop keeps going across passes until no rival is left to attack.
    cleared = {
        "100": {"userId": "100", "power": "100", "titans": {}, "attackScore": 250, "seed": "0", "defenceScore": "0"},
        "101": {"userId": "101", "power": "100", "titans": {}, "attackScore": 250, "seed": "0", "defenceScore": "0"},
    }
    mock_call.side_effect = [
        _ok({"response": {"status": "battle", "tier": 7, "rivals": rivals, "canRaid": False}}),
        _ok({"response": {"battle": battle}}),  # StartBattle 100
        _ok({"response": {}}),  # EndBattle 100
        _ok({"response": {"battle": battle}}),  # StartBattle 101
        _ok({"response": {}}),  # EndBattle 101
        _ok({"response": {}}),  # CompleteTier
        _ok({"response": {}}),  # FarmDailyReward
        _ok({"response": {"status": "battle", "tier": 7, "rivals": cleared, "canRaid": False}}),
        _ok({"response": {}}),  # FarmDailyReward (nothing left)
    ]
    summary = run_titan_arena_tier(
        client,
        titans=[4003, 4023, 4004, 4001, 4000],
        engine=PythonBattleEngine(),
    )
    # Both rivals won in pass 1; pass 2 sees them cleared and exits.
    assert sum(1 for r in summary["rival_results"] if r.get("win")) == 2
    assert summary["completed_tier"] is True


def test_run_titan_arena_tier_auto_titans(mock_client, mock_sleep):
    client, mock_call = mock_client
    # mock teamGetAll then getStatus peace_time
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}}),  # _resolve
        _ok({"response": {"status": "peace_time", "tier": 1, "rivals": {}}}),
    ]
    summary = run_titan_arena_tier(client, titans=None, engine=PythonBattleEngine())
    assert summary["titans"] == [1, 2, 3, 4, 5]


def test_run_titan_arena_tier_explicit_titans_skips_resolve(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok({"response": {"status": "peace_time", "tier": 7, "rivals": {}}}),
    ]
    summary = run_titan_arena_tier(
        client,
        titans=[4003, 4023, 4004, 4001, 4000],
        engine=PythonBattleEngine(),
    )
    assert summary["titans"] == [4003, 4023, 4004, 4001, 4000]
    # No teamGetAll call — only getStatus
    assert mock_call.call_count == 1
    assert "teamGetAll" not in str(mock_call.call_args_list)


def test_run_titan_arena_auto_picks_lowest_score(mock_client, mock_sleep):
    client, mock_call = mock_client
    battle = {
        "type": "titan_arena",
        "seed": 1,
        "userId": "1",
        "typeId": "-480711",
        "attackers": {"4003": {"id": 4003, "power": 100, "hp": 1000}},
        "defenders": [{"4000": {"id": 4000, "power": 1, "hp": 100}}],
        "effects": [],
        "reward": [],
        "startTime": 0,
    }
    # titans resolve via teamGetAll, then status with 3 rivals, then StartBattle/EndBattle
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [4003, 4023, 4004, 4001, 4000]}}),  # _resolve
        _ok(
            {
                "response": {
                    "status": "battle",
                    "tier": 1,
                    "rivals": {
                        "-480511": {"attackScore": 167, "power": "1800000"},
                        "-480711": {"attackScore": 120, "power": "1800000"},
                        "49301300": {"attackScore": 0, "power": "495427"},
                    },
                }
            }
        ),
        _ok({"response": {"battle": battle}}),
        _ok({"response": {}}),
    ]
    res = run_titan_arena(client, rival_id=None, titans=None, dry_run=False)
    assert res["status"].value == "success" or res.get("end_detail") is not None or res.get("estimate") is not None
    # Verify auto-selected rival was the lowest score 49301300 (0)
    assert mock_call.call_count == 4
    # Second call is GetStatus, third is StartBattle with rivalId 49301300
    start_call = mock_call.call_args_list[2]
    assert "49301300" in str(start_call)


def test_run_titan_arena_explicit_rival_skips_fetch(mock_client, mock_sleep):
    client, mock_call = mock_client
    battle = {
        "type": "titan_arena",
        "seed": 1,
        "userId": "1",
        "typeId": "-470711",
        "attackers": {"4003": {"id": 4003, "power": 100, "hp": 1000}},
        "defenders": [{"4000": {"id": 4000, "power": 1, "hp": 100}}],
        "effects": [],
        "reward": [],
        "startTime": 0,
    }
    mock_call.side_effect = [
        _ok({"response": {"battle": battle}}),
        _ok({"response": {}}),
    ]
    run_titan_arena(client, rival_id="-470711", titans=[4003, 4023, 4004, 4001, 4000], dry_run=False)
    # Only StartBattle + EndBattle, no GetStatus
    assert mock_call.call_count == 2
    assert "titanArenaGetStatus" not in str(mock_call.call_args_list)


def test_run_titan_arena_auto_no_rivals_skips_battle(mock_client, mock_sleep):
    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}}),  # _resolve titans
        _ok({"response": {"status": "battle", "tier": 1, "rivals": {"a": {"attackScore": 250, "power": "1"}}}}),
    ]
    res = run_titan_arena(client, rival_id=None, titans=None)
    assert res["status"].value == "skipped"
    assert res["reason"] == "no_rivals"
    # No StartBattle
    assert mock_call.call_count == 2


def test_run_titan_arena_invalid_titans_no_fetch(mock_client, mock_sleep):
    client, mock_call = mock_client
    # Invalid titans (3 instead of 5) should raise before any GetStatus
    try:
        run_titan_arena(client, rival_id=None, titans=[1, 2, 3])
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "titans must be 5" in str(e)
    assert mock_call.call_count == 0


def test_run_rivals_sorts_via_select(mock_client, mock_sleep, mocker):
    from hw_genie.commands.titan_arena import _run_rivals

    status = {
        "status": "battle",
        "tier": 1,
        "rivals": {
            "-480511": {"attackScore": 167, "power": "1800000"},
            "-480711": {"attackScore": 120, "power": "1800000"},
            "49301300": {"attackScore": 0, "power": "495427"},
            "19913600": {"attackScore": 250, "power": "545611"},
        },
    }
    called = []

    def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
        called.append(str(rival_id))
        return {"estimate": MagicMock(win=True), "status": MagicMock(value="success")}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    _run_rivals(client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(), threshold=250, stop_on_first_loss=False)
    # Should be sorted via _select_auto_rivals: 49301300 (0), -480711 (120), -480511 (167)
    assert called == ["49301300", "-480711", "-480511"]


def test_run_titan_arena_tier_default_threshold_is_constant():
    import inspect
    from hw_genie.commands.titan_arena import AUTO_RIVAL_SCORE_THRESHOLD, run_titan_arena_tier

    sig = inspect.signature(run_titan_arena_tier)
    assert sig.parameters["attack_score_threshold"].default == AUTO_RIVAL_SCORE_THRESHOLD
    assert AUTO_RIVAL_SCORE_THRESHOLD == 250


def test_cmd_toe_attack_wiring_handles_none(mocker):
    from hw_genie.main import cmd_toe_attack

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.main.HWClient")
    mock_run = mocker.patch("hw_genie.commands.titan_arena.run_titan_arena")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=PythonBattleEngine())
    args = MagicMock()
    args.account = "TestUser"
    args.rival = None
    args.titans = None
    args.dry_run = False
    args.estimate_only = False
    args.engine = "estimate"
    args.auth_server_url = "http://127.0.0.1:8765"
    cmd_toe_attack(args)
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs.get("rival_id") is None or mock_run.call_args.args[1] is None
    # titans should be None (auto-resolve)
    called_titans = kwargs.get("titans") if "titans" in kwargs else mock_run.call_args.args[2] if len(mock_run.call_args.args) > 2 else None
    assert called_titans is None


def test_cmd_toe_run_wiring_handles_none(mocker):
    from hw_genie.main import cmd_toe_run

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.main.HWClient")
    mock_run = mocker.patch("hw_genie.commands.titan_arena.run_titan_arena_tier")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=PythonBattleEngine())
    args = MagicMock()
    args.account = "TestUser"
    args.titans = None
    args.threshold = 250
    args.stop_on_loss = False
    args.engine = "estimate"
    args.auth_server_url = "http://127.0.0.1:8765"
    args.seeds = 2
    args.max_attempts = None
    cmd_toe_run(args)
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    called_titans = kwargs.get("titans") if "titans" in kwargs else mock_run.call_args.args[1] if len(mock_run.call_args.args) > 1 else None
    assert called_titans is None
    assert kwargs.get("max_total_attempts") is None


def test_cmd_toe_run_threads_max_attempts(mocker):
    """toe run --max-attempts reaches run_titan_arena_tier as max_total_attempts."""
    from hw_genie.main import cmd_toe_run

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.main.HWClient")
    mock_run = mocker.patch("hw_genie.commands.titan_arena.run_titan_arena_tier")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=PythonBattleEngine())
    args = MagicMock()
    args.account = "TestUser"
    args.titans = None
    args.threshold = 250
    args.stop_on_loss = False
    args.engine = "estimate"
    args.auth_server_url = "http://127.0.0.1:8765"
    args.seeds = 2
    args.max_attempts = 5
    cmd_toe_run(args)
    assert mock_run.call_args.kwargs.get("max_total_attempts") == 5


def test_tier_threads_max_total_attempts_to_rivals(mock_client, mock_sleep, mocker):
    """run_titan_arena_tier forwards max_total_attempts to _run_rivals."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    status = {"status": "battle", "tier": 7, "rivals": {"-1": {"attackScore": 0}}, "canRaid": False}
    mock_call.side_effect = [
        _ok({"response": status}),
    ]
    seen = {}

    def spy(client, status, titans, engine, threshold, stop, **kw):
        seen.update(kw)
        return []

    mocker.patch("hw_genie.commands.titan_arena._run_rivals", side_effect=spy)
    mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)
    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(), max_total_attempts=4)
    assert seen.get("max_total_attempts") == 4


def test_cli_parser_toe_optional(mocker):
    # Verify main parser accepts toe attack/run without --rival/--titans
    from hw_genie.main import main
    import sys

    # Patch DB and session to avoid real calls; we just test parsing up to function dispatch
    mocker.patch("hw_genie.core.database.init_db")
    mocker.patch("hw_genie.core.database.install_token_masking_filter")
    mocker.patch("hw_genie.main.setup_logging")
    # Mock the toe commands to capture that parsing succeeded
    mock_attack = mocker.patch("hw_genie.main.cmd_toe_attack")
    mock_run = mocker.patch("hw_genie.main.cmd_toe_run")

    # toe attack without args should parse and dispatch to cmd_toe_attack
    sys.argv = ["hw-genie", "toe", "attack", "-a", "TestUser"]
    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    # Need to mock resolve_account inside cmd? Actually cmd will be mocked, so not needed
    main()
    mock_attack.assert_called_once()
    args = mock_attack.call_args.args[0]
    assert getattr(args, "rival", None) is None
    assert getattr(args, "titans", None) is None

    mock_attack.reset_mock()
    mock_run.reset_mock()
    sys.argv = ["hw-genie", "toe", "run", "-a", "TestUser"]
    main()
    mock_run.assert_called_once()
    args = mock_run.call_args.args[0]
    assert getattr(args, "titans", None) is None


def test_run_rivals_retries_invalid_battle_with_fresh_seed(mock_client, mock_sleep, mocker):
    """Invalid battle is retried with a new StartBattle instead of moving on."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
        calls.append(str(rival_id))
        if len(calls) == 1:
            return {"estimate": MagicMock(win=True), "end_error": "Invalid battle"}
        return {"estimate": MagicMock(win=True)}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(), threshold=250, stop_on_first_loss=False)
    assert calls == ["-1", "-1"]
    assert results[0]["win"] is True
    assert results[0].get("end_error") is None


def test_run_rivals_treats_beaten_already_as_cleared(mock_client, mock_sleep, mocker):
    """A stale snapshot's beaten-up rival counts as cleared after refresh."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}

    def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
        return {"error": "NotAvailable", "detail": {"description": "beaten up already"}}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    mocker.patch(
        "hw_genie.commands.titan_arena.fetch_titan_arena_status",
        return_value={"status": "battle", "rivals": {"-1": {"attackScore": 250}}},
    )
    client, _ = mock_client
    results = _run_rivals(client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(), threshold=250, stop_on_first_loss=False)
    assert results == [{"rivalId": "-1", "win": True, "already_cleared": True}]


def test_resolve_team_rotation_saved_first_and_deduped(mock_client, mock_sleep):
    from hw_genie.commands.titan_arena import _resolve_team_rotation

    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [4044, 4012, 4013, 4043, 4010]}}),
    ]
    rotation = _resolve_team_rotation(client, None)
    # Saved team first; identical static entry skipped, rest in order
    assert rotation[0] == [4044, 4012, 4013, 4043, 4010]
    assert [4044, 4013, 4043, 4014, 4010] in rotation
    assert [4042, 4013, 4043, 4014, 4010] in rotation
    assert [4012, 4042, 4013, 4043, 4010] in rotation
    # docs/superpowers/ToE.md teams appended after the initial four
    assert [4044, 4003, 4043, 4002, 4004] in rotation
    assert [4023, 4043, 4024, 4022, 4040] in rotation
    assert rotation.index([4044, 4003, 4043, 4002, 4004]) > rotation.index([4012, 4042, 4013, 4043, 4010])
    # No duplicates anywhere (saved + static combined)
    assert len({tuple(t) for t in rotation}) == len(rotation)
    assert len(rotation) == 28  # saved dupes static#1, so 1 + 28 - 1
    # 水+1 Tenebris/Eden mixed team
    assert [4033, 4003, 4023, 4004, 4000] in rotation
    # 水2火3
    assert [4003, 4013, 4004, 4014, 4010] in rotation
    # 水3火2（Carol Tier8 の光壁 -480913 に WIN の実績）
    assert [4003, 4004, 4014, 4001, 4010] in rotation


def test_resolve_team_rotation_explicit_is_single(mock_client, mock_sleep):
    from hw_genie.commands.titan_arena import _resolve_team_rotation

    client, _ = mock_client
    assert _resolve_team_rotation(client, [1, 2, 3, 4, 5]) == [[1, 2, 3, 4, 5]]


def test_run_rivals_tries_next_team_after_loss(mock_client, mock_sleep, mocker):
    """A loss moves to the next rotation team; a win stops the rotation."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    seen = []

    def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
        seen.append(list(titans))
        win = titans[0] == 9
        return {"estimate": MagicMock(win=win)}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]], seeds_per_team=1,
    )
    assert seen == [[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]]
    assert results[-1]["win"] is True
    assert results[-1]["team"] == [9, 9, 9, 9, 9]


def test_run_titan_arena_end_on_loss_false_abandons(mock_client, mock_sleep):
    """With end_on_loss=False, a losing sim skips EndBattle entirely."""
    from hw_genie.commands.titan_arena import run_titan_arena

    client, mock_call = mock_client
    losing_battle = {
        "type": "titan_arena",
        "seed": 1,
        "attackers": {"1": {"power": 1, "hp": 10}},
        "defenders": [{"2": {"power": 1000000, "hp": 1000000}}],
    }
    mock_call.side_effect = [
        _ok({"response": {"battle": losing_battle}}),
    ]
    res = run_titan_arena(
        client, rival_id="-1", titans=[1, 2, 3, 4, 5],
        engine=PythonBattleEngine(), end_on_loss=False,
    )
    assert res.get("abandoned") is True
    assert res["estimate"].win is False
    assert mock_call.call_count == 1  # StartBattle only, no EndBattle


def test_run_titan_arena_end_on_loss_default_sends_endbattle(mock_client, mock_sleep):
    """Default keeps legacy behavior: EndBattle is sent even on loss."""
    from hw_genie.commands.titan_arena import run_titan_arena

    client, mock_call = mock_client
    losing_battle = {
        "type": "titan_arena",
        "seed": 1,
        "attackers": {"1": {"power": 1, "hp": 10}},
        "defenders": [{"2": {"power": 1000000, "hp": 1000000}}],
    }
    mock_call.side_effect = [
        _ok({"response": {"battle": losing_battle}}),
        _ok({"response": {"attackScore": 10}}),
    ]
    res = run_titan_arena(client, rival_id="-1", titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    assert res.get("abandoned") is None
    assert mock_call.call_count == 2


def test_run_rivals_retries_next_seed_on_abandon(mock_client, mock_sleep, mocker):
    """Abandoned seeds advance the plan; a later win stops the rival."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    seen = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        seen.append((list(titans), end_on_loss))
        if len(seen) == 1:
            return {"estimate": MagicMock(win=False), "abandoned": True}
        return {"estimate": MagicMock(win=True)}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=2, end_on_loss=False,
    )
    assert len(seen) == 2
    assert all(e is False for _, e in seen)
    assert results[-1]["win"] is True


def test_run_rivals_stops_after_consecutive_bridge_timeouts(mock_client, mock_sleep, mocker):
    """3 straight timeouts raise BridgeDeadError instead of grinding."""
    import pytest

    from hw_genie.battle.engine import BridgeDeadError
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": MagicMock(win=False), "bridge_error": "userscript did not finish battle within 30.0s", "estimate_only": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    with pytest.raises(BridgeDeadError) as excinfo:
        _run_rivals(
            client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
            threshold=250, stop_on_first_loss=False, team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=9,
        )
    assert len(calls) == 3
    assert excinfo.value.partial_results == []


def test_run_rivals_bridge_dead_preserves_partial_results(mock_client, mock_sleep, mocker):
    """A win before the outage is preserved on the raised BridgeDeadError."""
    import pytest

    from hw_genie.battle.engine import BridgeDeadError
    from hw_genie.commands.titan_arena import _run_rivals

    status = {
        "status": "battle", "tier": 8,
        "rivals": {"-1": {"attackScore": 0, "power": "1"}, "-2": {"attackScore": 0, "power": "1"}},
    }
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(str(rival_id))
        if str(rival_id) == "-1":
            return {"estimate": MagicMock(win=True)}
        return {"estimate": MagicMock(win=False), "bridge_error": "userscript did not finish battle within 5s", "estimate_only": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    # Single-team plan with many attempts so the second rival can time out 3x.
    client, _ = mock_client
    with pytest.raises(BridgeDeadError) as excinfo:
        _run_rivals(
            client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
            threshold=250, stop_on_first_loss=False,
            team_rotation=None, max_attempts_per_rival=9, end_on_loss=False,
        )
    assert excinfo.value.partial_results[0]["win"] is True
    assert excinfo.value.partial_results[0]["rivalId"] == "-1"


def test_tier_bridge_dead_canraid_false_reports_error(mock_client, mock_sleep, mocker):
    """canRaid=False + bridge dead → summary errors, no misleading 'No rivals'."""
    from hw_genie.battle.engine import BridgeDeadError
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    status = {"status": "battle", "tier": 7, "rivals": {"-1": {"attackScore": 0}}, "canRaid": False}
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}}),
        _ok({"response": status}),
    ]
    mocker.patch(
        "hw_genie.commands.titan_arena._run_rivals",
        side_effect=BridgeDeadError("bridge dead", partial_results=[{"rivalId": "-1", "win": True}]),
    )
    summary = run_titan_arena_tier(client, titans=None, engine=PythonBattleEngine())
    assert summary["rival_results"] == [{"rivalId": "-1", "win": True}]
    assert any("bridge dead" in str(e.get("message", "")) for e in summary["errors"])
    assert summary["completed_tier"] is False


def test_tier_bridge_dead_canraid_true_stops_without_complete(mock_client, mock_sleep, mocker):
    """canRaid=True + raid bridge dead → tier aborts, no CompleteTier grind."""
    from hw_genie.battle.engine import BridgeDeadError
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    status = {"status": "battle", "tier": 7, "rivals": {"-1": {"attackScore": 0}}, "canRaid": True}
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}}),
        _ok({"response": status}),
    ]
    mocker.patch(
        "hw_genie.commands.titan_arena._run_raid",
        side_effect=BridgeDeadError("raid bridge dead", partial_results=[]),
    )
    spy_complete = mocker.patch("hw_genie.commands.titan_arena._complete_tier")
    summary = run_titan_arena_tier(client, titans=None, engine=PythonBattleEngine())
    assert any(e.get("stage") == "raid" for e in summary["errors"])
    spy_complete.assert_not_called()


def test_run_rivals_attempt_plan_bounded(mock_client, mock_sleep, mocker):
    """Rotation does exactly one full pass (teams × seeds), no more."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": MagicMock(win=False), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    rotation = [[i, i + 1, i + 2, i + 3, i + 4] for i in range(3)]
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=rotation, seeds_per_team=2, max_attempts_per_rival=3,
    )
    assert len(calls) == 3 * 2
    assert len(results) == 3 * 2
    assert all(r.get("abandoned") for r in results)


def test_run_rivals_stop_on_first_loss_with_rotation(mock_client, mock_sleep, mocker):
    """stop_on_first_loss aborts after the first loss even with rotation left."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {
        "status": "battle", "tier": 8,
        "rivals": {"-1": {"attackScore": 0, "power": "1"}, "-2": {"attackScore": 0, "power": "1"}},
    }
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(str(rival_id))
        return {"estimate": MagicMock(win=False)}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=True,
        team_rotation=[[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]], seeds_per_team=2,
    )
    assert len(calls) == 1
    assert len(results) == 1
    assert results[0]["win"] is False


def test_run_raid_bridge_breaker_skips_fake_and_raises(mock_client, mock_sleep):
    """Raid bridge errors skip EndRaid fakes; 3rd consecutive raises."""
    import pytest

    from hw_genie.battle.engine import BridgeDeadError, BridgeTimeoutError
    from hw_genie.commands.titan_arena import _run_raid

    class FlakyEngine:
        def __init__(self):
            self.n = 0

        def calc(self, battle):
            self.n += 1
            raise BridgeTimeoutError("userscript did not finish battle within 5s")

    client, mock_call = mock_client
    status = {"status": "battle", "tier": 1, "userId": "1", "rivals": {"a": {"attackScore": 0}}}
    start_payload = {
        "attackers": {"4003": {"id": 4003}},
        "rivals": {"r1": {"t": 1}, "r2": {"t": 2}, "r3": {"t": 3}},
    }
    mock_call.side_effect = [_ok({"response": start_payload})]
    with pytest.raises(BridgeDeadError):
        _run_raid(client, status, [1, 2, 3, 4, 5], FlakyEngine(), 250)
    # StartRaid only — EndRaid never called with fabricated progress.
    assert mock_call.call_count == 1


def test_run_raid_partial_bridge_errors_skip_only_failed(mock_client, mock_sleep):
    """One bridge failure among successes submits only verified results."""
    from hw_genie.battle.engine import BridgeTimeoutError
    from hw_genie.commands.titan_arena import _run_raid

    calls = {"n": 0}

    class MixedEngine:
        def calc(self, battle):
            calls["n"] += 1
            if calls["n"] == 1:
                raise BridgeTimeoutError("userscript did not finish battle within 5s")
            from hw_genie.battle.engine import BattleEstimate

            return BattleEstimate(
                win=True,
                stars=3,
                progress=[{"attackers": {"heroes": {"1": {"hp": 10}}}, "defenders": {"heroes": {}}}],
            )

    client, mock_call = mock_client
    status = {"status": "battle", "tier": 1, "userId": "1", "rivals": {}}
    start_payload = {"attackers": {"4003": {}}, "rivals": {"r1": {"t": 1}, "r2": {"t": 2}}}
    mock_call.side_effect = [
        _ok({"response": start_payload}),
        _ok({"response": {"results": {"r2": {"attackScore": 250}}}}),
    ]
    summary = _run_raid(client, status, [1, 2, 3, 4, 5], MixedEngine(), 250)
    assert len(summary["battles"]) == 2
    assert mock_call.call_count == 2
    end_args = mock_call.call_args_list[1][0][0]["calls"][0]["args"]["results"]
    assert set(end_args) == {"r2"}


def test_is_beaten_already_negative_and_log(mock_client, mock_sleep, capsys):
    """Non-matching NotAvailable detail → False + warning log."""
    from hw_genie.commands.titan_arena import _is_beaten_already

    assert _is_beaten_already({"error": "NotAvailable", "detail": {"description": "beaten up already"}}) is True
    assert _is_beaten_already({"error": "NotAvailable", "detail": {"description": "something else changed"}}) is False
    assert _is_beaten_already({"error": "Other", "detail": "beaten up already"}) is False
    out = capsys.readouterr().out
    assert "unexpected detail" in out


def test_end_battle_prints_concise_summary(capsys, mock_client, mock_sleep):
    """EndBattle success prints one short line, not the full payload."""
    from hw_genie.commands.titan_arena import run_titan_arena

    client, mock_call = mock_client
    battle = {
        "type": "titan_arena", "seed": 1,
        "attackers": {"1": {"power": 999999, "hp": 10}},
        "defenders": [{"2": {"power": 1, "hp": 10}}],
    }
    big = {"attackScore": 250, "attackScoreEarned": 10, "result": {"stars": 3},
           "rivalTeam": {"x": "y" * 5000}, "battle": {"z": [1, 2, 3]}}
    mock_call.side_effect = [
        _ok({"response": {"battle": battle}}),
        _ok({"response": big}),
    ]
    run_titan_arena(client, rival_id="-1", titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    out = capsys.readouterr().out
    assert "attackScore=250" in out
    assert "rivalTeam" not in out
    assert len([line for line in out.splitlines() if line.startswith("🏆 EndBattle")]) == 1


def test_run_rivals_max_total_attempts_caps_rotation(mock_client, mock_sleep, mocker):
    """max_total_attempts truncates the rotation pass; log shows effective bound."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": MagicMock(win=False), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    rotation = [[i, i + 1, i + 2, i + 3, i + 4] for i in range(5)]
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=rotation, seeds_per_team=2, max_total_attempts=3,
    )
    assert len(calls) == 3
    assert len(results) == 3


def test_run_rivals_max_total_attempts_none_is_full_pass(mock_client, mock_sleep, mocker, capsys):
    """Default None keeps the full pass and logs the effective bound."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": MagicMock(win=False), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    rotation = [[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]]
    _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=rotation, seeds_per_team=2, max_total_attempts=None,
    )
    assert len(calls) == 4
    out = capsys.readouterr().out
    assert "Attempt plan per rival: 4/4" in out


def test_run_rivals_last_attempt_invalid_battle_is_not_win(mock_client, mock_sleep, mocker):
    """Last-attempt Invalid battle must not count as a win (no tier-loop spin)."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        return {"estimate": MagicMock(win=True), "end_error": "Invalid battle"}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=None, max_attempts_per_rival=1, end_on_loss=False,
    )
    assert len(results) == 1
    assert results[0]["win"] is False
    assert results[0]["end_error"] == "Invalid battle"
    assert not any(r.get("win") for r in results)


def test_summarize_end_battle_none_safe():
    """None score/earned/stars fall back instead of printing None."""
    from hw_genie.battle.engine import BattleEstimate
    from hw_genie.commands.titan_arena import _summarize_end_battle

    est = BattleEstimate(win=True, stars=3, progress=[])
    line = _summarize_end_battle("-1", est, {"attackScore": None, "attackScoreEarned": None, "result": {"stars": None}})
    assert "None" not in line
    assert "stars=3" in line
    line2 = _summarize_end_battle("-1", est, {"attackScore": None, "result": None})
    assert "None" not in line2
    assert "attackScore=?" in line2


def test_complete_tier_notavailable_logs_detail(capsys, mock_client, mock_sleep):
    """NotAvailable includes truncated detail + tier context at info level."""
    from hw_genie.commands.titan_arena import _complete_tier

    client, mock_call = mock_client
    mock_call.side_effect = [_err("NotAvailable", {"description": "not ready xyz"})]
    summary: dict = {"raid_results": [], "completed_tier": False}
    _complete_tier(client, summary, tier=7)
    out = capsys.readouterr().out
    assert "tier=7" in out
    assert "not ready xyz" in out
    assert summary["completed_tier"] is False


def test_complete_tier_notavailable_after_raid_completed_warns(capsys, mock_client, mock_sleep):
    """Raid completed + CompleteTier NotAvailable emits a WARNING."""
    from hw_genie.commands.titan_arena import _complete_tier

    client, mock_call = mock_client
    mock_call.side_effect = [_err("NotAvailable", {"description": "stuck detail"})]
    summary: dict = {"raid_results": [{"completed": True}], "completed_tier": False}
    _complete_tier(client, summary, tier=9)
    out = capsys.readouterr().out
    assert "stuck detail" in out
    assert "⚠️" in out or "WARNING" in out or "raid reported completed" in out


def test_run_titan_arena_shows_attempt_label(capsys, mock_client, mock_sleep):
    """Attempt counter prefix appears on the battle header line."""
    from hw_genie.commands.titan_arena import run_titan_arena

    client, mock_call = mock_client
    battle = {
        "type": "titan_arena", "seed": 1,
        "attackers": {"1": {"power": 999999, "hp": 10}},
        "defenders": [{"2": {"power": 1, "hp": 10}}],
    }
    mock_call.side_effect = [
        _ok({"response": {"battle": battle}}),
        _ok({"response": {"attackScore": 250}}),
    ]
    run_titan_arena(client, rival_id="-1", titans=[1, 2, 3, 4, 5],
                    engine=PythonBattleEngine(), attempt_label="3/48")
    out = capsys.readouterr().out
    assert "Titan Arena: [3/48]" in out


def test_run_titan_arena_header_keeps_colon_without_label(capsys, mock_client, mock_sleep):
    """No-label header keeps the legacy `Titan Arena: rivalId=` form (log parsers)."""
    from hw_genie.commands.titan_arena import run_titan_arena

    client, mock_call = mock_client
    battle = {
        "type": "titan_arena", "seed": 1,
        "attackers": {"1": {"power": 999999, "hp": 10}},
        "defenders": [{"2": {"power": 1, "hp": 10}}],
    }
    mock_call.side_effect = [
        _ok({"response": {"battle": battle}}),
        _ok({"response": {"attackScore": 250}}),
    ]
    run_titan_arena(client, rival_id="-1", titans=[1, 2, 3, 4, 5],
                    engine=PythonBattleEngine())
    out = capsys.readouterr().out
    assert "Titan Arena: rivalId=-1" in out


def test_run_rivals_shows_rival_counter_and_pace(capsys, mock_client, mock_sleep, mocker):
    """Rival header shows N/M and retry lines show elapsed pace."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {
        "-1": {"attackScore": 0, "power": "1"},
        "-2": {"attackScore": 10, "power": "1"},
    }}

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        if str(rival_id) == "-1":
            return {"estimate": MagicMock(win=False), "abandoned": True}
        return {"estimate": MagicMock(win=True)}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]], seeds_per_team=1,
    )
    out = capsys.readouterr().out
    assert "Rival -1 (1/2," in out
    assert "Rival -2 (2/2," in out
    assert "elapsed" in out and "/attempt" in out
    assert results[0]["win"] is False and results[-1]["win"] is True


def test_run_titan_arena_timeout_sets_structured_flag(mock_client, mock_sleep):
    """Bridge timeouts carry bridge_timeout=True; legacy message still matches."""
    from hw_genie.battle.engine import BridgeTimeoutError
    from hw_genie.commands.titan_arena import _is_bridge_timeout, run_titan_arena

    client, mock_call = mock_client
    battle = {
        "type": "titan_arena",
        "seed": 1,
        "userId": "1",
        "typeId": "-1",
        "attackers": {"4003": {"id": 4003, "power": 100, "hp": 1000}},
        "defenders": [{"4000": {"id": 4000, "power": 1, "hp": 100}}],
        "effects": [],
        "reward": [],
        "startTime": 0,
    }
    mock_call.side_effect = [_ok({"response": {"battle": battle}})]

    class DeadEngine:
        def calc(self, battle):
            raise BridgeTimeoutError("userscript did not finish battle within 5s")

    res = run_titan_arena(client, rival_id="-1", titans=[1, 2, 3, 4, 5], engine=DeadEngine())
    assert res["bridge_timeout"] is True
    assert _is_bridge_timeout(res) is True
    # Legacy fallback for dicts built without the flag.
    assert _is_bridge_timeout({"bridge_error": "userscript did not finish battle within 5s"}) is True
    assert _is_bridge_timeout({"bridge_error": "boom"}) is False
    assert _is_bridge_timeout({}) is False


def test_tier_stops_after_consecutive_no_progress_passes(mock_client, mock_sleep, mocker):
    """canRaid tier with zero wins stops after 3 passes instead of grinding."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    mock_call.return_value = _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}})
    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0}}, "canRaid": True}
    calls = {"rivals": 0}

    def fake_rivals(*a, **k):
        calls["rivals"] += 1
        return [{"rivalId": "-1", "win": False, "team": [1, 2, 3, 4, 5]}]

    mocker.patch("hw_genie.commands.titan_arena.fetch_titan_arena_status", return_value=status)
    mocker.patch(
        "hw_genie.commands.titan_arena._run_raid",
        return_value={"stage": "raid", "battles": [], "completed": False},
    )
    mocker.patch("hw_genie.commands.titan_arena._run_rivals", side_effect=fake_rivals)
    mocker.patch("hw_genie.commands.titan_arena._complete_tier")
    mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)

    summary = run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    assert calls["rivals"] == 3
    assert summary["errors"] == []


def test_tier_raid_completed_still_farms_daily_reward(mock_client, mock_sleep, mocker):
    """A raid-only final-tier clear must not skip the daily chest."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    mock_call.return_value = _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}})
    statuses = [
        {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0}}, "canRaid": True},
        {"status": "peace_time", "tier": 8, "rivals": {}},
    ]
    farm_calls = []
    mocker.patch("hw_genie.commands.titan_arena.fetch_titan_arena_status", side_effect=statuses)
    mocker.patch(
        "hw_genie.commands.titan_arena._run_raid",
        return_value={"stage": "raid", "battles": [], "completed": True},
    )
    mocker.patch("hw_genie.commands.titan_arena._complete_tier")
    mocker.patch(
        "hw_genie.commands.titan_arena._farm_daily_reward",
        side_effect=lambda *a, **k: farm_calls.append(1) or True,
    )

    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    assert len(farm_calls) == 1


def test_tier_non_raid_stops_after_single_zero_win_pass(mock_client, mock_sleep, mocker):
    """canRaid=False with zero wins stops after exactly 1 pass (fast path)."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    mock_call.return_value = _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}})
    status = {"status": "battle", "tier": 7, "rivals": {"-1": {"attackScore": 0}}, "canRaid": False}
    calls = {"rivals": 0}

    def fake_rivals(*a, **k):
        calls["rivals"] += 1
        return [{"rivalId": "-1", "win": False, "team": [1, 2, 3, 4, 5]}]

    mocker.patch("hw_genie.commands.titan_arena.fetch_titan_arena_status", return_value=status)
    mocker.patch("hw_genie.commands.titan_arena._run_rivals", side_effect=fake_rivals)
    mocker.patch("hw_genie.commands.titan_arena._complete_tier")
    mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)

    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    assert calls["rivals"] == 1


def test_tier_raid_win_resets_no_progress_counter(mock_client, mock_sleep, mocker):
    """A banked raid win + zero-win rivals must not increment the counter."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    mock_call.return_value = _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}})
    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0}}, "canRaid": True}
    calls = {"rivals": 0}

    def fake_rivals(*a, **k):
        calls["rivals"] += 1
        return [{"rivalId": "-1", "win": False, "team": [1, 2, 3, 4, 5]}]

    raid_results = [
        {"stage": "raid", "battles": [{"rivalId": "-1", "win": True}], "completed": False},
        {"stage": "raid", "battles": [{"rivalId": "-1", "win": False}], "completed": False},
        {"stage": "raid", "battles": [{"rivalId": "-1", "win": False}], "completed": False},
        {"stage": "raid", "battles": [{"rivalId": "-1", "win": False}], "completed": False},
    ]

    mocker.patch("hw_genie.commands.titan_arena.fetch_titan_arena_status", return_value=status)
    mocker.patch("hw_genie.commands.titan_arena._run_raid", side_effect=list(raid_results))
    mocker.patch("hw_genie.commands.titan_arena._run_rivals", side_effect=fake_rivals)
    mocker.patch("hw_genie.commands.titan_arena._complete_tier")
    mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)

    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    # First pass banks a raid win (counter stays 0); the next 3 zero-win
    # passes then trip the >=3 stop. Buggy code ignoring raid wins stops
    # after 3 passes instead.
    assert calls["rivals"] == 4


def test_run_titan_arena_shows_account_label(capsys, mock_client, mock_sleep):
    """Account label appears on the battle header line when given."""
    from hw_genie.commands.titan_arena import run_titan_arena

    client, mock_call = mock_client
    battle = {
        "type": "titan_arena", "seed": 1,
        "attackers": {"1": {"power": 999999, "hp": 10}},
        "defenders": [{"2": {"power": 1, "hp": 10}}],
    }
    mock_call.side_effect = [
        _ok({"response": {"battle": battle}}),
        _ok({"response": {"attackScore": 250}}),
    ]
    run_titan_arena(client, rival_id="-1", titans=[1, 2, 3, 4, 5],
                    engine=PythonBattleEngine(), account_label="Alice")
    out = capsys.readouterr().out
    assert "Titan Arena: [Alice] rivalId=-1" in out


def test_end_battle_win_uses_victory_emoji_loss_keeps_success(capsys, mock_client, mock_sleep):
    """Wins print 🏆, losses keep ✅ so they are visually distinct from Start."""
    from hw_genie.commands.titan_arena import run_titan_arena

    client, mock_call = mock_client
    win_battle = {
        "type": "titan_arena", "seed": 1,
        "attackers": {"1": {"power": 999999, "hp": 10}},
        "defenders": [{"2": {"power": 1, "hp": 10}}],
    }
    lose_battle = {
        "type": "titan_arena", "seed": 2,
        "attackers": {"1": {"power": 1, "hp": 10}},
        "defenders": [{"2": {"power": 999999, "hp": 10}}],
    }
    mock_call.side_effect = [
        _ok({"response": {"battle": win_battle}}),
        _ok({"response": {"attackScore": 250}}),
        _ok({"response": {"battle": lose_battle}}),
        _ok({"response": {"attackScore": 10}}),
    ]
    run_titan_arena(client, rival_id="-1", titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    run_titan_arena(client, rival_id="-2", titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    out = capsys.readouterr().out
    assert len([line for line in out.splitlines() if line.startswith("🏆 EndBattle")]) == 1
    assert len([line for line in out.splitlines() if line.startswith("✅ EndBattle")]) == 1
    # Start lines keep ✅ and stay distinct from wins.
    assert len([line for line in out.splitlines() if "StartBattle accepted" in line]) == 2


def test_run_titan_arena_tier_prints_account_banner(capsys, mock_client, mock_sleep):
    """Tier banner shows the account when a label is given; silent otherwise."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}}),
        _ok({"response": {"status": "peace_time", "tier": 1, "rivals": {}}}),
    ]
    run_titan_arena_tier(client, titans=None, engine=PythonBattleEngine(), account_label="Alice")
    out = capsys.readouterr().out
    assert "account: Alice" in out


def test_run_rivals_forwards_account_label(mock_client, mock_sleep, mocker):
    """_run_rivals passes the account label down to each battle."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    seen = []

    def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
        seen.append(kw.get("account_label"))
        return {"estimate": MagicMock(win=True)}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    _run_rivals(client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
                threshold=250, stop_on_first_loss=False,
                team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=1,
                account_label="Carol")
    assert seen and all(label == "Carol" for label in seen)


def test_run_titan_arena_tier_records_final_tier_and_remaining(mock_client, mock_sleep):
    """Tier summary tracks the last seen tier and remaining target count."""
    client, mock_call = mock_client
    rivals = {
        "100": {"userId": "100", "power": "100", "titans": {}, "attackScore": 0, "seed": "0", "defenceScore": "0"},
        "101": {"userId": "101", "power": "100", "titans": {}, "attackScore": 100, "seed": "0", "defenceScore": "0"},
    }
    mock_call.side_effect = [
        _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}}),
        _ok({"response": {"status": "peace_time", "tier": 7, "rivals": rivals}}),
    ]
    summary = run_titan_arena_tier(client, titans=None, engine=PythonBattleEngine())
    assert summary["final_tier"] == 7
    assert summary["remaining_rivals"] == 2


def test_run_rivals_estimate_only_fallback_never_wins(mock_client, mock_sleep, mocker):
    """Pure estimate-only (no EndBattle sent) must not count as a win."""
    from hw_genie.commands.titan_arena import _run_rivals

    # Pure estimate-only without bridge_error (explicit --estimate-only mode):
    # stays on the unverified path without tripping the dead-bridge abort.
    # Estimate claims win, but nothing was banked so win must be False.
    def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
        return {"estimate": MagicMock(win=True), "estimate_only": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, mock_call = mock_client
    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0}}}
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=None, max_attempts_per_rival=3,
    )
    assert len(results) == 3
    assert all(r["win"] is False for r in results)
    assert all(r.get("unverified") is True for r in results)


def test_run_raid_bridge_breaker_resets_on_success(mock_client, mock_sleep):
    """Interleaved bridge errors must not trip the outage breaker."""
    from hw_genie.battle.engine import BridgeTimeoutError
    from hw_genie.commands.titan_arena import _run_raid

    calls = {"n": 0}

    class FlakyEngine:
        def calc(self, battle):
            calls["n"] += 1
            if calls["n"] % 2 == 1:
                raise BridgeTimeoutError("userscript did not finish battle within 5s")
            from hw_genie.battle.engine import BattleEstimate

            return BattleEstimate(
                win=True,
                stars=3,
                progress=[{"attackers": {"heroes": {"1": {"hp": 10}}}, "defenders": {"heroes": {}}}],
            )

    client, mock_call = mock_client
    status = {"status": "battle", "tier": 1, "userId": "1", "rivals": {}}
    start_payload = {"attackers": {"4003": {}}, "rivals": {f"r{i}": {"t": i} for i in range(1, 6)}}
    mock_call.side_effect = [
        _ok({"response": start_payload}),
        _ok({"response": {"results": {}}}),
    ]
    summary = _run_raid(client, status, [1, 2, 3, 4, 5], FlakyEngine(), 250)
    assert len(summary["battles"]) == 5
    assert "error" not in summary


def test_has_any_heroes():
    """Empty-heroes dummy progress is detectable; genuine results pass."""
    from hw_genie.commands.titan_arena import _has_any_heroes

    assert _has_any_heroes([{"attackers": {"heroes": {}}, "defenders": {"heroes": {}}}]) is False
    assert _has_any_heroes([]) is False
    assert _has_any_heroes("nope") is False
    assert _has_any_heroes([{"attackers": {"heroes": {"1": {"hp": 0}}}, "defenders": {"heroes": {}}}]) is True


def test_run_raid_dummy_loss_excluded_from_endraid(mock_client, mock_sleep):
    """Userscript dummy losses are skipped so they can't void the raid."""
    from hw_genie.battle.engine import BattleEstimate
    from hw_genie.commands.titan_arena import _run_raid

    def calc(battle):
        if battle["typeId"] == "r1":
            return BattleEstimate(
                win=False, stars=0,
                progress=[{"attackers": {"heroes": {}}, "defenders": {"heroes": {}}}],
            )
        return BattleEstimate(
            win=True,
            stars=3,
            progress=[{"attackers": {"heroes": {"1": {"hp": 10}}}, "defenders": {"heroes": {}}}],
        )

    class DummyEngine:
        pass

    DummyEngine.calc = staticmethod(calc)

    client, mock_call = mock_client
    status = {"status": "battle", "tier": 1, "userId": "1", "rivals": {}}
    start_payload = {"attackers": {"4003": {}}, "rivals": {"r1": {"t": 1}, "r2": {"t": 2}}}
    mock_call.side_effect = [
        _ok({"response": start_payload}),
        _ok({"response": {"results": {"r2": {"attackScore": 250}}}}),
    ]
    summary = _run_raid(client, status, [1, 2, 3, 4, 5], DummyEngine(), 250)
    end_args = mock_call.call_args_list[1][0][0]["calls"][0]["args"]["results"]
    assert set(end_args) == {"r2"}
    assert summary["battles"][0] == {"rivalId": "r1", "win": False, "dummy": True}


def test_run_rivals_stop_on_first_loss_ignores_unverified(mock_client, mock_sleep, mocker):
    """Unverified attempts never trigger the stop_on_first_loss abort."""
    from hw_genie.commands.titan_arena import _run_rivals

    # Pure estimate-only without bridge_error: unverified but not a bridge
    # failure, so the full plan runs without either abort tripping.
    def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
        return {"estimate": MagicMock(win=False), "estimate_only": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0}}}
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=True,
        team_rotation=None, max_attempts_per_rival=3,
    )
    assert len(results) == 3
    assert all(r.get("unverified") is True for r in results)


def test_run_raid_dummy_resets_breaker(mock_client, mock_sleep):
    """A dummy proves bridge contact, so it resets the consecutive counter."""
    from hw_genie.battle.engine import BattleEstimate, BridgeTimeoutError
    from hw_genie.commands.titan_arena import _run_raid

    calls = {"n": 0}

    def calc(battle):
        calls["n"] += 1
        if battle["typeId"] in ("r1", "r2", "r4"):
            raise BridgeTimeoutError("userscript did not finish battle within 5s")
        return BattleEstimate(
            win=False,
            stars=0,
            progress=[{"attackers": {"heroes": {}}, "defenders": {"heroes": {}}}],
        )

    class MixedEngine:
        pass

    MixedEngine.calc = staticmethod(calc)

    client, mock_call = mock_client
    status = {"status": "battle", "tier": 1, "userId": "1", "rivals": {}}
    start_payload = {"attackers": {"4003": {}}, "rivals": {"r1": {"t": 1}, "r2": {"t": 2}, "r3": {"t": 3}, "r4": {"t": 4}}}
    mock_call.side_effect = [_ok({"response": start_payload})]
    # error,error,dummy(reset),error → must NOT raise (would raise at 3 without reset).
    summary = _run_raid(client, status, [1, 2, 3, 4, 5], MixedEngine(), 250)
    assert len(summary["battles"]) == 4
    assert summary["battles"][2] == {"rivalId": "r3", "win": False, "dummy": True}


def test_run_rivals_non_timeout_bridge_errors_raise_dead(mock_client, mock_sleep, mocker):
    """3 consecutive non-timeout bridge errors raise BridgeDeadError fast."""
    import pytest

    from hw_genie.battle.engine import BridgeDeadError
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": MagicMock(win=False), "bridge_error": "auth-server 404", "estimate_only": True, "bridge_timeout": False}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    rotation = [[i, i + 1, i + 2, i + 3, i + 4] for i in range(5)]
    with pytest.raises(BridgeDeadError) as excinfo:
        _run_rivals(
            client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
            threshold=250, stop_on_first_loss=False,
            team_rotation=rotation, seeds_per_team=2, end_on_loss=False,
        )
    # Large plan (5x2=10) but abort after 3 consecutive bridge errors.
    assert len(calls) == 3
    assert excinfo.value.partial_results == []


def test_run_rivals_mixed_timeout_and_non_timeout_count_together(mock_client, mock_sleep, mocker):
    """Timeouts + non-timeout bridge errors share one consecutive counter."""
    import pytest

    from hw_genie.battle.engine import BridgeDeadError
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    seq = [
        {"estimate": MagicMock(win=False), "bridge_error": "userscript did not finish battle within 5s", "estimate_only": True, "bridge_timeout": True},
        {"estimate": MagicMock(win=False), "bridge_error": "Connection refused", "estimate_only": True, "bridge_timeout": False},
        {"estimate": MagicMock(win=False), "bridge_error": "userscript did not finish battle within 5s", "estimate_only": True, "bridge_timeout": True},
    ]
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return seq[len(calls) - 1]

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    with pytest.raises(BridgeDeadError):
        _run_rivals(
            client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
            threshold=250, stop_on_first_loss=False,
            team_rotation=None, max_attempts_per_rival=9, end_on_loss=False,
        )
    assert len(calls) == 3


def test_run_rivals_bridge_counter_resets_on_verified(mock_client, mock_sleep, mocker):
    """Verified contact resets the consecutive bridge-error counter."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    seq = [
        {"estimate": MagicMock(win=False), "bridge_error": "Connection refused", "estimate_only": True, "bridge_timeout": False},
        {"estimate": MagicMock(win=False)},
        {"estimate": MagicMock(win=False), "bridge_error": "Connection refused", "estimate_only": True, "bridge_timeout": False},
        {"estimate": MagicMock(win=False), "bridge_error": "Connection refused", "estimate_only": True, "bridge_timeout": False},
    ]
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return seq[len(calls) - 1]

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    # error,verified(reset),error,error → only 2 consecutive at the end, no raise.
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=None, max_attempts_per_rival=4, end_on_loss=False,
    )
    assert len(calls) == 4
    assert len(results) == 1
    assert results[0]["win"] is False
    assert results[0].get("unverified") is None


def test_run_rivals_pure_estimate_only_does_not_trigger_dead(mock_client, mock_sleep, mocker):
    """Pure estimate-only without bridge_error is not a bridge failure."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": MagicMock(win=False), "estimate_only": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    rotation = [[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]]
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=rotation, seeds_per_team=2, end_on_loss=False,
    )
    assert len(calls) == 4
    assert len(results) == 4
    assert all(r.get("unverified") is True for r in results)
    assert all(r["win"] is False for r in results)


def test_tier_cancel_stops_after_current_attempt(mock_client, mocker):
    """Cancel set mid-run stops after the current attempt with interrupted summary."""
    from hw_genie import runner
    from hw_genie.battle.engine import PythonBattleEngine
    from hw_genie.commands.titan_arena import run_titan_arena_tier
    from hw_genie.runner import summarize_toe

    runner.reset_cancel()
    try:
        client, mock_call = mock_client
        status = {
            "status": "battle", "tier": 7, "canRaid": False,
            "rivals": {"-1": {"attackScore": 0}, "-2": {"attackScore": 0}},
        }
        mock_call.side_effect = [_ok({"response": status})]
        calls: list[str] = []

        def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
            calls.append(str(rival_id))
            if len(calls) == 1:
                runner.request_cancel()
                return {"estimate": MagicMock(win=True)}
            return {"estimate": MagicMock(win=True)}

        mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
        mocker.patch("hw_genie.commands.titan_arena._complete_tier")
        mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)
        summary = run_titan_arena_tier(
            client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        )
        assert summary.get("interrupted") is True
        assert any(e.get("stage") == "interrupted" for e in summary.get("errors", []))
        # Only the in-flight rival finished; the next rival never started and
        # no EndBattle was fabricated for it.
        assert calls == ["-1"]
        assert len(summary["rival_results"]) == 1
        assert summary["rival_results"][0]["win"] is True
        assert summary["completed_tier"] is False
        # User-aborted tiers count as COMPLETE (ok), not failed.
        assert summarize_toe([("acc", (summary, None))]) == 0
    finally:
        runner.reset_cancel()


def test_bridge_calc_raises_promptly_on_cancel(monkeypatch):
    """JsBridge poll loop aborts fast on cancel instead of waiting 30s."""
    import json
    import threading
    import time
    import urllib.request

    import pytest

    from hw_genie import runner
    from hw_genie.battle.engine import JsBridgeBattleEngine

    runner.reset_cancel()
    try:
        eng = JsBridgeBattleEngine(
            auth_server_url="http://127.0.0.1:8765",
            user_id="1",
            poll_interval=0.01,
            timeout=30.0,
        )

        class _FakeResp:
            def __init__(self, payload):
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

        def fake_urlopen(req, timeout=5):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url.endswith("/toe/job"):
                return _FakeResp({"id": "job123"})
            return _FakeResp({"status": "pending"})

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        timer = threading.Timer(0.05, runner.request_cancel)
        timer.start()
        try:
            start = time.monotonic()
            with pytest.raises(InterruptedError):
                eng.calc({"attackers": {}})
            elapsed = time.monotonic() - start
        finally:
            timer.cancel()
        assert elapsed < 5.0
    finally:
        runner.reset_cancel()


def test_cmd_toe_run_keyboard_interrupt_during_rivals_exits_zero(mock_client, mock_sleep, mocker):
    """Single `toe run`: cooperative KI inside _run_rivals is a clean complete (exit 0)."""
    from hw_genie.main import cmd_toe_run

    client, mock_call = mock_client
    mock_call.side_effect = [
        _ok({"response": {"status": "battle", "tier": 7, "canRaid": False,
                          "rivals": {"-1": {"attackScore": 0}}}}),
    ]
    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.HWClient", return_value=client)
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=PythonBattleEngine())
    mocker.patch(
        "hw_genie.commands.titan_arena._run_rivals",
        side_effect=KeyboardInterrupt(),
    )
    args = MagicMock()
    args.account = "TestUser"
    args.titans = [1, 2, 3, 4, 5]
    args.threshold = 250
    args.stop_on_loss = False
    args.engine = "estimate"
    args.auth_server_url = "http://127.0.0.1:8765"
    args.seeds = 2
    args.max_attempts = None
    cmd_toe_run(args)  # must not raise SystemExit


def test_cmd_toe_run_interrupted_summary_exits_zero(mocker):
    """Single `toe run`: a returned interrupted summary without real errors exits 0."""
    from hw_genie.main import cmd_toe_run

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.HWClient")
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=PythonBattleEngine())
    mocker.patch(
        "hw_genie.commands.titan_arena.run_titan_arena_tier",
        return_value={"interrupted": True, "rival_results": [], "errors": [{"stage": "interrupted"}]},
    )
    args = MagicMock()
    args.account = "TestUser"
    args.titans = None
    args.threshold = 250
    args.stop_on_loss = False
    args.engine = "estimate"
    args.auth_server_url = "http://127.0.0.1:8765"
    args.seeds = 2
    args.max_attempts = None
    cmd_toe_run(args)  # must not raise SystemExit


def test_cmd_toe_run_interrupted_summary_with_real_errors_exits_1(mocker):
    """Single `toe run`: interrupted + real errors exits 1 (not 130)."""
    from hw_genie.main import cmd_toe_run

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.HWClient")
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=PythonBattleEngine())
    mocker.patch(
        "hw_genie.commands.titan_arena.run_titan_arena_tier",
        return_value={
            "interrupted": True,
            "rival_results": [],
            "errors": [{"stage": "interrupted"}, {"stage": "rivals", "message": "bridge dead"}],
        },
    )
    args = MagicMock()
    args.account = "TestUser"
    args.titans = None
    args.threshold = 250
    args.stop_on_loss = False
    args.engine = "estimate"
    args.auth_server_url = "http://127.0.0.1:8765"
    args.seeds = 2
    args.max_attempts = None
    with pytest.raises(SystemExit) as exc:
        cmd_toe_run(args)
    assert exc.value.code == 1


# --- Compact ToE progress output (quiet|line|verbose) ---


def test_progress_function_defaults_stay_verbose():
    """Public functions keep verbose default so existing callers are unchanged."""
    import inspect

    from hw_genie.commands.titan_arena import run_titan_arena, run_titan_arena_tier

    assert inspect.signature(run_titan_arena).parameters["progress"].default == "verbose"
    assert inspect.signature(run_titan_arena_tier).parameters["progress"].default == "verbose"


def test_quiet_suppresses_per_attempt_lines(capsys, mock_client, mock_sleep):
    """quiet/line suppress noise but keep the decided EndBattle summary."""
    from hw_genie.commands.titan_arena import run_titan_arena

    client, mock_call = mock_client
    battle = {
        "type": "titan_arena", "seed": 1,
        "attackers": {"1": {"power": 999999, "hp": 10}},
        "defenders": [{"2": {"power": 1, "hp": 10}}],
    }
    for mode in ("quiet", "line"):
        mock_call.side_effect = [
            _ok({"response": {"battle": battle}}),
            _ok({"response": {"attackScore": 250}}),
        ]
        run_titan_arena(client, rival_id="-1", titans=[1, 2, 3, 4, 5],
                        engine=PythonBattleEngine(), progress=mode)
        out = capsys.readouterr().out
        assert "Titan Arena:" not in out
        assert "StartBattle accepted" not in out
        assert "Engine:" not in out
        assert "Calling titanArenaEndBattle" not in out
        assert "EndBattle:" in out
        assert ("WIN" in out or "LOSS" in out)


def test_quiet_rivals_keep_decided_lines_but_drop_plan(capsys, mock_client, mock_sleep, mocker):
    """quiet drops plan/rival headers and pace lines but keeps WIN decided lines."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}

    def fake_run(client, rival_id=None, titans=None, engine=None, **kw):
        return {"estimate": MagicMock(win=True)}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=1, progress="quiet",
    )
    out = capsys.readouterr().out
    assert results[0]["win"] is True
    assert "Attempt plan per rival" not in out
    assert "Rival -1 (1/1" not in out
    assert "elapsed" not in out
    assert "rival -1: WIN" in out


def test_line_mode_non_tty_throttles_status_lines(capsys, mock_client, mock_sleep, mocker, monkeypatch):
    """Non-TTY line mode emits throttled plain lines with [a/b] and pace."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        return {"estimate": MagicMock(win=False), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    # Freeze time: all attempts within one throttle window (5s) → 1 status line.
    times = iter([100.0 + i * 0.1 for i in range(50)])
    monkeypatch.setattr("time.monotonic", lambda: next(times))
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]], seeds_per_team=2,
        progress="line", account_label="Alice",
    )
    out = capsys.readouterr().out
    assert len(results) == 4
    assert "\r" not in out
    status_lines = [line for line in out.splitlines() if line.startswith("[Alice] rival -1 [")]
    assert len(status_lines) == 1, f"expected 1 throttled line, got {status_lines}"
    assert "[1/4]" in status_lines[0]
    assert "wins 0" in status_lines[0]
    assert "pace" in status_lines[0] and "/attempt" in status_lines[0]
    # Rival-decided summary still present alongside the status line.
    assert "rival -1: no win after 4 attempts" in out


def test_line_mode_tty_uses_carriage_return(capsys, mock_client, mock_sleep, mocker, monkeypatch):
    """TTY line mode updates in place with \\r instead of new lines."""
    from hw_genie.commands import titan_arena as ta_mod
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        return {"estimate": MagicMock(win=True)}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    monkeypatch.setattr(ta_mod, "_is_tty", lambda: True)
    client, _ = mock_client
    _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=1,
        progress="line", account_label="Alice",
    )
    out = capsys.readouterr().out
    assert "\r[Alice] rival -1 [1/1]" in out
    assert "rival -1: WIN" in out


def test_engine_heartbeat_suppressed_outside_verbose(capsys, monkeypatch):
    """Bridge heartbeats print only in verbose mode (quiet/line stay silent)."""
    import json as _json

    from hw_genie.battle.engine import JsBridgeBattleEngine
    from hw_genie.commands.titan_arena import _engine_calc

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return _json.dumps(self._payload).encode()

    polls = {"n": 0}

    def fake_urlopen(req, timeout=5):
        if req.full_url.endswith("/toe/job"):
            return FakeResp({"id": "abcdef1234567890"})
        polls["n"] += 1
        if polls["n"] < 4:
            return FakeResp({"status": "pending"})
        return FakeResp({"status": "done", "result": {"progress": [], "result": {"win": True, "stars": 3}}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    eng = JsBridgeBattleEngine("http://127.0.0.1:1", "u", poll_interval=0.01, timeout=5, heartbeat_interval=0.02)
    _engine_calc(eng, {"attackers": {}, "defenders": [{}]}, progress="quiet")
    assert "waiting for userscript job" not in capsys.readouterr().out
    polls["n"] = 0
    _engine_calc(eng, {"attackers": {}, "defenders": [{}]}, progress="line")
    assert "waiting for userscript job" not in capsys.readouterr().out
    polls["n"] = 0
    _engine_calc(eng, {"attackers": {}, "defenders": [{}]}, progress="verbose")
    assert "waiting for userscript job" in capsys.readouterr().out


def _bridge_engine():
    from hw_genie.battle.engine import JsBridgeBattleEngine

    return JsBridgeBattleEngine(auth_server_url="http://127.0.0.1:9", user_id="1")


def _loss(stars):
    from hw_genie.battle.engine import BattleEstimate

    return BattleEstimate(win=False, stars=stars, progress=[])


def test_run_rivals_banks_best_loss_after_full_plan(mock_client, mock_sleep, mocker):
    """No win in the plan → one extra EndBattle with the highest-stars team."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append((list(titans), end_on_loss))
        if end_on_loss:
            return {"estimate": _loss(1)}
        stars = 2 if list(titans) == [9, 9, 9, 9, 9] else 0
        return {"estimate": _loss(stars), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]], seeds_per_team=1, end_on_loss=False,
    )
    assert calls == [([1, 2, 3, 4, 5], False), ([9, 9, 9, 9, 9], False), ([9, 9, 9, 9, 9], True)]
    assert len(results) == 3
    assert results[-1]["banked_loss"] is True
    assert results[-1]["win"] is False
    assert results[-1]["team"] == [9, 9, 9, 9, 9]


def test_run_rivals_best_loss_fallback_win_counts(mock_client, mock_sleep, mocker):
    """A win on the best-loss banking attempt is recorded as a win."""
    from hw_genie.battle.engine import BattleEstimate
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        if end_on_loss:
            return {"estimate": BattleEstimate(win=True, stars=1, progress=[])}
        return {"estimate": _loss(0), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=2, end_on_loss=False,
    )
    assert results[-1]["banked_loss"] is True
    assert results[-1]["win"] is True


def test_run_rivals_no_best_loss_fallback_for_estimate_engine(mock_client, mock_sleep, mocker):
    """Estimate-engine progress is always rejected, so no banking attempt."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append((list(titans), end_on_loss))
        return {"estimate": _loss(0), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=2, end_on_loss=False,
    )
    assert len(calls) == 2
    assert all("banked_loss" not in r for r in results)


def test_run_rivals_no_best_loss_fallback_when_opted_out(mock_client, mock_sleep, mocker):
    """bank_best_loss=False disables the extra banking attempt."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append((list(titans), end_on_loss))
        return {"estimate": _loss(0), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=2, end_on_loss=False,
        bank_best_loss=False,
    )
    assert len(calls) == 2
    assert all("banked_loss" not in r for r in results)


def test_run_rivals_no_best_loss_fallback_all_unverified(mock_client, mock_sleep, mocker):
    """Unverified-only rivals carry no bankable result, so no fallback."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": _loss(0), "estimate_only": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=2, end_on_loss=False,
    )
    assert len(calls) == 2
    assert all(r.get("unverified") for r in results)
    assert all("banked_loss" not in r for r in results)


def test_run_rivals_no_best_loss_fallback_on_stop_on_loss(mock_client, mock_sleep, mocker):
    """--stop-on-loss keeps its fast abort without a banking attempt."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": _loss(0), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=True,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=2, end_on_loss=False,
    )
    assert len(calls) == 1
    assert all("banked_loss" not in r for r in results)


def test_tier_threads_bank_best_loss_to_rivals(mock_client, mock_sleep, mocker):
    """run_titan_arena_tier forwards bank_best_loss to _run_rivals (default True)."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    seen = []
    status = {"status": "battle", "tier": 7, "rivals": {"-1": {"attackScore": 0}}, "canRaid": False}
    mock_call.side_effect = [_ok({"response": status}), _ok({"response": status})]

    def spy(client, status, titans, engine, threshold, stop, **kw):
        seen.append(kw.get("bank_best_loss"))
        return []

    mocker.patch("hw_genie.commands.titan_arena._run_rivals", side_effect=spy)
    mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)
    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(), bank_best_loss=False)
    assert seen == [True, False]


def test_cmd_toe_run_bank_best_loss_default_and_opt_out(mocker):
    """toe run banks by default; --no-bank-best-loss disables it."""
    from hw_genie.main import cmd_toe_run

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.main.HWClient")
    mock_run = mocker.patch("hw_genie.commands.titan_arena.run_titan_arena_tier")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=PythonBattleEngine())

    def make_args(no_bank):
        args = MagicMock()
        args.account = "TestUser"
        args.titans = None
        args.threshold = 250
        args.stop_on_loss = False
        args.engine = "estimate"
        args.auth_server_url = "http://127.0.0.1:8765"
        args.seeds = 2
        args.max_attempts = None
        args.no_bank_best_loss = no_bank
        return args

    cmd_toe_run(make_args(False))
    assert mock_run.call_args.kwargs.get("bank_best_loss") is True
    cmd_toe_run(make_args(True))
    assert mock_run.call_args.kwargs.get("bank_best_loss") is False


def test_run_rivals_best_loss_tie_break_earliest_first(mock_client, mock_sleep, mocker):
    """Equal stars → the earliest-seen losing team is banked."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append((list(titans), end_on_loss))
        if end_on_loss:
            return {"estimate": _loss(1)}
        return {"estimate": _loss(2), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]], seeds_per_team=1, end_on_loss=False,
    )
    assert calls[-1] == ([1, 2, 3, 4, 5], True)
    assert results[-1]["team"] == [1, 2, 3, 4, 5]
    assert results[-1]["banked_loss"] is True


def test_loss_stars_non_int_and_none():
    """Non-int/None stars rank as 0 instead of raising."""
    from hw_genie.commands.titan_arena import _loss_stars

    assert _loss_stars(MagicMock(stars=None)) == 0
    assert _loss_stars(MagicMock(stars="x")) == 0
    assert _loss_stars(MagicMock(stars=2.9)) == 2
    assert _loss_stars(MagicMock(stars=3)) == 3
    assert _loss_stars(object()) == 0


def test_run_rivals_fallback_bridge_dead_raise(mock_client, mock_sleep, mocker):
    """Fallback timeout completing 3 consecutive bridge errors raises BridgeDeadError."""
    import pytest

    from hw_genie.battle.engine import BridgeDeadError
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(end_on_loss)
        if end_on_loss:
            return {"estimate": _loss(0), "bridge_error": "userscript did not finish battle within 5s", "estimate_only": True, "bridge_timeout": True}
        if len(calls) == 1:
            return {"estimate": _loss(1), "abandoned": True}
        return {"estimate": _loss(0), "bridge_error": "userscript did not finish battle within 5s", "estimate_only": True, "bridge_timeout": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    with pytest.raises(BridgeDeadError) as excinfo:
        _run_rivals(
            client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
            threshold=250, stop_on_first_loss=False,
            team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=3, end_on_loss=False,
        )
    # attempt1 verified loss (resets counter, banks the candidate),
    # attempts 2-3 timeouts (counter=2), fallback timeout → 3rd → dead.
    assert calls == [False, False, False, True]
    assert len(excinfo.value.partial_results) == 2
    assert excinfo.value.partial_results[0]["win"] is False
    assert excinfo.value.partial_results[-1].get("banked_loss") is True
    assert excinfo.value.partial_results[-1].get("unverified") is True


def test_run_rivals_fallback_beaten_already_cleared_and_not(mock_client, mock_sleep, mocker):
    """Fallback beaten-already → WIN when cleared, loss otherwise."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        if end_on_loss:
            return {"error": "NotAvailable", "detail": {"description": "beaten up already"}}
        return {"estimate": _loss(1), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    mocker.patch("hw_genie.commands.titan_arena._is_already_cleared", return_value=True)
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=1, end_on_loss=False,
    )
    assert results[-1] == {"rivalId": "-1", "win": True, "already_cleared": True}

    mocker.patch("hw_genie.commands.titan_arena._is_already_cleared", return_value=False)
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=1, end_on_loss=False,
    )
    assert results[-1]["win"] is False
    assert results[-1]["banked_loss"] is True
    assert results[-1]["start_error"] == "NotAvailable"


def test_run_rivals_cancel_before_fallback(mock_client, mock_sleep, mocker):
    """Cancel set during the last plan attempt aborts before the banking attempt."""
    import pytest

    from hw_genie import runner
    from hw_genie.commands.titan_arena import _run_rivals

    runner.reset_cancel()
    try:
        status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
        calls = []

        def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
            calls.append(end_on_loss)
            runner.request_cancel()
            return {"estimate": _loss(1), "abandoned": True}

        mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
        client, _ = mock_client
        with pytest.raises(InterruptedError) as excinfo:
            _run_rivals(
                client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
                threshold=250, stop_on_first_loss=False,
                team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=1, end_on_loss=False,
            )
        # Only the plan attempt ran; no banking attempt followed.
        assert calls == [False]
        assert len(excinfo.value.partial_results) == 1
    finally:
        runner.reset_cancel()


def test_tier_raid_runs_before_rivals(mock_client, mock_sleep, mocker):
    """canRaid=True fresh status calls _run_raid before _run_rivals."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    mock_call.return_value = _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}})
    order = []
    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0}}, "canRaid": True}
    mocker.patch(
        "hw_genie.commands.titan_arena.fetch_titan_arena_status",
        side_effect=[status, {"status": "peace_time", "tier": 8, "rivals": {}}],
    )
    mocker.patch(
        "hw_genie.commands.titan_arena._run_raid",
        side_effect=lambda *a, **k: order.append("raid") or {"stage": "raid", "battles": [], "completed": False},
    )
    mocker.patch(
        "hw_genie.commands.titan_arena._run_rivals",
        side_effect=lambda *a, **k: order.append("rivals") or [{"rivalId": "-1", "win": True}],
    )
    mocker.patch("hw_genie.commands.titan_arena._complete_tier")
    mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)

    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    assert order == ["raid", "rivals"]


def test_tier_continues_after_banked_only_pass(mock_client, mock_sleep, mocker):
    """A pass with banked losses (no wins) attempts completion and continues."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    mock_call.return_value = _ok({"response": {"titan_arena": [1, 2, 3, 4, 5]}})
    statuses = [
        {"status": "battle", "tier": 7, "rivals": {"-1": {"attackScore": 0}}, "canRaid": False},
        {"status": "peace_time", "tier": 7, "rivals": {}},
    ]
    fetch = mocker.patch("hw_genie.commands.titan_arena.fetch_titan_arena_status", side_effect=statuses)
    mocker.patch(
        "hw_genie.commands.titan_arena._run_rivals",
        return_value=[{"rivalId": "-1", "win": False, "team": [1, 2, 3, 4, 5], "banked_loss": True}],
    )
    spy_complete = mocker.patch("hw_genie.commands.titan_arena._complete_tier")
    mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)

    summary = run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    assert fetch.call_count == 2
    spy_complete.assert_called_once()
    assert any(r.get("banked_loss") for r in summary["rival_results"])


def test_run_rivals_banking_status_line_total_bumped(mock_client, mock_sleep, mocker, monkeypatch, capsys):
    """Banking status line shows [N+1/N+1], not [N+1/N]."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    ticks = {"t": 100.0}

    def fake_monotonic():
        ticks["t"] += 10.0
        return ticks["t"]

    monkeypatch.setattr("time.monotonic", fake_monotonic)

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        if end_on_loss:
            return {"estimate": _loss(1)}
        return {"estimate": _loss(0), "abandoned": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=_bridge_engine(),
        threshold=250, stop_on_first_loss=False,
        team_rotation=[[1, 2, 3, 4, 5], [9, 9, 9, 9, 9]], seeds_per_team=1, end_on_loss=False,
        progress="line", account_label="Alice",
    )
    out = capsys.readouterr().out
    assert "[3/3]" in out
    assert "[3/2]" not in out


def test_cli_parser_toe_run_defaults_seeds_10(mocker):
    """`toe run` defaults to 10 seeds with banking enabled."""
    from hw_genie.main import main
    import sys

    mocker.patch("hw_genie.core.database.init_db")
    mocker.patch("hw_genie.core.database.install_token_masking_filter")
    mocker.patch("hw_genie.main.setup_logging")
    mock_run = mocker.patch("hw_genie.main.cmd_toe_run")
    sys.argv = ["hw-genie", "toe", "run", "-a", "TestUser"]
    main()
    mock_run.assert_called_once()
    args = mock_run.call_args.args[0]
    assert args.seeds == 10
    assert args.no_end_on_loss is False
    assert args.no_bank_best_loss is False


def test_cmd_toe_run_end_on_loss_default_and_opt_out(mocker):
    """toe run banks losses by default; --no-end-on-loss disables it."""
    from hw_genie.main import cmd_toe_run

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.main.HWClient")
    mock_run = mocker.patch("hw_genie.commands.titan_arena.run_titan_arena_tier")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=PythonBattleEngine())

    def make_args(no_end):
        args = MagicMock()
        args.account = "TestUser"
        args.titans = None
        args.threshold = 250
        args.stop_on_loss = False
        args.engine = "estimate"
        args.auth_server_url = "http://127.0.0.1:8765"
        args.seeds = 10
        args.max_attempts = None
        args.no_bank_best_loss = False
        args.no_end_on_loss = no_end
        return args

    cmd_toe_run(make_args(False))
    assert mock_run.call_args.kwargs.get("end_on_loss") is True
    cmd_toe_run(make_args(True))
    assert mock_run.call_args.kwargs.get("end_on_loss") is False


def test_resolve_toe_end_on_loss_magicmock_safe(mocker):
    """Legacy bare-MagicMock args keep banking enabled (explicit True opts out)."""
    from hw_genie.main import _resolve_toe_end_on_loss

    assert _resolve_toe_end_on_loss(MagicMock()) is True

    class NoFlag:
        pass

    assert _resolve_toe_end_on_loss(NoFlag()) is True
    assert _resolve_toe_end_on_loss(MagicMock(no_end_on_loss=True)) is False
    assert _resolve_toe_end_on_loss(MagicMock(no_end_on_loss=False)) is True


def test_tier_threads_end_on_loss_to_rivals(mock_client, mock_sleep, mocker):
    """run_titan_arena_tier banks losses by default and forwards opt-out."""
    from hw_genie.commands.titan_arena import run_titan_arena_tier

    client, mock_call = mock_client
    seen = []
    status = {"status": "battle", "tier": 7, "rivals": {"-1": {"attackScore": 0}}, "canRaid": False}
    mock_call.side_effect = [_ok({"response": status}), _ok({"response": status})]

    def spy(client, status, titans, engine, threshold, stop, **kw):
        seen.append(kw.get("end_on_loss"))
        return []

    mocker.patch("hw_genie.commands.titan_arena._run_rivals", side_effect=spy)
    mocker.patch("hw_genie.commands.titan_arena._farm_daily_reward", return_value=True)
    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine())
    run_titan_arena_tier(client, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(), end_on_loss=False)
    assert seen == [True, False]
