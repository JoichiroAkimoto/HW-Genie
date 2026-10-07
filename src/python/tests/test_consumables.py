from unittest.mock import MagicMock

import logging

import pytest

from . import dummy_responses as dummy
from hw_genie.commands.consumables import run_consumable_use, run_inventory, _chunk_sizes
from hw_genie.core.client import HWAuthError, ResponseStatus
from hw_genie.core.consumables import CONSUMABLE_USE_TARGETS, CONSUMABLE_REGISTRY
from hw_genie.core.inventory import (
    ConsumableUseResult,
    fetch_inventory,
    parse_use_rewards,
    use_consumable,
)
from hw_genie.runner import consumable_routine, summarize_consumable


def _res_from(dummy_response: dict, is_success: bool = True, error_name: str | None = None) -> MagicMock:
    """dummy_responses の結果 dict を HWResponse 風の MagicMock に変換する。"""
    res = MagicMock()
    res.is_success = is_success
    res.error_name = error_name
    res.detail = dummy_response["results"][0].get("result")
    return res


def _lootbox_success(amount: int) -> MagicMock:
    """amount 分の消費成功レスポンス（応答キー = 消費数）を生成する。"""
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {
        "response": {
            str(amount): {
                "fragmentScroll": {"218": 5, "192": 10, "193": 15, "216": 5},
                "fragmentGear": {"91": 10, "93": 10, "171": 5, "94": 5},
            }
        }
    }
    return res


# --- fetch_inventory ---


def test_fetch_inventory_parses_categories(mock_client):
    """inventoryGet のレスポンスをカテゴリ別 {libId: 個数} にパースする。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_CONSUMABLE)

    snapshot = fetch_inventory(client)

    assert snapshot.consumable == {17: 327, 20: 1142335, 215: 48}
    assert snapshot.categories["gear"] == {4: 866}
    assert snapshot.categories["scroll"] == {100: 5}
    assert mock_call.call_count == 1


def test_fetch_inventory_raises_on_api_error(mock_client):
    """API エラー時は InventoryReadError を送出する。"""
    from hw_genie.core.inventory import InventoryReadError

    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = False
    res.error_name = "auth"
    res.status = ResponseStatus.ERROR
    mock_call.return_value = res

    try:
        fetch_inventory(client)
        assert False, "InventoryReadError expected"
    except InventoryReadError as exc:
        assert exc.error_name == "auth"


def test_fetch_inventory_reraises_auth_error(mock_client):
    """認証エラー（HWAuthError）は握りつぶさず再送出する。"""
    client, mock_call = mock_client
    mock_call.side_effect = HWAuthError("session expired")

    with pytest.raises(HWAuthError):
        fetch_inventory(client)


def test_use_consumable_reraises_auth_error(mock_client):
    """消費 API の認証エラー（HWAuthError）は握りつぶさず再送出する。"""
    client, mock_call = mock_client
    mock_call.side_effect = HWAuthError("session expired")

    with pytest.raises(HWAuthError):
        use_consumable(client, lib_id=215, amount=48, method="consumableUseLootBox")


# --- parse_use_rewards / use_consumable ---


def test_parse_use_rewards_sums_category_quantities():
    """報酬をカテゴリ別合計に集計する（実測レスポンス準拠）。"""
    detail = dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS["results"][0]["result"]

    rewards = parse_use_rewards(detail, 48)

    assert rewards == {"fragmentScroll": 35, "fragmentGear": 30}
    assert parse_use_rewards(detail, 99) == {"fragmentScroll": 35, "fragmentGear": 30}  # キー不一致でもキャップ応答として集計


def test_parse_use_rewards_scalar_values():
    """スカラー報酬（stamina 等）はそのまま合計に加える。"""
    detail = {"response": {"1": {"stamina": 120, "gold": 5000}}}

    assert parse_use_rewards(detail, 1) == {"stamina": 120, "gold": 5000}


def test_use_consumable_success(mock_client):
    """全量消費を payload に組み立て、報酬を返す。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS)

    result = use_consumable(client, lib_id=215, amount=48, method="consumableUseLootBox")

    call_args = mock_call.call_args.args[0]
    assert call_args["calls"][0]["name"] == "consumableUseLootBox"
    assert call_args["calls"][0]["args"] == {"libId": 215, "amount": 48}
    assert result.status == ResponseStatus.SUCCESS
    assert result.consumed == 48
    assert result.rewards["fragmentScroll"] == 35


