from unittest.mock import MagicMock

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
    # Sequence: getStatus -> StartBattle -> EndBattle (per rival x2) -> getStatus
    # -> (since complete_tier advances tier we re-call getStatus) -> FarmDailyReward
    mock_call.side_effect = [
        _ok({"response": {"status": "battle", "tier": 7, "rivals": rivals, "canRaid": False}}),
        _ok({"response": {"battle": battle}}),  # StartBattle 100
        _ok({"response": {}}),  # EndBattle 100
        _ok({"response": {"battle": battle}}),  # StartBattle 101
        _ok({"response": {}}),  # EndBattle 101
        _ok({"response": {"status": "battle", "tier": 7, "rivals": rivals, "canRaid": False}}),
        _ok({"response": {}}),  # CompleteTier
        _ok({"response": {}}),  # FarmDailyReward
        _ok({"response": {"status": "peace_time", "tier": 7, "rivals": {}}}),
    ]
    summary = run_titan_arena_tier(
        client,
        titans=[4003, 4023, 4004, 4001, 4000],
        engine=PythonBattleEngine(),
    )
    # The tier sees a rival that was attackScore>=0 so attempts both.
    # The second getStatus is "battle" again (peace_time expected on the
    # next iteration). We bail out because the script returns when no rival
    # has attackScore<250 on the second getStatus pass; here rivals are
    # still present, so it tries again — we expect at least one more pass
    # but the test just checks the tier ran and at least one rival win
    # happened.
    assert any(r.get("win") for r in summary["rival_results"])


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
    cmd_toe_run(args)
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    called_titans = kwargs.get("titans") if "titans" in kwargs else mock_run.call_args.args[1] if len(mock_run.call_args.args) > 1 else None
    assert called_titans is None


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
    assert len(rotation) == 4  # saved + 3 non-duplicate static teams


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
    """3 straight timeouts abort the tier with a hint instead of grinding."""
    from hw_genie.commands.titan_arena import _run_rivals

    status = {"status": "battle", "tier": 8, "rivals": {"-1": {"attackScore": 0, "power": "1"}}}
    calls = []

    def fake_run(client, rival_id=None, titans=None, engine=None, end_on_loss=False, **kw):
        calls.append(1)
        return {"estimate": MagicMock(win=False), "bridge_error": "userscript did not finish battle within 30.0s", "estimate_only": True}

    mocker.patch("hw_genie.commands.titan_arena.run_titan_arena", side_effect=fake_run)
    client, _ = mock_client
    results = _run_rivals(
        client, status, titans=[1, 2, 3, 4, 5], engine=PythonBattleEngine(),
        threshold=250, stop_on_first_loss=False, team_rotation=[[1, 2, 3, 4, 5]], seeds_per_team=9,
    )
    assert len(calls) == 3
    assert results == []
