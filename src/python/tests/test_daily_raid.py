import pytest
from unittest.mock import MagicMock, patch, call
from hw_genie.commands.daily_raid import run_daily_raid
from hw_genie.core.session_manager import SessionManager
from hw_genie.core.client import ResponseStatus, ApiAction, ErrorName

@patch("hw_genie.commands.daily_raid.HERO_MISSION_IDS", [1, 2])
def test_daily_raid_phase1_stamina_stops_phase2(mock_client, mock_sleep):
    """Phase 1 でスタミナ切れが発生した際、Phase 2 がスキップされることを検証"""
    client, mock_call = mock_client
    # DB初期化
    SessionManager.repo.save_data("default", {"headers": {}})
    
    mock_responses = []
    
    # 1. Hero Raid - Checking mission status
    res_status_all = MagicMock()
    res_status_all.is_success = True
    res_status_all.detail = {"response": []}
    mock_responses.append(res_status_all)

    # 2. Mission 1 Success
    res_m1 = MagicMock()
    res_m1.is_success = True
    res_m1.status = ResponseStatus.SUCCESS
    res_m1.detail = {"response": {}}
    mock_responses.append(res_m1)

    # 3. Mission 2 Stamina Error
    res_m2 = MagicMock()
    res_m2.is_success = False
    res_m2.status = ResponseStatus.ERROR
    res_m2.error_name = ErrorName.NOT_ENOUGH_STAMINA
    res_m2.detail = {"description": "need stamina"}
    mock_responses.append(res_m2)

    # 4. Hero Raid - inventoryExchangeStones
    res_h_ex = MagicMock()
    res_h_ex.is_success = True
    res_h_ex.exchange_info = MagicMock(stones=0)
    mock_responses.append(res_h_ex)

    mock_call.side_effect = mock_responses

    run_daily_raid(client)

    # 検証
    assert mock_call.call_count == 4
    mock_call.assert_has_calls([
        call({'calls': [{'name': ApiAction.MISSION_GET_ALL, 'args': {}, 'ident': 'body'}]}),
        call({'calls': [{'name': ApiAction.MISSION_RAID, 'args': {'id': 1, 'times': 3}, 'context': {'actionTs': 0}, 'ident': 'body'}]}),
        call({'calls': [{'name': ApiAction.MISSION_RAID, 'args': {'id': 2, 'times': 3}, 'context': {'actionTs': 0}, 'ident': 'body'}]}),
        call({'calls': [{'name': ApiAction.INVENTORY_EXCHANGE_STONES, 'args': {}, 'ident': 'exchange_stones'}]})
    ])