def test_use_consumable_includes_reward_choice_index(mock_client):
    """選択式ボックスは playerRewardChoiceIndex を args に含めて送信する。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS)

    result = use_consumable(
        client,
        lib_id=47,
        amount=3,
        method="consumableUseLootBox",
        player_reward_choice_index=2,
    )

    call_args = mock_call.call_args.args[0]
    assert call_args["calls"][0]["args"] == {
        "libId": 47,
        "amount": 3,
        "playerRewardChoiceIndex": 2,
    }
    assert result.status == ResponseStatus.SUCCESS


def test_use_consumable_includes_recursive_when_true(mock_client):
    """recursive=True は args に recursive: True を含めて送信する。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS)

    result = use_consumable(
        client, lib_id=509, amount=17, method="consumableUseLootBox", recursive=True
    )

    call_args = mock_call.call_args.args[0]
    assert call_args["calls"][0]["args"] == {
        "libId": 509,
        "amount": 17,
        "recursive": True,
    }
    assert result.status == ResponseStatus.SUCCESS


def test_use_consumable_omits_recursive_when_false(mock_client):
    """recursive=False の明示渡しは args に recursive を含めない。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS)

    use_consumable(client, lib_id=215, amount=48, method="consumableUseLootBox", recursive=False)

    call_args = mock_call.call_args.args[0]
    assert call_args["calls"][0]["args"] == {"libId": 215, "amount": 48}


def test_use_consumable_recursive_only_for_lootbox(mock_client, caplog):
    """recursive=True でも lootbox 以外のメソッドには recursive を付与しない（警告あり）。

    native 非 lootbox（17）と、recursive 登録 lib の --method 上書き経路（509）の
    両方をカバーする。
    """
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS)

    with caplog.at_level(logging.WARNING):
        use_consumable(client, lib_id=17, amount=1, method="consumableUseStamina", recursive=True)
        use_consumable(client, lib_id=509, amount=17, method="consumableUseStamina", recursive=True)

    sent = [c.args[0]["calls"][0]["args"] for c in mock_call.call_args_list]
    assert sent == [{"libId": 17, "amount": 1}, {"libId": 509, "amount": 17}]
    assert any(
        "recursive" in r.getMessage() and "17" in r.getMessage() and "consumableUseStamina" in r.getMessage()
        for r in caplog.records
    )
    assert any(
        "recursive" in r.getMessage() and "509" in r.getMessage() and "consumableUseStamina" in r.getMessage()
        for r in caplog.records
    )


def test_use_consumable_choice_only_for_lootbox(mock_client, caplog):
    """choice 指定があっても lootbox 以外のメソッドには choice を付与しない（警告あり）。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS)

    with caplog.at_level(logging.WARNING):
        use_consumable(
            client,
            lib_id=47,
            amount=3,
            method="consumableUseStamina",
            player_reward_choice_index=2,
            recursive=True,
        )

    call_args = mock_call.call_args.args[0]
    assert call_args["calls"][0]["args"] == {"libId": 47, "amount": 3}
    assert any(
        "recursive" in r.getMessage() and "47" in r.getMessage() and "consumableUseStamina" in r.getMessage()
        for r in caplog.records
    )
    assert any(
        "playerRewardChoiceIndex" in r.getMessage()
        and "47" in r.getMessage()
        and "consumableUseStamina" in r.getMessage()
        for r in caplog.records
    )


