from unittest.mock import MagicMock
from hw_genie.commands.hero_shopping import (
    ResponseStatus,
    run_hero_shopping,
    ShopId,
)


def test_shopping_purchase_order(mock_client, mock_sleep):
    """購入順序が ShopId の値順にソートされていることを検証"""
    client, mock_call = mock_client

    # shopGetAll: Shop 9, 4, 8 の順でデータを返す
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "9": {"slots": {"1": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}}}},
            "4": {"slots": {"1": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}}}},
            "8": {"slots": {"1": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}}}},
        }
    }

    # すべて成功
    mock_responses = [res_all]
    for _ in range(3):
        res_buy = MagicMock()
        res_buy.is_success = True
        mock_responses.append(res_buy)

    mock_call.side_effect = mock_responses

    # ShopId.ARENA(4), ShopId.SOUL(8), ShopId.FRIEND(9) の順に呼ばれるはず
    run_hero_shopping(client, hero_shop_ids=[ShopId.ARENA, ShopId.SOUL, ShopId.FRIEND])

    # 呼ばれた引数を確認
    calls = mock_call.call_args_list
    # calls[0] is shopGetAll
    # calls[1] is buy for Shop 4
    # calls[2] is buy for Shop 8
    # calls[3] is buy for Shop 9

    # Payload of shopBuy call
    buy_calls = [c.args[0]["calls"][0]["args"]["shopId"] for c in calls[1:]]
    assert buy_calls == [4, 8, 9]


def test_shopping_out_of_stock_continues(mock_client, mock_sleep):
    """在庫切れ(想定外のエラー)が発生しても、次のアイテムの購入を継続することを検証"""
    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "4": {
                "slots": {
                    "1": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}},
                    "2": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}},
                }
            },
        }
    }

    # 1つ目は在庫切れ(想定外エラー), 2つ目は成功
    res_buy_1 = MagicMock()
    res_buy_1.is_success = False
    res_buy_1.error_name = "OutOfStock"  # 実際にあるかは不明だが generic error として扱う

    res_buy_2 = MagicMock()
    res_buy_2.is_success = True

    mock_call.side_effect = [res_all, res_buy_1, res_buy_2]

    results, _ = run_hero_shopping(client, hero_shop_ids=[ShopId.ARENA])

    assert len(results) == 2
    assert results[0].status == ResponseStatus.ERROR
    assert results[0].error == "OutOfStock"
    assert results[1].status == ResponseStatus.SUCCESS
    assert mock_call.call_count == 3


def test_shopping_mixed_errors_behavior(mock_client, mock_sleep):
    """多様なエラーが混在した場合の挙動を検証"""
    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "4": {
                "slots": {
                    "1": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}},
                    "2": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}},
                }
            },
            "8": {
                "slots": {
                    "1": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}},
                    "2": {"bought": False, "reward": {"fragmentHero": {"1": 1}}, "cost": {}},
                }
            },
        }
    }

    # Shop 4: Slot 1 -> Generic Error (Continue)
    res_4_1 = MagicMock()
    res_4_1.is_success = False
    res_4_1.error_name = "SomeOtherError"

    # Shop 4: Slot 2 -> Success
    res_4_2 = MagicMock()
    res_4_2.is_success = True

    # Shop 8: Slot 1 -> NotEnough (Skip Shop 8)
    res_8_1 = MagicMock()
    res_8_1.is_success = False
    res_8_1.error_name = "NotEnough"

    # Shop 8: Slot 2 -> Should be skipped

    mock_call.side_effect = [res_all, res_4_1, res_4_2, res_8_1]

    results, _ = run_hero_shopping(client, hero_shop_ids=[ShopId.ARENA, ShopId.SOUL])

    # Results: 4_1(Error), 4_2(Success), 8_1(Error)
    assert len(results) == 3
    assert results[0].status == ResponseStatus.ERROR
    assert results[0].error == "SomeOtherError"
    assert results[1].status == ResponseStatus.SUCCESS
    assert results[2].status == ResponseStatus.ERROR
    assert results[2].error == "NotEnough"

    # 呼び出し回数: getAll(1) + 4_1(1) + 4_2(1) + 8_1(1) = 4
    assert mock_call.call_count == 4
