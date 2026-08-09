from unittest.mock import MagicMock

from . import dummy_responses as dummy
from hw_genie.commands.asgard_shop import (
    ResponseStatus,
    build_buy_queue,
    is_osh_shop,
    run_asgard_shop,
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


def test_run_asgard_shop_skips_maestro_week(mock_client, mock_sleep):
    """Maestro 週（Osh シグネチャ不一致）では購入せずスキップすることを検証。"""
    client, mock_call = mock_client

    mock_call.side_effect = [_res_from(dummy.CLAN_RAID_GET_INFO_MAESTRO)]

    result = run_asgard_shop(client)

    assert result.skipped is True
    assert result.bought == 0
    assert result.items == []
    assert mock_call.call_count == 1


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

    queue = build_buy_queue(shop)

    assert all(item.slot_id not in (22, 23, 24, 25) for item in queue)


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
