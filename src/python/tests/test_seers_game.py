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
