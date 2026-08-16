from unittest.mock import MagicMock

from . import dummy_responses as dummy
from hw_genie.commands.asgard_shop import (
    ResponseStatus,
    build_buy_queue,
    is_maestro_shop,
    is_osh_shop,
    run_asgard_shop,
    select_maestro_plan,
)


def _res_from(dummy_response: dict) -> MagicMock:
    """dummy_responses の結果 dict を HWResponse 風の MagicMock に変換する。"""
    res = MagicMock()
    res.is_success = True
    res.detail = dummy_response["results"][0]["result"]
    return res


def test_build_buy_queue_priority_then_price_order():
    """購入キューが優先度 1→2→3→残り（価格昇順）で並ぶことを検証。"""
    shop = dummy.CLAN_RAID_GET_INFO_OSH["results"][0]["result"]["response"]["shop"]
    queue = build_buy_queue(shop)

    # 優先度 1 の slot がまずリスト順で並ぶ
    assert [item.slot_id for item in queue[:6]] == [8, 17, 20, 12, 13, 19]
    # 優先度 2
    assert [item.slot_id for item in queue[6:10]] == [6, 10, 21, 18]
    # 優先度 3
    assert [item.slot_id for item in queue[10:13]] == [15, 16, 11]
    # 残り（7, 9, 14 は同額 150 → slot 昇順）
    assert [item.slot_id for item in queue[13:]] == [7, 9, 14]
    # ゴールドバフ（slot 1〜5）は含まれない
    assert all(item.slot_id > 5 for item in queue)


def test_is_osh_shop():
    """Osh シグネチャ判定（Maestro 週は False）。"""
    osh_shop = dummy.CLAN_RAID_GET_INFO_OSH["results"][0]["result"]["response"]["shop"]
    maestro_shop = dummy.CLAN_RAID_GET_INFO_MAESTRO["results"][0]["result"]["response"]["shop"]
    assert is_osh_shop(osh_shop) is True
    assert is_osh_shop(maestro_shop) is False


def test_run_asgard_shop_buys_until_budget(mock_client, mock_sleep):
    """残高 1000 で優先度順に購入し、残高不足で残りをスキップすることを検証。"""
    client, mock_call = mock_client

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    for _ in range(13):  # P1(6) + P2(4) + P3(3) = 13 件が 1000 コインで買える
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client)

    assert result.skipped is False
    assert result.bought == 13
    assert result.spent == 1000
    assert result.remaining == 0
    assert result.coins == 1000
    assert result.failed_count == 0
    skipped = [item for item in result.items if item.status == ResponseStatus.SKIPPED]
    assert [item.action for item in skipped] == [f"[Realm Traveler] Slot:{s} -> buff {s + 60} (x{dummy._OSH_BUFF_VALUES[s]}, {150} Valor Emblems)" for s in (7, 9, 14)]
    # getInfo(1) + 購入(13) = 14 回
    assert mock_call.call_count == 14


def test_run_asgard_shop_skips_bought_and_budget_check(mock_client, mock_sleep):
    """購入済み slot の除外と、残高 100 での 1 件購入後の停止を検証。"""
    client, mock_call = mock_client

    # slot 8, 17 購入済み・残高 100 → 最初の候補は slot 20(100) のみ購入可能
    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH_BOUGHT)]
    responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client)

    assert result.bought == 1
    assert result.spent == 100
    assert result.remaining == 0
    success = [item for item in result.items if item.status == ResponseStatus.SUCCESS]
    assert len(success) == 1
    assert "Slot:20" in success[0].action
    # getInfo(1) + 購入(1) = 2 回
    assert mock_call.call_count == 2


