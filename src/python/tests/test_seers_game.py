from unittest.mock import MagicMock

from hw_genie.core.client import ApiAction


def test_api_action_has_event_picker():
    from hw_genie.core.client import ApiAction as _ApiAction
    assert _ApiAction.EVENT_PICKER_START_GAME == "eventPicker_startGame"
    assert _ApiAction.EVENT_PICKER_PLAY_ROUND == "eventPicker_playRound"
    assert _ApiAction.EVENT_PICKER_FINISH_GAME == "eventPicker_finishGame"
    assert _ApiAction.EVENT_PICKER_GET_STATE == "eventPicker_getState"


def _res_from(dummy_response: dict) -> MagicMock:
    res = MagicMock()
    res.is_success = True
    res.detail = dummy_response["results"][0]["result"]
    return res


def _err_res(name: str = "NotEnough") -> MagicMock:
    res = MagicMock()
    res.is_success = False
    res.error_name = name
    return res


def _unexpected_res(name: str = "network_or_parse_error") -> MagicMock:
    from hw_genie.core.client import ResponseStatus as _RS
    res = MagicMock()
    res.is_success = False
    res.status = _RS.UNEXPECTED
    res.error_name = name
    return res


def _event_envelope(event: dict, extra: dict | None = None) -> dict:
    response = {"event": event}
    if extra:
        response.update(extra)
    return {"results": [{"ident": "body", "result": {"response": response}}]}


_START_EVENT = {
    "id": 2824000007, "round": 1, "size": 3, "state": "active",
    "win_streak": 10, "mark_history": [], "collected_rewards": [],
}

_NEW_GAME_EVENT = {
    "id": 2824000007, "round": 5, "size": 4, "state": "new_game",
    "win_streak": 14, "mark_history": [],
    "collected_rewards": {"consumable": {"12": 40}},
}

_FINISH_EVENT = dict(_NEW_GAME_EVENT)

_INVENTORY_DUMMY = {
    "results": [{"ident": "body", "result": {"response": {"coin": {"2824001094": 75}}}}]
}


def _play_event(round_no: int) -> dict:
    return {
        "id": 2824000007, "round": round_no, "size": 3, "state": "active",
        "win_streak": 10 + round_no, "mark_history": [],
        "collected_rewards": {"consumable": {"12": 20}},
    }


def test_run_single_game_success(mock_client, mock_sleep):
    from hw_genie.commands.seers_game import run_single_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_START_EVENT)),
        _res_from(_event_envelope(_play_event(2), {"result": "win"})),
        _res_from(_event_envelope(_play_event(3), {"result": "win"})),
        _res_from(_event_envelope(_play_event(4), {"result": "win"})),
        _res_from(_event_envelope(_play_event(5), {"result": "win"})),
        _res_from(_event_envelope(_FINISH_EVENT)),
    ]
    result = run_single_game(client, pick=2)
    assert result.games_played == 1
    names = [c["calls"][0]["name"] for c, in (call.args for call in mock_call.call_args_list)]
    assert names.count(ApiAction.EVENT_PICKER_PLAY_ROUND) == 4


def test_run_seers_game_stops_on_not_enough(mock_client, mock_sleep):
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _err_res("NotEnough"),
    ]
    result = run_seers_game(client)
    assert result.games_played == 0
    assert result.last_error is not None


def test_run_seers_game_dry_run_sends_only_reads(mock_client, mock_sleep):
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _res_from(_INVENTORY_DUMMY),
    ]
    result = run_seers_game(client, dry_run=True)
    allowed = {ApiAction.EVENT_PICKER_GET_STATE, ApiAction.INVENTORY_GET}
    sent = {c["calls"][0]["name"] for c, in (call.args for call in mock_call.call_args_list)}
    assert sent <= allowed
    assert sent  # at least the state read was sent
    assert result.games_played == 0


def test_run_seers_game_unexpected_on_start_raises_not_clean_stop(mock_client, mock_sleep):
    """UNEXPECTED はコイン枯渇と区別し、正常停止ではなく SeersGameReadError を送出する。"""
    import pytest
    from hw_genie.commands.seers_game import SeersGameReadError, run_seers_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _unexpected_res("network_or_parse_error"),
    ]
    with pytest.raises(SeersGameReadError):
        run_seers_game(client)
    # 正常停止（ERROR/コイン枯渇）なら例外なく last_error 記録で戻るため、
    # 例外送出そのものが枯渇との区別になる。games_played は 0 のまま。
    assert mock_call.call_count == 2


def test_run_single_game_unexpected_raises(mock_client, mock_sleep):
    import pytest
    from hw_genie.commands.seers_game import SeersGameReadError, run_single_game
    client, mock_call = mock_client
    mock_call.side_effect = [_unexpected_res("network_or_parse_error")]
    with pytest.raises(SeersGameReadError):
        run_single_game(client, pick=2)


