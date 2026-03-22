from unittest.mock import MagicMock, patch

# スクリプトのディレクトリをパスに追加

from hw_genie.core.client import ResponseStatus
from hw_genie.commands.daily_raid import run_daily_raid


@patch("hw_genie.commands.daily_raid.HERO_MISSION_IDS", [1])
def test_daily_raid_soul_shop_purchase(mock_client, mock_sleep):
    """Phase 3 のソウルショップ購入が正しくフィルタリング・実行されるか検証"""
    client, mock_call = mock_client
    mock_responses = []

    # --- Phase 1: Hero Raid ---
    # 1. missionRaid
    res_h1 = MagicMock()
    res_h1.is_success = True
    res_h1.status = ResponseStatus.SUCCESS
    res_h1.detail = {"response": {"reward": {}}}
    mock_responses.append(res_h1)

    # 2. inventoryExchangeStones (hero_raid の最後)
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
    # 3. Item Raid iteration 1 (Success)
    res_i1 = MagicMock()
    res_i1.is_success = True
    res_i1.status = ResponseStatus.SUCCESS
    res_i1.detail = {"response": {}}
    mock_responses.append(res_i1)

    # 4. Item Raid iteration 2 (Success)
    res_i2 = MagicMock()
    res_i2.is_success = True
    res_i2.status = ResponseStatus.SUCCESS
    res_i2.detail = {"response": {}}
    mock_responses.append(res_i2)

    # 5. Item Raid iteration 3 (NotEnoughStamina/NotEnough - Stop condition)
    res_i3 = MagicMock()
    res_i3.is_success = False
    res_i3.status = ResponseStatus.ERROR
    res_i3.error_name = "NotEnough"
    res_i3.detail = {"description": "Stopped"}
    mock_responses.append(res_i3)

    # Status (Item Raid 内)
    mock_responses.append(res_status)
    mock_responses.append(res_status)

    # --- Phase 3: Soul Shop Purchase ---
    # 6. shopGetAll
    res_shop_all = MagicMock()
    res_shop_all.is_success = True
    res_shop_all.status = ResponseStatus.SUCCESS
    res_shop_all.detail = {
        "response": {
            "8": {
                "slots": {
                    "1": {"reward": {"fragmentHero": {"10": 5}}, "cost": {"soulCoin": 100}, "bought": 0},  # Skip
                    "2": {"reward": {"item": {"50": 1}}, "cost": {"soulCoin": 500}, "bought": 0},  # Buy
                    "3": {"reward": {"item": {"51": 1}}, "cost": {"soulCoin": 1000}, "bought": 0},  # Buy (But NotEnough result)
                    "4": {"reward": {"item": {"52": 1}}, "cost": {"soulCoin": 2000}, "bought": 0},  # Skip
                    "5": {"reward": {"item": {"53": 1}}, "bought": 1},  # Skip
                }
            }
        }
    }
    mock_responses.append(res_shop_all)

    # 7. shopBuy Slot 2 (Success)
    res_buy_s2 = MagicMock()
    res_buy_s2.is_success = True
    res_buy_s2.status = ResponseStatus.SUCCESS
    mock_responses.append(res_buy_s2)

    # 8. shopBuy Slot 3 (NotEnough -> Break)
    res_buy_s3 = MagicMock()
    res_buy_s3.is_success = False
    res_buy_s3.status = ResponseStatus.ERROR
    res_buy_s3.error_name = "NotEnough"
    mock_responses.append(res_buy_s3)

    # 9. Status (User Info) - Daily Raid Final
    mock_responses.append(res_status)

    # 10. Status (Arena Info) - Daily Raid Final
    mock_responses.append(res_status)

    mock_call.side_effect = mock_responses

    # 実行
    run_daily_raid({"x-auth-token": "test"}, {"calls": []})

    # 呼び出し回数の確認
    # h1(1) + ex(1) + st(2) + i1(1) + i2(1) + i3(1) + st(2) + shop_all(1) + buy_s2(1) + buy_s3(1) + status(2) = 14
    assert mock_call.call_count == 14

    # 各呼び出しの中身を確認
    # 10番目 (index 9): shop_soul_raid 内の shopGetAll
    # calls: h1, ex, st1, st2, i1, i2, i3, st3, st4, shopGetAll...
    # index: 0,  1,  2,   3,   4,  5,  6,  7,   8,   9
    args_shop = mock_call.call_args_list[9][0][0]
    assert args_shop["calls"][0]["name"] == "shopGetAll"

    # 11番目: shopBuy Slot 2
    args_buy2 = mock_call.call_args_list[10][0][0]
    assert args_buy2["calls"][0]["args"]["slot"] == 2

    # 12番目: shopBuy Slot 3
    args_buy3 = mock_call.call_args_list[11][0][0]
    assert args_buy3["calls"][0]["args"]["slot"] == 3