def test_run_asgard_shop_stops_on_not_enough(mock_client, mock_sleep):
    """NotEnough エラー時に残りをすべてスキップすることを検証。"""
    client, mock_call = mock_client

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    res_not_enough = MagicMock()
    res_not_enough.is_success = False
    res_not_enough.error_name = "NotEnough"
    responses.append(res_not_enough)
    mock_call.side_effect = responses

    result = run_asgard_shop(client)

    assert result.bought == 0
    assert result.spent == 0
    assert result.failed_count == 1
    errored = [item for item in result.items if item.status == ResponseStatus.ERROR]
    assert len(errored) == 1
    assert "Slot:8" in errored[0].action
    skipped = [item for item in result.items if item.status == ResponseStatus.SKIPPED]
    assert len(skipped) == 15
    # NotEnough 後は追加の購入呼び出しを行わない
    assert mock_call.call_count == 2


def test_run_asgard_shop_dry_run_makes_no_buy_calls(mock_client, mock_sleep):
    """dry-run では getInfo のみ呼び出し、購入計画を返すことを検証。"""
    client, mock_call = mock_client

    mock_call.side_effect = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]

    result = run_asgard_shop(client, dry_run=True)

    assert result.bought == 13  # 計画上の購入可能数
    assert result.spent == 1000
    assert result.remaining == 0
    assert mock_call.call_count == 1  # 購入は実行されない


def test_run_asgard_shop_maestro_week_buys(mock_client, mock_sleep):
    """Maestro 週は組み合わせ最適化で選定した 11 商品（S6+A3+B2）を購入する。"""
    client, mock_call = mock_client

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_MAESTRO)]
    for _ in range(11):  # S(6) + A(3) + B(2) = 11 件（残高 1000 で全購入可能）
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client)

    assert result.skipped is False
    assert result.bought == 11
    assert result.spent == 1000
    assert result.remaining == 0
    assert result.failed_count == 0
    # ゴールドバフ（conftest の gold=1000）は残高不足で購入されない
    assert result.gold_bought == 0
    success = [item for item in result.items if item.status == ResponseStatus.SUCCESS]
    assert [item.action for item in success] == [
        f"[Realm Traveler] Slot:{s} -> buff {dummy._MAESTRO_BUFF_IDS[s]} (x{dummy._MAESTRO_BUFF_VALUES[s]}, {dummy._MAESTRO_PRICES[s]} Valor Emblems)"
        for s in (11, 15, 9, 7, 17, 16, 14, 12, 10, 19, 8)
    ]
    # getInfo(1) + 購入(11) = 12 回
    assert mock_call.call_count == 12


def test_run_asgard_shop_skips_unknown_week(mock_client, mock_sleep):
    """Osh / Maestro 以外のラインナップでは購入せずスキップする。"""
    client, mock_call = mock_client

    res_unknown = MagicMock()
    res_unknown.is_success = True
    res_unknown.detail = {
        "response": {
            "shop": {
                "6": {"branch": "", "buffId": 500, "buffValue": 3, "buyLimit": 1,
                      "cost": {"coin": {"30": 50}}, "rank": 3, "requirement": "", "boughtCount": 0},
            },
            "coins": 1000,
        }
    }
    mock_call.side_effect = [res_unknown]

    result = run_asgard_shop(client)

    assert result.skipped is True
    assert result.bought == 0
    assert result.items == []
    assert mock_call.call_count == 1


def test_run_asgard_shop_empty_shop_skips_cleanly(mock_client, mock_sleep):
    """空 shop（全 slot 買い切り済み）はエラー扱いせずスキップで正常終了する。"""
    client, mock_call = mock_client

    res_empty = MagicMock()
    res_empty.is_success = True
    res_empty.detail = {"response": {"shop": {}, "coins": 300}}
    mock_call.side_effect = [res_empty]

    result = run_asgard_shop(client)

    assert result.skipped is True
    assert result.bought == 0
    assert result.spent == 0
    assert result.remaining == 300
    assert result.error is None
    assert mock_call.call_count == 1  # buy は一切呼ばれない


