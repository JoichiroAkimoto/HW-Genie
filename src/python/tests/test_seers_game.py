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
    # ただし枯渇 (NotEnough) は正常終了のため別テストで確認する。
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


def test_cmd_seers_game_played_with_failure_exit_1(monkeypatch, capsys):
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands.seers_game import SeersGameResult

    # ≥1 ゲーム消化後も games_failed>=1 は失敗（exit 1）。
    _patch_seers_handler(
        monkeypatch,
        lambda *a, **k: SeersGameResult(
            games_played=1, games_failed=1, last_error="LimitReached"
        ),
    )
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_seers_game(_seers_handler_args())
    assert e.value.code == 1
    assert "Error" in capsys.readouterr().err


def test_cmd_seers_game_depletion_zero_games_exit_0(monkeypatch, capsys):
    import hw_genie.main as main_mod
    from hw_genie.commands.seers_game import SeersGameResult

    # 枯渇 (NotEnough) は 0 ゲームでも正常終了（exit 0、失敗にしない）。
    _patch_seers_handler(
        monkeypatch,
        lambda *a, **k: SeersGameResult(games_played=0, last_error="NotEnough"),
    )
    main_mod.cmd_seers_game(_seers_handler_args())
    assert "Error" not in capsys.readouterr().err


def test_summarize_seers_game_depletion_zero_games_ok(capsys):
    from hw_genie.runner import summarize_seers_game
    from hw_genie.commands.seers_game import SeersGameResult

    # 枯渇のみのアカウントは失敗に数えない。
    failed = summarize_seers_game(
        [("Alice", (SeersGameResult(games_played=0, last_error="NotEnough"), None))]
    )
    assert failed == 0
    # 枯渇以外の error は従来通り失敗。
    failed = summarize_seers_game(
        [("Alice", (SeersGameResult(games_played=0, last_error="boom"), None))]
    )
    assert failed == 1


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


def _names_of(mock_call):
    return [c["calls"][0]["name"] for c, in (call.args for call in mock_call.call_args_list)]


def test_run_seers_game_resume_round5_only_finish(mock_client, mock_sleep):
    """round=5（4 play 済み）の中断再開は play なしで finish のみ送る。"""
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    resume_event = dict(_START_EVENT, round=5, size=4)
    mock_call.side_effect = [
        _res_from(_event_envelope(resume_event)),
        _res_from(_event_envelope(_FINISH_EVENT)),
        _err_res("NotEnough"),
    ]
    result = run_seers_game(client)
    names = _names_of(mock_call)
    assert names.count(ApiAction.EVENT_PICKER_PLAY_ROUND) == 0
    assert names.count(ApiAction.EVENT_PICKER_FINISH_GAME) == 1
    assert result.games_played == 1


def test_run_seers_game_resume_round2_plays_3(mock_client, mock_sleep):
    """round=2 の中断再開は残り 3 play + finish で 1 ゲーム扱い。"""
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    resume_event = dict(_START_EVENT, round=2, size=3)
    mock_call.side_effect = [
        _res_from(_event_envelope(resume_event)),
        _res_from(_event_envelope(_play_event(3), {"result": "win"})),
        _res_from(_event_envelope(_play_event(4), {"result": "win"})),
        _res_from(_event_envelope(_play_event(5), {"result": "win"})),
        _res_from(_event_envelope(_FINISH_EVENT)),
        _err_res("NotEnough"),
    ]
    result = run_seers_game(client)
    names = _names_of(mock_call)
    assert names.count(ApiAction.EVENT_PICKER_PLAY_ROUND) == 3
    assert names.count(ApiAction.EVENT_PICKER_FINISH_GAME) == 1
    assert result.games_played == 1


def test_run_seers_game_max_games_1_inexhaustible(mock_client, mock_sleep):
    """max_games=1 でコイン枯渇なしでもちょうど 1 ゲームで止まる。"""
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _res_from(_event_envelope(_START_EVENT)),
        _res_from(_event_envelope(_play_event(2), {"result": "win"})),
        _res_from(_event_envelope(_play_event(3), {"result": "win"})),
        _res_from(_event_envelope(_play_event(4), {"result": "win"})),
        _res_from(_event_envelope(_play_event(5), {"result": "win"})),
        _res_from(_event_envelope(_FINISH_EVENT)),
    ]
    result = run_seers_game(client, max_games=1)
    assert result.games_played == 1
    assert result.games_failed == 0
    # getState + start + 4 play + finish のみ（2 ゲーム目の start を送らない）。
    assert mock_call.call_count == 7