def test_use_consumable_api_error(mock_client):
    """API エラー（limitReached 等）は status=ERROR で返す。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(
        dummy.CONSUMABLE_USE_LIMIT_REACHED, is_success=False, error_name="limitReached"
    )

    result = use_consumable(client, lib_id=215, amount=48, method="consumableUseLootBox")

    assert result.status == ResponseStatus.ERROR
    assert result.error_name == "limitReached"
    assert result.consumed == 0


def test_use_consumable_capped_response_counts_actual(mock_client):
    """サーバーが消費数をキャップしたレスポンスでは実際の消費数を集計する。"""
    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {
        "response": {  # 1000 要求 → 48 でキャップ応答
            "48": {"fragmentScroll": {"218": 5}, "fragmentGear": {"91": 10}},
        }
    }
    mock_call.return_value = res

    result = use_consumable(client, lib_id=169, amount=1000, method="consumableUseLootBox")

    assert result.status == ResponseStatus.SUCCESS
    assert result.consumed == 48  # requested (1000) ではなく実際の消費数
    assert result.rewards["fragmentScroll"] == 5


def test_use_consumable_empty_response_falls_back_to_requested(mock_client):
    """消費数キーの無い成功応答は requested にフォールバックする（挙動の固定）。"""
    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.error_name = None
    res.detail = {"response": {}}
    mock_call.return_value = res

    result = use_consumable(client, lib_id=215, amount=48, method="consumableUseLootBox")

    assert result.status == ResponseStatus.SUCCESS
    assert result.consumed == 48
    assert result.rewards == {}


# --- run_consumable_use（CLI コマンド本体） ---


def test_registry_covers_all_use_targets():
    """CONSUMABLE_USE_TARGETS の全対象がレジストリ登録済みで lootbox メソッドを持つ。"""
    assert len(CONSUMABLE_USE_TARGETS) == 62  # 215 + Add-Consumables.md 記載 + Doll/Box 5 種 + Boxy/Doll/Bag 4 種 + Ascension Chest 502 + Cosmic Titans Battle Chest 508 + 478/509/513
    for lib_id in CONSUMABLE_USE_TARGETS:
        assert lib_id in CONSUMABLE_REGISTRY
        assert CONSUMABLE_REGISTRY[lib_id].method == "consumableUseLootBox"
    # 17（Stamina Potion）はレジストリ登録のみで一括消費対象外
    assert 17 not in CONSUMABLE_USE_TARGETS
    assert CONSUMABLE_REGISTRY[17].method == "consumableUseStamina"
    # 1000 分割対象（ドキュメント記載の 7 種と完全一致）
    chunked = {
        lib_id
        for lib_id in CONSUMABLE_USE_TARGETS
        if CONSUMABLE_REGISTRY[lib_id].max_amount > 0
    }
    assert chunked == {169, 170, 171, 172, 173, 271, 272}
    # 分割対象以外は上限なし（在庫全量を 1 リクエストで消費）
    for lib_id in set(CONSUMABLE_USE_TARGETS) - chunked:
        assert CONSUMABLE_REGISTRY[lib_id].max_amount == 0
    # 選択式報酬ボックス（playerRewardChoiceIndex 指定、Add-Consumables.md 記載）
    choices = {
        lib_id: CONSUMABLE_REGISTRY[lib_id].player_reward_choice_index
        for lib_id in (47, 48, 49, 50, 328, 62, 63, 64)
    }
    assert choices == {47: 2, 48: 2, 49: 2, 50: 2, 328: 0, 62: 4, 63: 4, 64: 4}
    # 選択式以外は playerRewardChoiceIndex 未指定（args に含めない）
    for lib_id in set(CONSUMABLE_USE_TARGETS) - set(choices):
        assert CONSUMABLE_REGISTRY[lib_id].player_reward_choice_index is None
    # クライアント側ラウンドループ対象の既存定義（サーバー側 recursive 対象の 7 件とは別概念）
    for lib_id in (149, 469, 492, 497, 176, 185, 187, 190):
        assert lib_id in CONSUMABLE_USE_TARGETS


def test_registry_recursive_flags_confirmed():
    """recursive=True（本番実測済み 3 件：478/509/513）。"""
    confirmed = {478, 509, 513}
    assert len(confirmed) == 3
    for lib_id in confirmed:
        assert CONSUMABLE_REGISTRY[lib_id].recursive is True


def test_registry_recursive_flags_documented_unmeasured():
    """recursive=True（既知マトリョーシカ 4 件：149/469/492/497）。

    TODO: サーバー側 recursive の本番実測後に confirmed へ昇格させる。
    """
    documented = {149, 469, 492, 497}
    assert len(documented) == 4
    for lib_id in documented:
        assert CONSUMABLE_REGISTRY[lib_id].recursive is True
    # 同名のみでは不十分：508（492 と同名）・493（513 と同名）は False
    assert CONSUMABLE_REGISTRY[508].recursive is False
    assert CONSUMABLE_REGISTRY[493].recursive is False
    # サーバー側 recursive 対象は計 7 件
    recursive_true = {
        lib_id for lib_id in CONSUMABLE_USE_TARGETS if CONSUMABLE_REGISTRY[lib_id].recursive
    }
    assert recursive_true == documented | {478, 509, 513}
    assert len(recursive_true) == 7
    for lib_id in CONSUMABLE_USE_TARGETS:
        assert CONSUMABLE_REGISTRY[lib_id].recursive is (lib_id in recursive_true)
    assert CONSUMABLE_REGISTRY[17].recursive is False


def test_registry_new_items_478_509_513():
    """新規追加（478/509/513）の名前・対象登録・recursive=True。"""
    assert CONSUMABLE_REGISTRY[478].name == "Titans Tesseract of Luck"
    assert CONSUMABLE_REGISTRY[509].name == "Mead Festival Chest"
    assert CONSUMABLE_REGISTRY[513].name == "Titan Upgrade Chest"
    for lib_id in (478, 509, 513):
        assert lib_id in CONSUMABLE_USE_TARGETS
        assert CONSUMABLE_REGISTRY[lib_id].recursive is True


def test_registry_recursive_rejects_non_lootbox_method():
    """recursive=True かつ lootbox 以外のメソッドは登録時に拒否する。"""
    from hw_genie.core.consumables import ConsumableInfo

    with pytest.raises(ValueError):
        ConsumableInfo(name="X", method="consumableUseStamina", recursive=True)


def test_registry_choice_rejects_non_lootbox_method():
    """choice 指定かつ lootbox 以外のメソッドは登録時に拒否する。"""
    from hw_genie.core.consumables import ConsumableInfo

    with pytest.raises(ValueError):
        ConsumableInfo(
            name="Y", method="consumableUseStamina", player_reward_choice_index=2
        )


def test_chunk_sizes_boundaries():
    """_chunk_sizes の境界値: 上限ちょうど・上限なし・在庫 0・端数。"""
    assert _chunk_sizes(1000, 1000) == [1000]
    assert _chunk_sizes(999, 1000) == [999]
    assert _chunk_sizes(2500, 1000) == [1000, 1000, 500]
    assert _chunk_sizes(100, 0) == [100]
    assert _chunk_sizes(0, 1000) == [0]


def test_run_consumable_use_consumes_registered_targets(mock_client, mock_sleep):
    """登録済み対象（215）を在庫全量（48）消費し、検証ラウンドまで実行する。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # ラウンド1: 在庫取得
        _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS),  # 消費 48
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(client, lib_ids=[215])

    assert len(results) == 1
    result = results[0]
    assert result.lib_id == 215
    assert result.status == ResponseStatus.SUCCESS
    assert result.consumed == 48
    assert result.name == CONSUMABLE_REGISTRY[215].name
    # inventory(1) + use(1) + 検証 inventory(1)
    assert mock_call.call_count == 3