def test_run_asgard_shop_fetch_error_reports_error(mock_client, mock_sleep):
    """clanRaid_getInfo 失敗時はエラーを報告し empty 結果（error 付き）を返すことを検証。"""
    client, mock_call = mock_client

    res_error = MagicMock()
    res_error.is_success = False
    res_error.error_name = "notFound"
    mock_call.side_effect = [res_error]

    result = run_asgard_shop(client)

    assert result.bought == 0
    assert result.items == []
    assert result.skipped is False
    assert result.error is not None  # multi サマリで失敗判定に使われる
    assert "notFound" in result.error
    assert mock_call.call_count == 1


def test_is_osh_shop_subset_of_signature_is_osh():
    """買い切った slot が省略された部分集合でも Osh と判定される。"""
    shop = dummy.CLAN_RAID_GET_INFO_OSH["results"][0]["result"]["response"]["shop"]
    # 一部 slot だけ残った shop（= 部分集合）
    subset_shop = {k: v for k, v in shop.items() if int(k) in (6, 7, 8)}
    assert is_osh_shop(subset_shop) is True
    # 空 shop は判定不能 → Osh とみなさない
    assert is_osh_shop({}) is False


def test_is_maestro_shop():
    """Maestro シグネチャ判定（Osh 週は False）。"""
    maestro_shop = dummy.CLAN_RAID_GET_INFO_MAESTRO["results"][0]["result"]["response"]["shop"]
    osh_shop = dummy.CLAN_RAID_GET_INFO_OSH["results"][0]["result"]["response"]["shop"]
    assert is_maestro_shop(maestro_shop) is True
    assert is_maestro_shop(osh_shop) is False
    # 一部 slot だけ残った shop（= 部分集合）も Maestro と判定される
    subset_shop = {k: v for k, v in maestro_shop.items() if int(k) in (6, 7, 11)}
    assert is_maestro_shop(subset_shop) is True
    # 空 shop は判定不能 → Maestro とみなさない
    assert is_maestro_shop({}) is False


def test_select_maestro_plan_full_budget():
    """残高 1000 では S(6) + A(3) + B(2) の全 11 商品が順位順に選ばれる。"""
    shop = dummy.CLAN_RAID_GET_INFO_MAESTRO["results"][0]["result"]["response"]["shop"]
    plan = select_maestro_plan(shop, 1000)

    assert [item.slot_id for item in plan] == [11, 15, 9, 7, 17, 16, 14, 12, 10, 19, 8]
    assert sum(item.price for item in plan) == 1000


def test_select_maestro_plan_excludes_c_rank():
    """C ランク（優先度表にない slot）は残高が足りても購入候補に含まれない。"""
    shop = dummy.CLAN_RAID_GET_INFO_MAESTRO["results"][0]["result"]["response"]["shop"]
    plan = select_maestro_plan(shop, 10_000)
    assert all(item.slot_id not in (6, 13, 18, 21) for item in plan)


def test_select_maestro_plan_prioritizes_s_count():
    """S 数を最優先する（「上位バフ 1 個の確保」は「下位バフ複数」より優先）。

    残高 100 のとき、S 1 個（slot 11 = 100）は S 2 個（slot 15 + 9 = 100）より
    優先度が低いため、S 2 個の組み合わせが選ばれる。
    """
    shop = dummy.CLAN_RAID_GET_INFO_MAESTRO["results"][0]["result"]["response"]["shop"]
    plan = select_maestro_plan(shop, 100)

    assert [item.slot_id for item in plan] == [15, 9]
    assert sum(item.price for item in plan) == 100


def test_select_maestro_plan_prefers_higher_rank_within_s():
    """同一ランク内では高順位を優先する。

    残高 150 のとき、S 2 個（slot 11 + 15 = 順位 1+2）は S 2 個 + A 1 個
    （slot 15 + 9 + 14 = 順位 2+3 + A7）より優先される。
    """
    shop = dummy.CLAN_RAID_GET_INFO_MAESTRO["results"][0]["result"]["response"]["shop"]
    plan = select_maestro_plan(shop, 150)

    assert [item.slot_id for item in plan] == [11, 15]
    assert sum(item.price for item in plan) == 150