def test_run_seers_game_second_game_play_error(mock_client, mock_sleep):
    """2 ゲーム目の playRound ERROR は clean return（例外なし）で失敗計上。"""
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _res_from(_event_envelope(_START_EVENT)),
        _res_from(_event_envelope(_play_event(2), {"result": "win"})),
        _res_from(_event_envelope(_play_event(3), {"result": "win"})),
        _res_from(_event_envelope(_play_event(4), {"result": "win"})),
        _res_from(_event_envelope(_play_event(5), {"result": "win"})),
        _res_from(_event_envelope(_FINISH_EVENT)),
        _res_from(_event_envelope(_START_EVENT)),
        _err_res("SomePlayError"),
    ]
    result = run_seers_game(client)
    assert result.games_played == 1
    assert result.games_failed == 1
    assert result.last_error is not None


def test_run_seers_game_pick_out_of_range_raises(mock_client, mock_sleep):
    """run 経路の pick 範囲外は ValueError を送出する（live size に対して検証）。"""
    import pytest
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _res_from(_event_envelope(_START_EVENT)),  # size=3
    ]
    with pytest.raises(ValueError, match="pick must be between"):
        run_seers_game(client, pick=9)


def test_run_single_game_rewards_cumulative_not_summed(mock_client, mock_sleep):
    """報酬は累積スナップショットの最終値のみ計上（play 12:20×4 + finish 12:40 → 40）。"""
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
    assert result.rewards == {"consumable:12": 40}


def test_multi_seers_game_invalid_pick_exit_2_no_api_call(monkeypatch, capsys):
    """multi seers-game の事前検証: pick=0 は API 呼び出し前に stderr + exit 2。"""
    import argparse

    import pytest

    import hw_genie.main as main_mod

    called = []
    monkeypatch.setattr(
        main_mod, "run_all_accounts", lambda *a, **k: called.append(1) or {}
    )
    args = argparse.Namespace(
        mode="seers-game",
        accounts=["Alice"],
        pick=0,
        max_games=None,
        dry_run=False,
        parallel=1,
    )
    with pytest.raises(SystemExit) as e:
        main_mod.cmd_multi(args)
    assert e.value.code == 2
    assert called == []
    assert "--pick" in capsys.readouterr().err


def test_coin_balance_zero_when_id_missing_from_coin():
    """残高0のアイテムは API がキーを省略するため、coin 枠自体があれば 0 とみなす。"""
    from hw_genie.commands.seers_game import _coin_balance_from_inventory
    assert _coin_balance_from_inventory({"response": {"coin": {"1": 999}}}) == 0
    assert _coin_balance_from_inventory({"response": {}}) is None
    assert _coin_balance_from_inventory({"response": {"coin": {"2824001094": 0}}}) == 0
    assert _coin_balance_from_inventory({"response": {"coin": {"2824001094": 125}}}) == 125


def test_dry_run_shows_zero_balance(mock_client, mock_sleep, capsys):
    """残高0でも dry-run で Balance: 0 と表示する。"""
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _res_from({"results": [{"ident": "body", "result": {"response": {"coin": {"1": 999}}}}]}),
    ]
    run_seers_game(client, dry_run=True)
    assert "Seer's Coin balance: 0 (~0 game(s))" in capsys.readouterr().out


def test_run_seers_game_non_depletion_start_error_after_success_fails(mock_client, mock_sleep):
    """1 成功後の非枯渇 start ERROR は games_failed=1 で失敗扱い（NotEnough は除く）。"""
    from hw_genie.commands.seers_game import run_seers_game
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(_event_envelope(_NEW_GAME_EVENT)),
        _res_from(_event_envelope(_START_EVENT)),
        _res_from(_event_envelope(_play_event(2), {"result": "win"})),
        _res_from(_event_envelope(_play_event(3), {"result": "win"})),
        _res_from(_event_envelope(_play_event(4), {"result": "win"})),
        _res_from(_event_envelope(_play_event(5), {"result": "win"})),
        _res_from(_event_envelope(_FINISH_EVENT)),
        _err_res("LimitReached"),
    ]
    result = run_seers_game(client)
    assert result.games_played == 1
    assert result.games_failed == 1
    assert result.last_error == "LimitReached"


def test_summarize_seers_game_non_depletion_start_error_after_success_fails():
    """≥1 成功後の非枯渇停止も summarize では失敗に数える。"""
    from hw_genie.runner import summarize_seers_game
    from hw_genie.commands.seers_game import SeersGameResult

    failed = summarize_seers_game(
        [("Alice", (SeersGameResult(games_played=1, games_failed=1, last_error="LimitReached"), None))]
    )
    assert failed == 1


def test_coin_balance_nested_inventory_strict():
    """ネスト inventory は coin 枠のみ参照し、それ以外は None（不明）。"""
    from hw_genie.commands.seers_game import _coin_balance_from_inventory
    assert _coin_balance_from_inventory({"response": {"inventory": {"consumable": {"1": 5}}}}) is None
    assert _coin_balance_from_inventory({"response": {"inventory": {"coin": {"2824001094": 50}}}}) == 50
    assert _coin_balance_from_inventory({"response": {"inventory": {"coin": {"1": 999}}}}) == 0
