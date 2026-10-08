def test_api_action_has_event_picker():
    from hw_genie.core.client import ApiAction
    assert ApiAction.EVENT_PICKER_START_GAME == "eventPicker_startGame"
    assert ApiAction.EVENT_PICKER_PLAY_ROUND == "eventPicker_playRound"
    assert ApiAction.EVENT_PICKER_FINISH_GAME == "eventPicker_finishGame"
    assert ApiAction.EVENT_PICKER_GET_STATE == "eventPicker_getState"