def test_maestro_eval_key_tiebreak_by_cost():
    """評価キーは S/A/B 構成・順位合計が同じならコストが小さい方を優先する。"""
    from hw_genie.commands.asgard_shop import AsgardItem, _maestro_eval_key

    # S2+S5（200）と S1+S6（250）はどちらも S 2 個・順位合計 7 → コストが小さい方が優先
    combo_cheap = (
        AsgardItem(slot_id=15, buff_id=125, buff_value=2, price=50),
        AsgardItem(slot_id=17, buff_id=128, buff_value=3, price=150),
    )
    combo_expensive = (
        AsgardItem(slot_id=11, buff_id=118, buff_value=10, price=100),
        AsgardItem(slot_id=16, buff_id=127, buff_value=20, price=150),
    )
    assert _maestro_eval_key(combo_cheap) > _maestro_eval_key(combo_expensive)


def test_select_maestro_plan_uses_best_combination():
    """残高 250 では S 3 個 + A 1 個（250）が S 2 個（200）より優先される。"""
    shop = dummy.CLAN_RAID_GET_INFO_MAESTRO["results"][0]["result"]["response"]["shop"]
    plan = select_maestro_plan(shop, 250)

    assert sum(item.price for item in plan) == 250
    assert {item.slot_id for item in plan} == {11, 15, 9, 14}


def test_select_maestro_plan_empty_when_nothing_affordable():
    """残高不足で 1 個も買えない場合は空プランを返す。"""
    shop = dummy.CLAN_RAID_GET_INFO_MAESTRO["results"][0]["result"]["response"]["shop"]
    assert select_maestro_plan(shop, 10) == []


def test_run_asgard_shop_gold_buffs_purchased(mock_client, mock_sleep):
    """--gold（gold_buffs=True）なら Osh 週でもゴールドバフを購入する。"""
    client, mock_call = mock_client
    status = MagicMock()
    status.gold = 15_000_000
    client.fetch_player_status = MagicMock(return_value=status)

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    for _ in range(15):  # 1500万 / 100万 = slot 1〜5 の 5 回ずつ（15 回）
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    for _ in range(13):  # Valor 商品（残高 1000）
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client, gold_buffs=True)

    assert result.gold_bought == 15
    assert result.gold_spent == 15_000_000
    assert result.bought == 13
    assert result.spent == 1000
    # getInfo(1) + ゴールドバフ(15) + Valor(13) = 29 回
    assert mock_call.call_count == 29
    gold_success = [
        item for item in result.items
        if item.status == ResponseStatus.SUCCESS and "Gold" in item.action
    ]
    assert len(gold_success) == 15


def test_run_asgard_shop_gold_buffs_disabled(mock_client, mock_sleep):
    """gold_buffs=False ではゴールドバフを購入しない（Valor のみ）。"""
    client, mock_call = mock_client

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    for _ in range(13):
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client, gold_buffs=False)

    assert result.gold_bought == 0
    assert result.gold_spent == 0
    assert result.bought == 13
    assert mock_call.call_count == 14


def test_run_asgard_shop_gold_buffs_insufficient_gold(mock_client, mock_sleep):
    """ゴールド残高が 100 万未満ならゴールドバフを購入せず Valor は続行する。"""
    client, mock_call = mock_client
    status = MagicMock()
    status.gold = 999_999
    client.fetch_player_status = MagicMock(return_value=status)

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    for _ in range(13):
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client, gold_buffs=True)

    assert result.gold_bought == 0
    assert result.gold_spent == 0
    assert result.bought == 13
    # getInfo(1) + Valor(13) = 14 回（ゴールドバフ購入は発生しない）
    assert mock_call.call_count == 14


def test_run_asgard_shop_gold_buffs_zero_balance(mock_client, mock_sleep, capsys):
    """ゴールド残高 0（API がエラーを投げず 0 を返すケース）でも Valor は続行する。"""
    client, mock_call = mock_client
    status = MagicMock()
    status.gold = 0
    client.fetch_player_status = MagicMock(return_value=status)

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    for _ in range(13):
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client, gold_buffs=True)

    assert result.gold_bought == 0
    assert result.gold_spent == 0
    assert result.bought == 13
    assert "Gold buffs: insufficient gold" in capsys.readouterr().out


