from unittest.mock import MagicMock, patch, call
from hw_genie.commands.daily_raid import run_daily_raid
from hw_genie.core.session_manager import SessionManager
from hw_genie.core.client import ResponseStatus, ApiAction, ErrorName

@patch("hw_genie.commands.daily_raid.HERO_MISSION_IDS", [1, 2])
def test_daily_raid_phase1_stamina_stops_phase2(mock_client, mock_sleep):
    """Phase 1 でスタミナ切れが発生した際、Phase 2 がスキップされることを検証"""
    client, mock_call = mock_client
    # DB初期化（実名の単一アカウント）
    SessionManager.repo.save_data("DailyUser", {"headers": {}, "player": {"id": "daily_id", "name": "DailyUser"}})
    
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

@patch("hw_genie.commands.daily_raid.run_item_raid")
@patch("hw_genie.commands.daily_raid.run_hero_raid")
def test_daily_raid_item_payload_from_calls(mock_hero_raid, mock_item_raid, mock_client, mock_sleep):
    """curl形式のペイロード（callsに含まれるID）が正しく認識され、アイテムレイドが実行されることを検証"""
    client, mock_call = mock_client
    
    # run_hero_raid の戻り値を設定 (hero_res, recovery_count, ex_info)
    # hero_res の中に STAMINA_ERROR がないようにする
    mock_hero_res = [MagicMock(status=ResponseStatus.SUCCESS)]
    mock_hero_raid.return_value = (mock_hero_res, 0, MagicMock())
    
    # status fetch 用
    mock_call.return_value = MagicMock(name="PlayerStatus")
    
    # curl形式のペイロード
    item_payload = {
        "calls": [
            {"name": "missionRaid", "args": {"id": 176, "times": 10}, "ident": "body"}
        ]
    }
    
    with patch("hw_genie.commands.daily_raid.resolve_account", return_value="daily_user"):
        run_daily_raid(client, item_payload=item_payload)
    
    # run_item_raid が呼ばれたこと、および mission_id が正しくセットされていることを検証
    mock_item_raid.assert_called_once()
    called_payload = mock_item_raid.call_args[0][1]
    assert called_payload["mission_id"] == 176