def test_run_consumable_use_passes_reward_choice_index(mock_client, mock_sleep):
    """選択式ボックスはレジストリの playerRewardChoiceIndex を args に含めて消費する。"""
    client, mock_call = mock_client
    inv = MagicMock()
    inv.is_success = True
    inv.error_name = None
    inv.detail = {"response": {"consumable": {"47": 3}}}
    mock_call.side_effect = [
        inv,  # ラウンド1: 在庫取得
        _lootbox_success(3),  # 消費 3
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(client, lib_ids=[47])

    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 3
    use_calls = [
        c
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseLootBox"
    ]
    assert len(use_calls) == 1
    assert use_calls[0].args[0]["calls"][0]["args"] == {
        "libId": 47,
        "amount": 3,
        "playerRewardChoiceIndex": 2,
    }


def test_run_consumable_use_passes_recursive_for_other_chests(mock_client, mock_sleep):
    """509（Other Chests）は recursive を付けて消費する。"""
    client, mock_call = mock_client
    inv = MagicMock()
    inv.is_success = True
    inv.error_name = None
    inv.detail = {"response": {"consumable": {"509": 17}}}
    mock_call.side_effect = [
        inv,  # ラウンド1: 在庫取得
        _lootbox_success(17),  # 消費 17
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(client, lib_ids=[509])

    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 17
    use_calls = [
        c
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseLootBox"
    ]
    assert len(use_calls) == 1
    assert use_calls[0].args[0]["calls"][0]["args"] == {
        "libId": 509,
        "amount": 17,
        "recursive": True,
    }


def test_run_consumable_use_omits_recursive_for_non_lootbox_override(
    mock_client, mock_sleep
):
    """recursive=True の登録 lib でも非 lootbox の --method 上書き時は recursive を付与しない。"""
    client, mock_call = mock_client
    inv = MagicMock()
    inv.is_success = True
    inv.error_name = None
    inv.detail = {"response": {"consumable": {"509": 17}}}
    mock_call.side_effect = [
        inv,  # ラウンド1: 在庫取得
        _lootbox_success(17),  # 消費 17
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(
        client, lib_ids=[509], method_override="consumableUseStamina"
    )

    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 17
    use_calls = [
        c
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseStamina"
    ]
    assert len(use_calls) == 1
    assert use_calls[0].args[0]["calls"][0]["args"] == {"libId": 509, "amount": 17}


def test_run_consumable_use_omits_choice_for_non_lootbox_override(
    mock_client, mock_sleep
):
    """choice 付きの登録 lib でも非 lootbox の --method 上書き時は choice を付与しない。"""
    client, mock_call = mock_client
    inv = MagicMock()
    inv.is_success = True
    inv.error_name = None
    inv.detail = {"response": {"consumable": {"47": 3}}}
    mock_call.side_effect = [
        inv,  # ラウンド1: 在庫取得
        _lootbox_success(3),  # 消費 3
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(
        client, lib_ids=[47], method_override="consumableUseStamina"
    )

    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 3
    use_calls = [
        c
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseStamina"
    ]
    assert len(use_calls) == 1
    assert use_calls[0].args[0]["calls"][0]["args"] == {"libId": 47, "amount": 3}


def test_run_consumable_use_dry_run_recursive_display_gate(mock_client, mock_sleep, capsys):
    """dry-run 表示は lootbox 解決時のみ `(recursive)` を含める。"""
    client, mock_call = mock_client
    inv = MagicMock()
    inv.is_success = True
    inv.error_name = None
    inv.detail = {"response": {"consumable": {"509": 17, "47": 3}}}

    mock_call.return_value = inv
    run_consumable_use(client, lib_ids=[509], dry_run=True)
    assert "(recursive)" in capsys.readouterr().out

    mock_call.return_value = inv
    run_consumable_use(client, lib_ids=[509], method_override="consumableUseStamina", dry_run=True)
    assert "(recursive)" not in capsys.readouterr().out


def test_run_consumable_use_dry_run_warns_on_dropped_args(mock_client, mock_sleep, capsys):
    """dry-run でも非 lootbox 上書きで剥落される引数があれば dropped 警告を出す。"""
    client, mock_call = mock_client
    inv = MagicMock()
    inv.is_success = True
    inv.error_name = None
    inv.detail = {"response": {"consumable": {"509": 17, "47": 3}}}

    mock_call.return_value = inv
    run_consumable_use(
        client, lib_ids=[509, 47], method_override="consumableUseStamina", dry_run=True
    )
    out = capsys.readouterr().out
    assert "dropped" in out
    assert "recursive" in out
    assert "playerRewardChoiceIndex" in out


def test_run_consumable_use_loops_until_no_stock(mock_client, mock_sleep):
    """マトリョーシカ: 消費後に再出現した対象はラウンドを繰り返して全消費する。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # ラウンド1: 215 x48
        _lootbox_success(48),  # 48 消費
        _res_from(dummy.INVENTORY_GET_LEFTOVER),  # ラウンド2: 215 x10 再出現
        _lootbox_success(10),  # 10 消費
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(client, lib_ids=[215])

    assert len(results) == 1
    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 58
    assert results[0].stock == 10  # 直近ラウンドの在庫を反映
    assert mock_call.call_count == 5


def test_run_consumable_use_chunks_at_max_amount(mock_client, mock_sleep):
    """上限 1000 のアイテムは 1000 ずつ分割し、端数を最後に消費する。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CHUNKED),  # 169: 2500
        _lootbox_success(1000),
        _lootbox_success(1000),
        _lootbox_success(500),
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(client, lib_ids=[169])

    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 2500
    use_calls = [
        c.args[0]
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseLootBox"
    ]
    assert [c["calls"][0]["args"]["amount"] for c in use_calls] == [1000, 1000, 500]
    # 169 は recursive=False のため recursive は付かない
    assert all("recursive" not in c["calls"][0]["args"] for c in use_calls)
    assert mock_call.call_count == 5


def test_run_consumable_use_chunk_partial_failure(mock_client, mock_sleep):
    """チャンク途中で失敗した場合、成功分のみ集計し ERROR で報告する。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CHUNKED),  # 169: 2500
        _lootbox_success(1000),  # 1000 成功
        _res_from(
            dummy.CONSUMABLE_USE_LIMIT_REACHED, is_success=False, error_name="limitReached"
        ),  # 2 個目失敗
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 失敗済みのため再試行なし
    ]

    results = run_consumable_use(client, lib_ids=[169])

    assert results[0].status == ResponseStatus.ERROR
    assert results[0].error_name == "limitReached"
    assert results[0].consumed == 1000  # 成功したチャンクのみ集計
    assert results[0].stock == 2500
    use_calls = [
        c
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseLootBox"
    ]
    assert len(use_calls) == 2  # 3 個目のチャンクは試行しない
    assert mock_call.call_count == 4


def test_run_consumable_use_matryoshka_and_chunking(mock_client, mock_sleep):
    """マトリョーシカ × 1000 分割: 開封で出現した分割対象も分割消費する。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_NESTED_BOX),  # ラウンド1: 271 x1200
        _lootbox_success(1000),  # 1000
        _lootbox_success(200),  # 200
        _res_from(dummy.INVENTORY_GET_CHUNKED),  # ラウンド2: 169 x2500 出現
        _lootbox_success(1000),  # 1000
        _lootbox_success(1000),  # 1000
        _lootbox_success(500),  # 500
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(client, lib_ids=[271, 169])

    by_id = {r.lib_id: r for r in results}
    assert by_id[271].status == ResponseStatus.SUCCESS
    assert by_id[271].consumed == 1200
    assert by_id[169].status == ResponseStatus.SUCCESS
    assert by_id[169].consumed == 2500
    amounts = [
        c.args[0]["calls"][0]["args"]["amount"]
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseLootBox"
    ]
    assert amounts == [1000, 200, 1000, 1000, 500]
    assert mock_call.call_count == 8


def test_run_consumable_use_stops_at_max_rounds(mock_client, mock_sleep, capsys):
    """在庫が減らない場合（マトリョーシカ無限）は max_rounds で停止し警告する。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # ラウンド1
        _lootbox_success(48),
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # ラウンド2（再出現）
        _lootbox_success(48),
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # ラウンド3: 上限超過
    ]

    results = run_consumable_use(client, lib_ids=[215], max_rounds=2)

    assert results[0].status == ResponseStatus.ERROR  # 未完了のため失敗扱い
    assert results[0].error_name == "maxRoundsReached"
    assert results[0].consumed == 96
    assert results[0].stock == 48  # 打ち切り時点の残り在庫
    assert mock_call.call_count == 5  # inventory(3) + use(2)
    assert "Stopped after 2 rounds" in capsys.readouterr().out


def test_run_consumable_use_max_rounds_zero(mock_client, mock_sleep, capsys):
    """max_rounds=0 は消費せず即停止する（安全弁の境界）。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_CONSUMABLE)

    results = run_consumable_use(client, lib_ids=[215], max_rounds=0)

    assert results[0].status == ResponseStatus.ERROR  # 何も消費していない
    assert results[0].error_name == "maxRoundsReached"
    assert results[0].consumed == 0
    assert results[0].stock == 48  # 実在庫を報告
    assert mock_call.call_count == 1  # inventoryGet のみ
    assert "Stopped after 0 rounds" in capsys.readouterr().out


def test_run_consumable_use_retries_unexpected(mock_client, mock_sleep):
    """UNEXPECTED（一時障害）は次ラウンドで再試行し、最終的に全量消費する。"""
    client, mock_call = mock_client

    def _inv(consumable: dict) -> MagicMock:
        res = MagicMock()
        res.is_success = True
        res.error_name = None
        res.detail = {"response": {"consumable": consumable}}
        return res

    mock_call.side_effect = [
        _inv({"169": 2500}),  # ラウンド1
        _lootbox_success(1000),  # 1000 成功
        Exception("boom"),  # 2 個目 UNEXPECTED
        _inv({"169": 1500}),  # ラウンド2: 残り 1500
        _lootbox_success(1000),
        _lootbox_success(500),
        _inv({}),  # 検証: 残りなし
    ]

    results = run_consumable_use(client, lib_ids=[169])

    assert results[0].status == ResponseStatus.SUCCESS  # 再試行成功で最終 SUCCESS
    assert results[0].consumed == 2500
    amounts = [
        c.args[0]["calls"][0]["args"]["amount"]
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseLootBox"
    ]
    # ラウンド1: 1000 成功 + 1000 失敗 → ラウンド2: 残り 1500 を [1000, 500] で再試行
    assert amounts == [1000, 1000, 1000, 500]
    assert mock_call.call_count == 7


def test_run_consumable_use_does_not_retry_hard_failures(mock_client, mock_sleep):
    """失敗（limitReached）したアイテムは後続ラウンドで再試行しない。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # ラウンド1: 在庫取得
        _res_from(dummy.CONSUMABLE_USE_LIMIT_REACHED, is_success=False, error_name="limitReached"),
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # 検証: 在庫は残っているが失敗済み
    ]

    results = run_consumable_use(client, lib_ids=[215])

    assert results[0].status == ResponseStatus.ERROR
    assert results[0].error_name == "limitReached"
    use_calls = [
        c
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseLootBox"
    ]
    assert len(use_calls) == 1
    assert mock_call.call_count == 3


def test_run_consumable_use_skips_no_stock(mock_client, mock_sleep):
    """在庫が無いアカウントではスキップされ、消費 API は呼ばれない。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_NO_STOCK)

    results = run_consumable_use(client, lib_ids=[215])

    assert len(results) == 1
    assert results[0].status == ResponseStatus.SKIPPED
    assert mock_call.call_count == 1  # inventoryGet のみ