def test_run_asgard_shop_gold_buffs_stops_on_not_enough(mock_client, mock_sleep):
    """ゴールドバフ購入中に NotEnough が起きたら残りを打ち切り、Valor は続行する。"""
    client, mock_call = mock_client
    status = MagicMock()
    status.gold = 15_000_000
    client.fetch_player_status = MagicMock(return_value=status)

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))  # ゴールドバフ 1 回目
    res_not_enough = MagicMock()
    res_not_enough.is_success = False
    res_not_enough.error_name = "NotEnough"
    responses.append(res_not_enough)  # ゴールドバフ 2 回目 → 打ち切り
    for _ in range(13):  # Valor 商品は続行
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client, gold_buffs=True)

    assert result.gold_bought == 1
    assert result.gold_spent == 1_000_000
    assert result.bought == 13
    gold_failed = [
        item for item in result.items
        if item.status == ResponseStatus.ERROR and "Gold" in item.action
    ]
    assert len(gold_failed) == 1
    # getInfo(1) + gold成功(1) + gold失敗(1) + Valor(13) = 16 回
    assert mock_call.call_count == 16


def test_run_asgard_shop_gold_buffs_dry_run(mock_client, mock_sleep):
    """dry-run ではゴールドバフの計画も表示し、購入は実行しない。"""
    client, mock_call = mock_client
    status = MagicMock()
    status.gold = 15_000_000
    client.fetch_player_status = MagicMock(return_value=status)

    mock_call.side_effect = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]

    result = run_asgard_shop(client, dry_run=True, gold_buffs=True)

    assert result.gold_bought == 15  # 計画上の購入可能数
    assert result.gold_spent == 15_000_000
    assert result.bought == 13
    assert mock_call.call_count == 1  # 購入は実行されない


def test_run_asgard_shop_gold_fetch_failure_skips_gold_buffs(mock_client, mock_sleep, capsys):
    """ゴールド残高取得失敗時は警告を出し、Valor 購入は続行する。"""
    client, mock_call = mock_client
    client.fetch_player_status = MagicMock(side_effect=RuntimeError("boom"))

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    for _ in range(13):
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client, gold_buffs=True)

    assert result.gold_bought == 0
    assert result.gold_spent == 0
    assert result.bought == 13
    assert "Failed to fetch gold balance - skipping gold buffs." in capsys.readouterr().out


def test_run_asgard_shop_gold_buffs_all_bought_out_skips_fetch(mock_client, mock_sleep):
    """ゴールドバフが全買い切り済みの場合は残高取得 API を呼ばない。"""
    client, mock_call = mock_client
    shop = dummy.CLAN_RAID_GET_INFO_OSH["results"][0]["result"]["response"]["shop"]
    shop = dict(shop)
    for slot_id in ("1", "2", "3", "4", "5"):
        shop[slot_id] = dict(shop[slot_id]) | {"boughtCount": 5}
    dummy_res = dict(dummy.CLAN_RAID_GET_INFO_OSH["results"][0]["result"]["response"]) | {"shop": shop}
    get_info = {"results": [{"result": {"response": dummy_res}}]}
    responses = [_res_from(get_info)]
    for _ in range(13):
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client, gold_buffs=True)

    assert result.gold_bought == 0
    client.fetch_player_status.assert_not_called()


def test_run_asgard_shop_gold_buffs_osh_week_default_off(mock_client, mock_sleep, capsys):
    """Osh 週デフォルト（gold_buffs=None）ではゴールドバフを購入しない。"""
    client, mock_call = mock_client

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_OSH)]
    for _ in range(13):
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client)

    assert result.gold_bought == 0
    assert result.gold_spent == 0
    assert result.bought == 13
    client.fetch_player_status.assert_not_called()
    out = capsys.readouterr().out
    assert "Gold buffs: skipped (default off for Osh week" in out
    assert "use --gold to enable" in out


