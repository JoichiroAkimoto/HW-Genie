from unittest.mock import MagicMock
from . import dummy_responses as dummy
from hw_genie.commands.hero_shopping import (
    ResponseStatus,
    run_hero_shopping,
    TARGET_SHOP_IDS,
)


def test_hero_shopping_extraction_and_skip_bought(mock_client, mock_sleep):
    """購入対象の抽出と、購入済みアイテムのスキップを検証"""
    client, mock_call = mock_client

    mock_responses = []

    # shopGetAll
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = dummy.SHOP_GET_ALL_VARIED["results"][0]["result"]
    mock_responses.append(res_all)

    # 4 回の購入成功
    for _ in range(4):
        res_buy = MagicMock()
        res_buy.is_success = True
        mock_responses.append(res_buy)

    # 換金成功
    res_ex = MagicMock()
    res_ex.is_success = True
    res_ex.detail = dummy.INVENTORY_EXCHANGE_STONES_MULTI["results"][0]["result"]
    mock_responses.append(res_ex)

    mock_call.side_effect = mock_responses

    results, _ = run_hero_shopping(client, hero_shop_ids=TARGET_SHOP_IDS)
    ex_res = client.exchange_stones()
    ex_info = ex_res.exchange_info

    # 検証: 購入成功アイテムが 4 つあること
    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    assert len(success_items) == 4
    assert ex_info.stones == 15

    # 呼び出し回数の確認: getAll(1) + buy(4) + exchange(1) = 6
    assert mock_call.call_count == 6


def test_hero_shopping_insufficient_funds_skips_same_shop(mock_client, mock_sleep):
    """資金不足(NotEnough)時に同じショップのみスキップすることを検証"""
    client, mock_call = mock_client

    mock_responses = []

    # shopGetAll
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = dummy.SHOP_GET_ALL_VARIED["results"][0]["result"]
    mock_responses.append(res_all)

    # Shop 4: Slot 1 -> 成功
    res_buy_4_1 = MagicMock()
    res_buy_4_1.is_success = True
    mock_responses.append(res_buy_4_1)

    # Shop 8: Slot 1 -> 資金不足 (NotEnough)
    res_buy_8_1 = MagicMock()
    res_buy_8_1.is_success = False
    res_buy_8_1.error_name = "NotEnough"
    mock_responses.append(res_buy_8_1)

    # Shop 9: Slot 1 -> 成功 (別のショップなので実行される)
    res_buy_9_1 = MagicMock()
    res_buy_9_1.is_success = True
    mock_responses.append(res_buy_9_1)

    # 換金
    res_ex = MagicMock()
    res_ex.is_success = True
    res_ex.exchange_info = None
    mock_responses.append(res_ex)

    mock_call.side_effect = mock_responses

    results, _ = run_hero_shopping(client, hero_shop_ids=TARGET_SHOP_IDS)
    client.exchange_stones()

    assert len(results) == 3
    assert results[0].status == ResponseStatus.SUCCESS  # Shop 4
    assert results[1].status == ResponseStatus.ERROR  # Shop 8 Slot 1
    assert results[1].error == "NotEnough"
    assert results[2].status == ResponseStatus.SUCCESS  # Shop 9

    # getAll(1) + buy(3) + exchange(1) = 5
    assert mock_call.call_count == 5


def test_hero_shopping_empty_slots(mock_client, mock_sleep):
    """対象ショップが存在しない、または slots が空の場合にエラーなく処理が完了するか検証"""
    client, mock_call = mock_client

    # shopGetAll (ショップ情報が空)
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {"response": {}}
    
    mock_responses = [res_all]
    mock_call.side_effect = mock_responses

    results, _ = run_hero_shopping(client, hero_shop_ids=TARGET_SHOP_IDS)

    assert len(results) == 0
    # getAll の1回だけ呼ばれる
    assert mock_call.call_count == 1


def test_hero_shopping_auth_error_abort(mock_client, mock_sleep):
    """実行中に認証エラーが発生した場合、直ちに中断されることを検証"""
    client, mock_call = mock_client

    # shopGetAll (認証エラー)
    res_all = MagicMock()
    res_all.status = ResponseStatus.AUTH_ERROR
    res_all.is_success = False
    
    mock_call.side_effect = [res_all]

    results, _ = run_hero_shopping(client, hero_shop_ids=TARGET_SHOP_IDS)

    # 途中で中断されるが最初のエラー結果が記録されるため result は1件
    assert len(results) == 1
    assert results[0].status == ResponseStatus.ERROR
    assert mock_call.call_count == 1


def test_hero_shopping_count_souls_only(mock_client, mock_sleep, capsys):
    """ヒーローソウルのみが集計対象になることを検証"""
    client, mock_call = mock_client

    mock_responses = []

    # shopGetAll
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = dummy.SHOP_GET_ALL_VARIED["results"][0]["result"]
    mock_responses.append(res_all)

    # 4 回の購入成功
    for _ in range(4):
        res_buy = MagicMock()
        res_buy.is_success = True
        mock_responses.append(res_buy)

    mock_call.side_effect = mock_responses

    run_hero_shopping(client, hero_shop_ids=TARGET_SHOP_IDS)

    captured = capsys.readouterr()
    assert "Total Hero Souls Purchased: 13" in captured.out
    assert "Total Items Purchased" not in captured.out
    assert mock_call.call_count == 5


def test_buy_pet_potion(mock_client, mock_sleep):
    """ペットソウルショップのペットポーション（スロット3）が正しく購入対象として識別されることを検証"""
    client, mock_call = mock_client

    mock_responses = []

    # shopGetAll
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "17": {
                "slots": {
                    "3": {
                        "bought": False,
                        "reward": {"consumable": {"31": 1}},
                        "cost": {"petSoul": 10}
                    }
                }
            }
        }
    }
    mock_responses.append(res_all)

    # 1 回の購入成功
    res_buy = MagicMock()
    res_buy.is_success = True
    mock_responses.append(res_buy)

    mock_call.side_effect = mock_responses

    results, _ = run_hero_shopping(client, buy_pet_potions=True)

    # 検証: 購入成功アイテムが 1 つあり、内容に "Pet Soul" と "3" が含まれること
    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    assert len(success_items) == 1
    assert "Pet Soul" in success_items[0].action
    assert "3" in success_items[0].action

    # getAll(1) + buy(1) = 2
    assert mock_call.call_count == 2