def test_seers_game_cli_registered(monkeypatch, capsys):
    import sys

    import pytest

    import hw_genie.main as main_mod

    monkeypatch.setattr(sys, "argv", ["hw-genie", "seers-game", "--help"])
    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--dry-run" in out and "--pick" in out and "--max-games" in out


def _seers_handler_args(pick=2, max_games=None, dry_run=False, account=None):
    import argparse

    return argparse.Namespace(
        pick=pick, max_games=max_games, dry_run=dry_run, account=account
    )


def _patch_seers_handler(monkeypatch, run_fake):
    """cmd_seers_game の外部依存（session / client / run）を差し替える。"""
    import hw_genie.main as main_mod

    monkeypatch.setattr(
        main_mod, "_ensure_session", lambda args: {"x-auth-token": "test"}
    )
    monkeypatch.setattr(main_mod, "HWClient", lambda headers: MagicMock())
    monkeypatch.setattr(
        "hw_genie.commands.seers_game.run_seers_game", run_fake
    )
    return main_mod


def test_cmd_seers_game_success_exit_0(monkeypatch):
    import hw_genie.main as main_mod
    from hw_genie.commands.seers_game import SeersGameResult

    seen = {}

    def fake_run(client, pick=2, max_games=None, dry_run=False, account_alias=None):
        seen.update(
            pick=pick,
            max_games=max_games,
            dry_run=dry_run,
            account_alias=account_alias,
        )
        # ≥1 ゲーム消化後のコイン枯渇 break は成功扱い（last_error 付きでも exit 0）。
        return SeersGameResult(games_played=2, last_error="NotEnough")

    _patch_seers_handler(monkeypatch, fake_run)
    main_mod.cmd_seers_game(_seers_handler_args(pick=2, account="Alice"))
    assert seen == {
        "pick": 2,
        "max_games": None,
        "dry_run": False,
        "account_alias": "Alice",
    }


def test_cmd_seers_game_clean_zero_games_exit_0(monkeypatch):
    import hw_genie.main as main_mod
    from hw_genie.commands.seers_game import SeersGameResult

    _patch_seers_handler(
        monkeypatch, lambda *a, **k: SeersGameResult(games_played=0)
    )
    main_mod.cmd_seers_game(_seers_handler_args())


def test_cmd_seers_game_failure_exit_1(monkeypatch, capsys):
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands.seers_game import SeersGameResult

    # 1 ゲームも消化できず last_error が残った場合は失敗（exit 1）。
    _patch_seers_handler(
        monkeypatch,
        lambda *a, **k: SeersGameResult(
            games_played=0, games_failed=1, last_error="boom"
        ),
    )
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_seers_game(_seers_handler_args())
    assert e.value.code == 1
    assert "Error" in capsys.readouterr().err


def test_cmd_seers_game_read_error_exit_1(monkeypatch, capsys):
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands.seers_game import SeersGameReadError

    def fake_raise(*a, **k):
        raise SeersGameReadError("eventPicker_getState failed (timeout)")

    _patch_seers_handler(monkeypatch, fake_raise)
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_seers_game(_seers_handler_args())
    assert e.value.code == 1
    assert "Error" in capsys.readouterr().err


def test_cmd_seers_game_value_error_exit_1(monkeypatch, capsys):
    import pytest

    import hw_genie.main as main_mod

    def fake_raise(*a, **k):
        raise ValueError("pick must be between 1 and 3, but got 5")

    _patch_seers_handler(monkeypatch, fake_raise)
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_seers_game(_seers_handler_args(pick=5))
    assert e.value.code == 1
    assert "Error" in capsys.readouterr().err


def test_cmd_seers_game_invalid_pick_exit_2(monkeypatch, capsys):
    import pytest

    import hw_genie.main as main_mod

    # API 呼び出し前の検証のため session/client の mock は不要。
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_seers_game(_seers_handler_args(pick=0))
    assert e.value.code == 2
    assert "--pick" in capsys.readouterr().err


def test_cmd_seers_game_invalid_max_games_exit_2(monkeypatch, capsys):
    import pytest

    import hw_genie.main as main_mod

    with pytest.raises(SystemExit) as e:
        main_mod.cmd_seers_game(_seers_handler_args(max_games=0))
    assert e.value.code == 2
    assert "--max-games" in capsys.readouterr().err


def test_seers_game_routine_counts_games(mock_client, mock_sleep):
    from hw_genie.runner import seers_game_routine

    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _res_from(_event_envelope(_START_EVENT)),
        _res_from(_event_envelope(_play_event(2), {"result": "win"})),
        _res_from(_event_envelope(_play_event(3), {"result": "win"})),
        _res_from(_event_envelope(_play_event(4), {"result": "win"})),
        _res_from(_event_envelope(_play_event(5), {"result": "win"})),
        _res_from(_event_envelope(_FINISH_EVENT)),
        _err_res("NotEnough"),
    ]
    result = seers_game_routine(client, "Alice")
    assert result.games_played >= 1
