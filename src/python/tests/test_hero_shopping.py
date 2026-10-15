from unittest.mock import MagicMock
from . import dummy_responses as dummy
from hw_genie.commands.hero_shopping import (
    ResponseStatus,
    ShopId,
    run_hero_shopping,
    TARGET_SHOP_IDS,
    PET_POTION_SLOT_ID,
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
                    PET_POTION_SLOT_ID: {
                        "bought": False,
                        "reward": {"consumable": {"85": 3000}},
                        "cost": {"coin": {"25": 600}}
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

    # 検証: 購入成功アイテムが 1 つあり、内容に "Pet Soul" と PET_POTION_SLOT_ID が含まれること
    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    assert len(success_items) == 1
    assert "Pet Soul" in success_items[0].action
    assert PET_POTION_SLOT_ID in success_items[0].action

    # getAll(1) + buy(1) = 2
    assert mock_call.call_count == 2


def test_buy_town_shop_slots(mock_client, mock_sleep):
    """タウンショップの固定スロット 2,4,5,6 が購入対象になることを検証"""
    from hw_genie.commands.hero_shopping import TOWN_SHOP_SLOT_IDS

    assert TOWN_SHOP_SLOT_IDS == frozenset({"2", "4", "5", "6"})
    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "1": {
                "slots": {
                    "1": {"bought": False, "reward": {"consumable": {"1": 1}}, "cost": {"gold": 100}},
                    "2": {"bought": False, "reward": {"consumable": {"2": 1}}, "cost": {"gold": 100}},
                    "3": {"bought": False, "reward": {"consumable": {"3": 1}}, "cost": {"gold": 100}},
                    "4": {"bought": False, "reward": {"consumable": {"4": 1}}, "cost": {"gold": 100}},
                    "5": {"bought": False, "reward": {"consumable": {"5": 1}}, "cost": {"gold": 100}},
                    "6": {"bought": False, "reward": {"consumable": {"6": 1}}, "cost": {"gold": 100}},
                    "7": {"bought": False, "reward": {"consumable": {"7": 1}}, "cost": {"gold": 100}},
                }
            }
        }
    }
    mock_responses = [res_all]
    for _ in range(4):
        res_buy = MagicMock()
        res_buy.is_success = True
        mock_responses.append(res_buy)
    mock_call.side_effect = mock_responses

    results, _ = run_hero_shopping(client, buy_town_shop_items=True)

    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    assert len(success_items) == 4
    # 購入されたスロットが 2,4,5,6 のみであること
    buy_slots = sorted(c.args[0]["calls"][0]["args"]["slot"] for c in mock_call.call_args_list[1:])
    assert buy_slots == [2, 4, 5, 6]
    assert all(c.args[0]["calls"][0]["args"]["shopId"] == 1 for c in mock_call.call_args_list[1:])
    assert mock_call.call_count == 5


def test_buy_town_shop_skips_bought_and_opt_out(mock_client, mock_sleep):
    """タウンショップの購入済みスキップと buy_town_shop_items=False の無効化を検証"""
    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "1": {
                "slots": {
                    "2": {"bought": True, "reward": {"consumable": {"2": 1}}, "cost": {"gold": 100}},
                    "4": {"bought": False, "reward": {"consumable": {"4": 1}}, "cost": {"gold": 100}},
                    "5": {"bought": 1, "reward": {"consumable": {"5": 1}}, "cost": {"gold": 100}},
                    "6": {"bought": "1", "reward": {"consumable": {"6": 1}}, "cost": {"gold": 100}},
                }
            }
        }
    }
    res_buy = MagicMock()
    res_buy.is_success = True
    mock_call.side_effect = [res_all, res_buy]

    results, _ = run_hero_shopping(client, buy_town_shop_items=True)
    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    # 購入済み(True/1/"1")は除外され slot 4 のみ購入
    assert len(success_items) == 1
    assert mock_call.call_args_list[1].args[0]["calls"][0]["args"] == {
        "shopId": 1,
        "slot": 4,
        "cost": {"gold": 100},
        "reward": {"consumable": {"4": 1}},
    }

    # オプトアウト時は購入なし（同一モックをリセットして再利用）
    mock_call.reset_mock()
    res_all2 = MagicMock()
    res_all2.is_success = True
    res_all2.detail = {
        "response": {
            "1": {"slots": {"2": {"bought": False, "reward": {"consumable": {"2": 1}}, "cost": {"gold": 100}}}}
        }
    }
    mock_call.side_effect = [res_all2]
    results2, _ = run_hero_shopping(client, buy_town_shop_items=False)
    assert results2 == []
    assert mock_call.call_count == 1


def test_town_shop_missing_shop(mock_client, mock_sleep):
    """shopGetAll に shop "1" が無い場合は購入 0 件で正常終了すること"""
    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {"response": {"8": {"slots": {}}}}
    mock_call.side_effect = [res_all]

    results, _ = run_hero_shopping(
        client, buy_soul_shop_items=False, hero_shop_ids=None, buy_town_shop_items=True
    )
    assert results == []
    assert mock_call.call_count == 1


def test_town_shop_missing_or_none_slots(mock_client, mock_sleep):
    """slots キー欠落・None の場合は購入 0 件で正常終了すること"""
    client, mock_call = mock_client

    for shop_body in ({"1": {}}, {"1": {"slots": None}}):
        mock_call.reset_mock()
        res_all = MagicMock()
        res_all.is_success = True
        res_all.detail = {"response": shop_body}
        mock_call.side_effect = [res_all]

        results, _ = run_hero_shopping(
            client, buy_soul_shop_items=False, hero_shop_ids=None, buy_town_shop_items=True
        )
        assert results == []
        assert mock_call.call_count == 1