def test_run_consumable_use_not_registered_needs_method(mock_client, mock_sleep):
    """レジストリ未登録の libId は --method がないとエラー、あれば実行できる。"""
    client, mock_call = mock_client

    # 未登録（201）を在庫ありで明示指定
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_UNREGISTERED),  # ラウンド1: 在庫取得
        _res_from(dummy.INVENTORY_GET_UNREGISTERED),  # 検証: 失敗済みのため再試行なし
    ]
    results = run_consumable_use(client, lib_ids=[201])
    assert results[0].status == ResponseStatus.ERROR
    assert results[0].error_name == "unknownMethod"
    assert mock_call.call_count == 2

    # --method 上書きなら在庫全量（360）を消費できる
    mock_call.reset_mock()
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_UNREGISTERED),
        _lootbox_success(360),
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]
    results = run_consumable_use(client, lib_ids=[201], method_override="consumableUseLootBox")
    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 360
    # 未登録 lib + --method では recursive は付与されない（登録後に有効化）
    use_calls = [
        c
        for c in mock_call.call_args_list
        if c.args[0]["calls"][0]["name"] == "consumableUseLootBox"
    ]
    assert len(use_calls) == 1
    assert use_calls[0].args[0]["calls"][0]["args"] == {"libId": 201, "amount": 360}


