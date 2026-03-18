from unittest.mock import MagicMock

# スクリプトのディレクトリをパスに追加

from . import dummy_responses as dummy
from hw_genie.commands.hero_shopping import ResponseStatus, run_hero_shopping


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

    results, _ = run_hero_shopping(client)
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

    results, _ = run_hero_shopping(client)
    client.exchange_stones()

    # 検証:
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
