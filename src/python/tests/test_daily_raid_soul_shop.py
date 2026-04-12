from unittest.mock import MagicMock, patch, call
from hw_genie.core.client import ResponseStatus, ApiAction
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

    # --- Phase 3: Soul Shop ---
    # shopGetAll
    res_shop_all = MagicMock()
    res_shop_all.is_success = True
    res_shop_all.detail = {"response": {"8": {"slots": {"1": {"reward": {"item": {"1": 1}}, "bought": 0, "cost": {}}}}}}
    mock_responses.append(res_shop_all)
    
    # shopBuy
    res_buy = MagicMock()
    res_buy.is_success = True
    mock_responses.append(res_buy)

    mock_call.side_effect = mock_responses
    run_daily_raid(client)

    # 呼び出しシーケンスの検証
    assert mock_call.call_count == 5
    mock_call.assert_has_calls([
        call({'calls': [{'name': ApiAction.MISSION_GET_ALL, 'args': {}, 'ident': 'body'}]}),
        call({'calls': [{'name': ApiAction.MISSION_RAID, 'args': {'id': 1, 'times': 3}, 'context': {'actionTs': 0}, 'ident': 'body'}]}),
        call({'calls': [{'name': ApiAction.INVENTORY_EXCHANGE_STONES, 'args': {}, 'ident': 'exchange_stones'}]}),
        call({'calls': [{'name': ApiAction.SHOP_GET_ALL, 'args': {}, 'ident': 'shopGetAll'}]}),
        call({'calls': [{'name': ApiAction.SHOP_BUY, 'args': {'shopId': 8, 'slot': 1, 'cost': {}, 'reward': {'item': {'1': 1}}}, 'ident': 'buy_8_1'}]})
    ])