def test_run_consumable_use_dedupes_lib_ids(mock_client, mock_sleep):
    """同一 libId の重複指定は 1 回の消費にまとめる。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),
        _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS),
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 残りなし
    ]

    results = run_consumable_use(client, lib_ids=[215, 215, 215])

    assert len(results) == 1
    assert results[0].status == ResponseStatus.SUCCESS
    assert mock_call.call_count == 3  # inventory(1) + use(1) + 検証 inventory(1)


def test_run_consumable_use_propagates_inventory_error(mock_client, mock_sleep):
    """在庫取得の失敗（InventoryReadError）は伝播し、消費は実行されない。"""
    from hw_genie.core.inventory import InventoryReadError

    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = False
    res.error_name = "someError"
    res.status = ResponseStatus.ERROR
    mock_call.return_value = res

    with pytest.raises(InventoryReadError):
        run_consumable_use(client)
    assert mock_call.call_count == 1  # inventoryGet のみ


def test_run_consumable_use_dry_run(mock_client, mock_sleep):
    """dry-run は消費 API を呼ばずプランのみ（status=SUCCESS, consumed=0）。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_CONSUMABLE)

    results = run_consumable_use(client, lib_ids=[215], dry_run=True)

    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 0
    assert results[0].stock == 48
    assert mock_call.call_count == 1  # inventoryGet のみ


