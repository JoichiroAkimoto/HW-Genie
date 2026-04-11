import pytest
from unittest.mock import MagicMock, patch
from hw_genie.core.client import ResponseStatus
from hw_genie.commands.daily_raid import run_daily_raid
from hw_genie.core.session_manager import SessionManager

@patch("hw_genie.commands.daily_raid.HERO_MISSION_IDS", [1])
def test_daily_raid_soul_shop_purchase(mock_client, mock_sleep):
    """Phase 3 のソウルショップ購入が正しくフィルタリング・実行されるか検証"""
    client, mock_call = mock_client
    # DB初期化
    SessionManager.repo.save_data("default", {"headers": {}})
    
    mock_responses = []
    
    # 0. mission_get_all
    res_all_status = MagicMock()
    res_all_status.is_success = True
    res_all_status.detail = {"response": []}
    mock_responses.append(res_all_status)

    # 1. missionRaid
    res_h1 = MagicMock()
    res_h1.is_success = True
    res_h1.status = ResponseStatus.SUCCESS
    res_h1.detail = {"response": {"reward": {}}}
    mock_responses.append(res_h1)

    # 2. inventoryExchangeStones
    res_h_ex = MagicMock()
    res_h_ex.is_success = True
    res_h_ex.status = ResponseStatus.SUCCESS
    res_h_ex.detail = {"response": {"cost": {"fragmentHero": {}}}}
    res_h_ex.exchange_info = MagicMock(stones=0)
    mock_responses.append(res_h_ex)

    # Status (Hero Raid 内)
    res_status = MagicMock()
    res_status.is_success = True
    res_status.detail = {"response": {}}
    mock_responses.append(res_status)
    mock_responses.append(res_status)

    # --- Phase 2: Item Raid ---
    # 3. Item Raid iteration 1
    res_i1 = MagicMock()
    res_i1.is_success = True
    res_i1.status = ResponseStatus.SUCCESS
    mock_responses.append(res_i1)

    # 4. Item Raid Stop
    res_i2 = MagicMock()
    res_i2.is_success = False
    res_i2.error_name = "NotEnough"
    mock_responses.append(res_i2)

    # Status (Item Raid 内)
    mock_responses.append(res_status)
    mock_responses.append(res_status)

    # --- Phase 3: Soul Shop ---
    res_shop_all = MagicMock()
    res_shop_all.is_success = True
    res_shop_all.detail = {"response": {"8": {"slots": {"1": {"reward": {"item": {"1": 1}}, "bought": 0}}}}}
    mock_responses.append(res_shop_all)
    
    # shopBuy
    res_buy = MagicMock()
    res_buy.is_success = True
    mock_responses.append(res_buy)

    # Final Status
    mock_responses.append(res_status)
    mock_responses.append(res_status)

    mock_call.side_effect = mock_responses
    run_daily_raid(client, {"calls": []})
    assert True