def test_run_asgard_shop_gold_buffs_maestro_week_default_on(mock_client, mock_sleep):
    """Maestro 週デフォルト（gold_buffs=None）ではゴールドバフを購入する。"""
    client, mock_call = mock_client
    status = MagicMock()
    status.gold = 15_000_000
    client.fetch_player_status = MagicMock(return_value=status)

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_MAESTRO)]
    for _ in range(15):  # ゴールドバフ slot 1〜5（Maestro 週も slot 1〜5 がゴールド）
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    for _ in range(11):  # Valor 商品（S6 + A3 + B2 = 11 件、残高 1000）
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client)

    assert result.gold_bought == 15
    assert result.gold_spent == 15_000_000
    assert result.bought == 11
    # getInfo(1) + ゴールドバフ(15) + Valor(11) = 27 回
    assert mock_call.call_count == 27


def test_run_asgard_shop_gold_buffs_maestro_week_no_gold(mock_client, mock_sleep):
    """--no-gold（gold_buffs=False）なら Maestro 週でもゴールドバフを購入しない。"""
    client, mock_call = mock_client

    responses = [_res_from(dummy.CLAN_RAID_GET_INFO_MAESTRO)]
    for _ in range(11):
        responses.append(_res_from(dummy.CLAN_RAID_SHOP_BUY_SUCCESS))
    mock_call.side_effect = responses

    result = run_asgard_shop(client, gold_buffs=False)

    assert result.gold_bought == 0
    assert result.gold_spent == 0
    assert result.bought == 11
    client.fetch_player_status.assert_not_called()


def test_parse_gold_slot():
    """parse_gold_slot はゴールド価格の slot のみをパースする。"""
    from hw_genie.commands.asgard_shop import parse_gold_slot

    parsed = parse_gold_slot("1", {"buffId": 61, "buffValue": 3, "cost": {"gold": 1000000}})
    assert parsed is not None
    assert parsed.slot_id == 1
    assert parsed.buff_id == 61
    assert parsed.price == 1_000_000
    # ゴールド価格なし（Valor 商品）は None
    assert parse_gold_slot("6", {"buffId": 66, "cost": {"coin": {"30": 50}}}) is None
    # 価格 0 以下は None
    assert parse_gold_slot("1", {"cost": {"gold": 0}}) is None
    assert parse_gold_slot("1", {"cost": {"gold": -5}}) is None
    # 構造不正・数値化不可の slotId は None
    assert parse_gold_slot("1", "not-a-dict") is None
    assert parse_gold_slot("1", {"cost": "invalid"}) is None
    assert parse_gold_slot("x", {"cost": {"gold": 1000000}}) is None


def test_build_buy_queue_excludes_malformed_slots():
    """価格 0 以下・非 dict・cost 不正の slot は購入候補から除外される。"""
    shop = dummy.CLAN_RAID_GET_INFO_OSH["results"][0]["result"]["response"]["shop"]
    shop = dict(shop)
    # 価格 0 のスロット（パース失敗相当）
    shop["22"] = dict(shop["6"]) | {"buffId": 82, "cost": {"coin": {"30": 0}}}
    # 価格マイナスのスロット
    shop["23"] = dict(shop["6"]) | {"buffId": 83, "cost": {"coin": {"30": -5}}}
    # 非 dict スロット
    shop["24"] = "not-a-dict"
    # cost が coin を持たないスロット
    shop["25"] = dict(shop["6"]) | {"buffId": 84, "cost": {"gold": 1000000}}
    # slotId が数値でないスロット（変換失敗時に slotId=0 の購入が飛ぶのを防ぐ）
    shop["x"] = dict(shop["6"]) | {"buffId": 85}

    queue = build_buy_queue(shop)

    assert all(item.slot_id not in (22, 23, 24, 25) for item in queue)
    # "x" は数値化できないため候補に含まれない
    assert all(item.slot_id != 0 for item in queue)