def test_run_consumable_use_reports_api_error(mock_client, mock_sleep):
    """消費 API の失敗（limitReached）は ERROR で報告され、後続は継続する。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),
        _res_from(dummy.CONSUMABLE_USE_LIMIT_REACHED, is_success=False, error_name="limitReached"),
        _res_from(dummy.INVENTORY_GET_NO_STOCK),  # 検証: 失敗済みのため再試行なし
    ]

    results = run_consumable_use(client, lib_ids=[215])

    assert results[0].status == ResponseStatus.ERROR
    assert results[0].error_name == "limitReached"


# --- run_inventory（表示） ---


def test_run_inventory_displays_consumable(mock_client, capsys):
    """デフォルトでは consumable のみ表示する。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),
    ]

    raw = run_inventory(client)
    assert raw["consumable"]["215"] == 48
    out = capsys.readouterr().out
    assert "Equipment Fragment Chest (libId 215)" in out

    run_inventory(client, show_all=True)
    out_all = capsys.readouterr().out
    assert "=== gear (1 kind(s)) ===" in out_all


def test_run_inventory_empty(mock_client, capsys):
    """空の在庫レスポンスは 'Inventory is empty.' を表示する。"""
    client, mock_call = mock_client
    res = MagicMock()
    res.is_success = True
    res.detail = {"response": {}}
    mock_call.return_value = res

    raw = run_inventory(client)

    assert raw == {}
    assert "Inventory is empty." in capsys.readouterr().out


