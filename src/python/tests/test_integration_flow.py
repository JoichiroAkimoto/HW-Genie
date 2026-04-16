from unittest.mock import MagicMock, patch
from hw_genie.core.session_manager import SessionManager
from hw_genie.commands.hero_raid import run_hero_raid
from hw_genie.commands.hero_shopping import run_hero_shopping, ShopId
from hw_genie.core.client import ResponseStatus
from . import dummy_responses as dummy


def test_full_integration_flow(mock_client, mock_sleep):
    """
    Auth -> Raid -> Shopping -> Save の一連の流れを検証する統合テスト
    """
    client, mock_call = mock_client
    account = "test_user"

    # 1. Session Load (Auth相当)
    # SessionRepository をモックして、初期データを返すようにする
    initial_data = {"player": {"name": "TestUser"}, "last_item_raid_mission_id": 10}
    with patch("hw_genie.core.session_manager.SessionRepository.get_data", return_value=initial_data):
        session_data = SessionManager.load(account)
        assert session_data == initial_data

    # 2. Raid 実行
    # APIレスポンスの順序: missionGetAll -> missionRaid -> exchangeStones
    res_status_all = MagicMock()
    res_status_all.is_success = True
    res_status_all.detail = {"response": []}

    res_raid_success = MagicMock()
    res_raid_success.status = "success"
    res_raid_success.is_success = True
    res_raid_success.detail = dummy.MISSION_RAID_SUCCESS["results"][0]["result"]

    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]

    # 3. Shopping 実行
    # APIレスポンス: shopGetAll -> shopBuy
    res_shop_all = MagicMock()
    res_shop_all.is_success = True
    res_shop_all.detail = {
        "response": {
            "8": {  # Soul Shop
                "slots": {"1": {"bought": False, "reward": {"fragmentHero": {"123": 10}}, "cost": {"soul": 100}, "label": "Hero Fragment"}}
            }
        }
    }

    res_shop_buy = MagicMock()
    res_shop_buy.is_success = True
    res_shop_buy.status = "success"

    # 全体のコールバック順序を定義
    # run_hero_raid: missionGetAll(1) -> missionRaid(1) -> exchangeStones(1)
    # run_hero_shopping: shopGetAll(1) -> shopBuy(1)
    mock_call.side_effect = [res_status_all, res_raid_success, res_exchange, res_shop_all, res_shop_buy]

    # Raid実行
    raid_results, recovery, ex_info = run_hero_raid(client, [1], times=1)
    assert raid_results[0].status == "success"

    # Shopping実行 (Soul shop itemsのみ購入)
    shop_results, _ = run_hero_shopping(client, buy_soul_shop_items=True, hero_shop_ids=[ShopId.SOUL])
    assert any(r.status == ResponseStatus.SUCCESS for r in shop_results)

    # 4. Session Save
    updated_data = session_data.copy()
    updated_data["last_item_raid_mission_id"] = 11

    with patch("hw_genie.core.session_manager.SessionRepository.save_data") as mock_save:
        SessionManager.save(account, updated_data)
        mock_save.assert_called_once_with(account, updated_data)

    # API呼び出し回数の検証
    # raid: 3 calls, shop: 2 calls = 5 calls
    assert mock_call.call_count == 5