def test_build_buy_queue_handles_string_counts_and_missing_coins():
    """boughtCount/buyLimit が str でも購入済み判定が機能する。"""
    shop = {
        "6": {"branch": "", "buffId": 66, "buffValue": 3, "buyLimit": "1", "cost": {"coin": {"30": 50}}, "rank": 3, "requirement": "", "boughtCount": "1"},
        "7": {"branch": "", "buffId": 67, "buffValue": 25, "buyLimit": 1, "cost": {"coin": {"30": 150}}, "rank": 1, "requirement": "", "boughtCount": 0},
    }
    queue = build_buy_queue(shop)
    # 文字列 "1" の boughtCount は購入済み扱い → slot 7 のみ残る
    assert [item.slot_id for item in queue] == [7]


def test_fetch_missing_coins_defaults_to_zero():
    """coins キー欠落時は残高 0 として扱われ、購入は行われない。"""
    from hw_genie.commands.asgard_shop import fetch_clan_raid_shop

    shop = dummy.CLAN_RAID_GET_INFO_OSH["results"][0]["result"]["response"]["shop"]
    res = MagicMock()
    res.is_success = True
    res.detail = {"response": {"shop": shop}}

    client = MagicMock()
    client.call.return_value = res

    fetched_shop, coins = fetch_clan_raid_shop(client)
    assert fetched_shop == shop
    assert coins == 0

# --- main.cmd_asgard_shop（exit code / エラー検知） ---


def test_cmd_asgard_shop_fetch_failure_exits_nonzero():
    """main.cmd_asgard_shop は在庫取得失敗（result.error）時に exit code 1 で終了する。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands.asgard_shop import AsgardRunResult

    class _Args:
        account = None
        dry_run = False
        gold_buffs = None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main_mod, "_ensure_session", lambda args: {"auth": "dummy"})

        def _run(*a, **k):
            return AsgardRunResult(
                coins=0,
                spent=0,
                remaining=0,
                bought=0,
                skipped=False,
                items=[],
                error="clanRaid_getInfo failed (notFound)",
            )

        mp.setattr(main_mod.HWClient, "__init__", lambda self, headers: None)
        mp.setattr("hw_genie.commands.asgard_shop.run_asgard_shop", _run)
        with pytest.raises(SystemExit) as exc_info:
            main_mod.cmd_asgard_shop(_Args())
    assert exc_info.value.code == 1


def test_cmd_asgard_shop_success_exits_zero():
    """main.cmd_asgard_shop は成功時に exit code 0 で終了する。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands.asgard_shop import AsgardRunResult

    class _Args:
        account = None
        dry_run = False
        gold_buffs = None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main_mod, "_ensure_session", lambda args: {"auth": "dummy"})
        mp.setattr(main_mod.HWClient, "__init__", lambda self, headers: None)

        def _run(*a, **k):
            return AsgardRunResult(
                coins=100,
                spent=50,
                remaining=50,
                bought=1,
                skipped=False,
                items=[],
            )

        mp.setattr("hw_genie.commands.asgard_shop.run_asgard_shop", _run)
        assert main_mod.cmd_asgard_shop(_Args()) is None


def test_cmd_asgard_shop_purchase_error_exits_zero():
    """購入エラー（error 未設定・items に ERROR あり）は exit 0（単一モード）。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands.asgard_shop import AsgardResult, AsgardRunResult, ResponseStatus

    class _Args:
        account = None
        dry_run = False
        gold_buffs = None

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main_mod, "_ensure_session", lambda args: {"auth": "dummy"})
        mp.setattr(main_mod.HWClient, "__init__", lambda self, headers: None)

        def _run(*a, **k):
            return AsgardRunResult(
                coins=1000,
                spent=100,
                remaining=900,
                bought=1,
                skipped=False,
                items=[AsgardResult(action="buy 8", status=ResponseStatus.ERROR, error="NotEnough")],
            )

        mp.setattr("hw_genie.commands.asgard_shop.run_asgard_shop", _run)
        assert main_mod.cmd_asgard_shop(_Args()) is None