def test_town_shop_unbought_variants_are_purchased(mock_client, mock_sleep):
    """bought が 0 / "0" / キー欠落の場合は未購入扱いで購入すること"""
    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "1": {
                "slots": {
                    "2": {"bought": 0, "reward": {"consumable": {"2": 1}}, "cost": {"gold": 100}},
                    "4": {"bought": "0", "reward": {"consumable": {"4": 1}}, "cost": {"gold": 100}},
                    "5": {"reward": {"consumable": {"5": 1}}, "cost": {"gold": 100}},
                }
            }
        }
    }
    mock_responses = [res_all]
    for _ in range(3):
        res_buy = MagicMock()
        res_buy.is_success = True
        mock_responses.append(res_buy)
    mock_call.side_effect = mock_responses

    results, _ = run_hero_shopping(
        client, buy_soul_shop_items=False, hero_shop_ids=None, buy_town_shop_items=True
    )
    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    assert len(success_items) == 3
    buy_slots = sorted(c.args[0]["calls"][0]["args"]["slot"] for c in mock_call.call_args_list[1:])
    assert buy_slots == [2, 4, 5]
    assert mock_call.call_count == 4


def test_town_shop_int_slot_keys(mock_client, mock_sleep):
    """スロットキーが int 型でも購入できること"""
    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            1: {
                "slots": {
                    2: {"bought": False, "reward": {"consumable": {"2": 1}}, "cost": {"gold": 100}},
                    4: {"bought": False, "reward": {"consumable": {"4": 1}}, "cost": {"gold": 100}},
                }
            }
        }
    }
    mock_responses = [res_all]
    for _ in range(2):
        res_buy = MagicMock()
        res_buy.is_success = True
        mock_responses.append(res_buy)
    mock_call.side_effect = mock_responses

    results, _ = run_hero_shopping(
        client, buy_soul_shop_items=False, hero_shop_ids=None, buy_town_shop_items=True
    )
    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    assert len(success_items) == 2
    buy_slots = sorted(c.args[0]["calls"][0]["args"]["slot"] for c in mock_call.call_args_list[1:])
    assert buy_slots == [2, 4]


def test_town_shop_skips_empty_reward_and_non_numeric_slot(mock_client, mock_sleep, caplog):
    """reward 空のスロットと非数値スロットはスキップし、cost 空は購入すること"""
    import logging

    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "1": {
                "slots": {
                    "2": {"bought": False, "reward": {}, "cost": {"gold": 100}},
                    "4": {"bought": False, "reward": {"consumable": {"4": 1}}, "cost": {"gold": 100}},
                    "5": {"bought": False, "reward": None, "cost": {"gold": 100}},
                    "6": {"bought": False, "reward": {"consumable": {"6": 1}}, "cost": {}},
                    "junk": {"bought": False, "reward": {"consumable": {"9": 1}}, "cost": {"gold": 100}},
                }
            }
        }
    }
    mock_responses = [res_all]
    for _ in range(2):
        res_buy = MagicMock()
        res_buy.is_success = True
        mock_responses.append(res_buy)
    mock_call.side_effect = mock_responses

    with caplog.at_level(logging.WARNING):
        results, _ = run_hero_shopping(
            client, buy_soul_shop_items=False, hero_shop_ids=None, buy_town_shop_items=True
        )

    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    assert len(success_items) == 2
    buy_slots = sorted(c.args[0]["calls"][0]["args"]["slot"] for c in mock_call.call_args_list[1:])
    assert buy_slots == [4, 6]
    assert "empty reward" in caplog.text
    assert "non-numeric slot" in caplog.text
    assert mock_call.call_count == 3


def test_shopping_purchase_order_is_numeric(mock_client, mock_sleep):
    """購入順序がショップIDの数値順（1,4,8,9,17）になること（辞書式では 1,17,4,8,9 になる）"""
    client, mock_call = mock_client

    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {
        "response": {
            "9": {"slots": {"1": {"bought": False, "reward": {"fragmentHero": {"44": 5}}, "cost": {}}}},
            "17": {"slots": {"3": {"bought": False, "reward": {"consumable": {"85": 3000}}, "cost": {}}}},
            "4": {"slots": {"1": {"bought": False, "reward": {"fragmentHero": {"18": 5}}, "cost": {}}}},
            "8": {"slots": {"1": {"bought": False, "reward": {"item": {"101": 1}}, "cost": {}}}},
            "1": {"slots": {"2": {"bought": False, "reward": {"consumable": {"2": 1}}, "cost": {}}}},
        }
    }
    mock_responses = [res_all]
    for _ in range(5):
        res_buy = MagicMock()
        res_buy.is_success = True
        mock_responses.append(res_buy)
    mock_call.side_effect = mock_responses

    run_hero_shopping(
        client,
        hero_shop_ids=[ShopId.FRIEND, ShopId.ARENA],
        buy_soul_shop_items=True,
        buy_pet_potions=True,
        buy_town_shop_items=True,
    )

    buy_shop_ids = [c.args[0]["calls"][0]["args"]["shopId"] for c in mock_call.call_args_list[1:]]
    assert buy_shop_ids == [1, 4, 8, 9, 17]
    assert mock_call.call_count == 6
