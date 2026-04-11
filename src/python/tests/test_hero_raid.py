import pytest
from unittest.mock import MagicMock
from hw_genie.commands.hero_raid import run_hero_raid
from . import dummy_responses as dummy

def test_raid_stops_on_limit_and_exchanges(mock_client, mock_sleep):
    """上限到達時に停止し、換金が実行されることを検証"""
    client, mock_call = mock_client
    mock_responses = []
    
    # 0. Checking mission status (mission_get_all)
    res_status_all = MagicMock()
    res_status_all.is_success = True
    res_status_all.detail = {"response": []}
    mock_responses.append(res_status_all)

    # 1. レイド成功
    res_success = MagicMock()
    res_success.status = "success"
    res_success.is_success = True
    res_success.detail = dummy.MISSION_RAID_SUCCESS["results"][0]["result"]
    mock_responses.append(res_success)
    
    # 2. 上限到達
    res_limit = MagicMock()
    res_limit.status = "error"
    res_limit.is_success = False
    res_limit.error_name = "limitReached"
    res_limit.detail = dummy.MISSION_RAID_LIMIT_REACHED["results"][0]["error"]
    mock_responses.append(res_limit)
    
    # 3. 換金 (exchange_stones)
    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_MULTI["results"][0]["result"]
    mock_responses.append(res_exchange)
    
    # 4. Status calls (fetch_player_status は 2 回 call を呼ぶ)
    res_status = MagicMock()
    res_status.is_success = True
    res_status.detail = {"response": {}}
    mock_responses.append(res_status)
    mock_responses.append(res_status)
    
    mock_call.side_effect = mock_responses
    
    # 実行 (ミッションID 1, 5 を対象)
    results, recovery_count, ex_info = run_hero_raid(client, [1, 5], times=3)
    
    # 検証
    assert len(results) == 2
    assert results[0].status == "success"
    assert results[1].status == "limit_reached"
    assert ex_info is not None
    # dummy.INVENTORY_EXCHANGE_STONES_MULTI の合計は 15
    assert ex_info.stones == 15
