from unittest.mock import MagicMock

# スクリプトのディレクトリをパスに追加

from . import dummy_responses as dummy
from hw_genie.commands.hero_shopping import (
    ResponseStatus,
    run_hero_shopping,
    TARGET_SHOP_IDS,
)


def test_hero_shopping_extraction_and_skip_bought(mock_client, mock_sleep):
    """購入対象の抽出と、購入済みアイテムのスキップを検証"""
    client, mock_call = mock_client

    # 1. shopGetAll のレスポンスを設定 (SHOP_GET_ALL_VARIED)
    # 4 (Arena): Slot 1 (対象), Slot 2 (購入済みスキップ)
    # 8 (Soul): Slot 1 (対象), Slot 2 (対象 - itemだけどショップ8なので)
    # 9 (Friend): Slot 1 (対象)
    # 合計 4 つがキューに入るはず

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
    # HWClient.exchange_stones 内部で detail を解析して exchange_info を生成するため、
    # 正しい detail を設定する
    res_ex.detail = dummy.INVENTORY_EXCHANGE_STONES_MULTI["results"][0]["result"]
    mock_responses.append(res_ex)

    mock_call.side_effect = mock_responses

    results, _ = run_hero_shopping(client, hero_shop_ids=TARGET_SHOP_IDS)
    ex_res = client.exchange_stones()
    ex_info = ex_res.exchange_info

    # 検証: 購入成功アイテムが 4 つあること
    success_items = [r for r in results if r.status == ResponseStatus.SUCCESS]
    assert len(success_items) == 4
    # INVENTORY_EXCHANGE_STONES_MULTI の合計は 15
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

    # (Shop 8: Slot 2 はスキップされるはず)

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

    # 1. Shop 4 Slot 1: SUCCESS
    # 2. Shop 8 Slot 1: ERROR (NotEnough)
    # 3. Shop 9 Slot 1: SUCCESS
    # 合計 3 つの実行結果があること (Shop 8 Slot 2 は run_hero_shopping 内でループ内で continue されるが results には追加されない)

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
    
    # 換金
    res_ex = MagicMock()
    res_ex.is_success = True
    res_ex.exchange_info = None
    
    mock_responses = [res_all, res_ex]
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

    # shopGetAll (SHOP_GET_ALL_VARIED)
    # Shop 4, Slot 1: fragmentHero x 5
    # Shop 8, Slot 1: fragmentHero x 3
    # Shop 8, Slot 2: item x 1 (集計対象外)
    # Shop 9, Slot 1: fragmentHero x 5
    # 合計: 5 + 3 + 5 = 13
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