def test_run_inventory_min_amount_filter(mock_client, capsys):
    """--min 未満の在庫は表示しない。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_CONSUMABLE)

    run_inventory(client, min_amount=1000)

    out = capsys.readouterr().out
    assert "libId 17" not in out  # 327 < 1000
    assert "libId 215" not in out  # 48 < 1000
    assert "libId 20" in out  # 1,142,335 >= 1000


# --- multi（consumable_routine / summarize_consumable） ---


def test_summarize_consumable_counts_and_fails(mock_client):
    """サマリは成功/スキップ/失敗を数え、失敗アカウント数を返す。"""
    results = {
        "Alice": (
            [
                ConsumableUseResult(lib_id=215, status=ResponseStatus.SUCCESS, consumed=48),
                ConsumableUseResult(lib_id=17, status=ResponseStatus.SKIPPED),
            ],
            None,
        ),
        "Bob": (
            [ConsumableUseResult(lib_id=215, status=ResponseStatus.ERROR, error_name="limitReached")],
            None,
        ),
        "Carol": (None, RuntimeError("boom")),
    }

    failed = summarize_consumable(results.items())

    assert failed == 2


def test_consumable_routine_wraps_run(mock_client, mock_sleep):
    """routine は account_alias 付きで run_consumable_use を呼ぶ。"""
    routine = consumable_routine()
    client, mock_call = mock_client
    empty = MagicMock()
    empty.is_success = True
    empty.error_name = None
    empty.detail = {"response": {"consumable": {}}}
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # ラウンド1: 215 x48（17 は targets 外）
        _lootbox_success(48),  # 215 消費
        empty,  # 検証: 残りなし
    ]

    result = routine(client, "Bob")

    assert isinstance(result, list)
    assert len(result) == len(CONSUMABLE_USE_TARGETS)
    by_id = {r.lib_id: r for r in result}
    assert by_id[215].status == ResponseStatus.SUCCESS
    assert sum(r.status == ResponseStatus.SKIPPED for r in result) == len(result) - 1
