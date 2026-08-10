from unittest.mock import MagicMock

from . import dummy_responses as dummy
from hw_genie.commands.consumables import run_consumable_use, run_inventory
from hw_genie.core.client import ResponseStatus
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


# --- parse_use_rewards / use_consumable ---


def test_parse_use_rewards_sums_category_quantities():
    """報酬をカテゴリ別合計に集計する（実測レスポンス準拠）。"""
    detail = dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS["results"][0]["result"]

    rewards = parse_use_rewards(detail, 48)

    assert rewards == {"fragmentScroll": 35, "fragmentGear": 30}
    assert parse_use_rewards(detail, 99) == {}  # 消費数と不一致なら空


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


# --- run_consumable_use（CLI コマンド本体） ---


def test_run_consumable_use_consumes_registered_targets(mock_client, mock_sleep):
    """登録済み対象（215）を在庫全量（48）消費し、在庫 0 のアイテムはスキップする。"""
    client, mock_call = mock_client
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),  # 在庫取得
        _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS),  # 消費
    ]

    results = run_consumable_use(client)

    assert CONSUMABLE_USE_TARGETS == [215]
    assert len(results) == 1
    result = results[0]
    assert result.lib_id == 215
    assert result.status == ResponseStatus.SUCCESS
    assert result.consumed == 48
    assert result.name == CONSUMABLE_REGISTRY[215].name
    # inventory(1) + use(1)
    assert mock_call.call_count == 2


def test_run_consumable_use_skips_no_stock(mock_client, mock_sleep):
    """在庫が無いアカウントではスキップされ、消費 API は呼ばれない。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_NO_STOCK)

    results = run_consumable_use(client)

    assert len(results) == 1
    assert results[0].status == ResponseStatus.SKIPPED
    assert mock_call.call_count == 1  # inventoryGet のみ


def test_run_consumable_use_not_registered_needs_method(mock_client, mock_sleep):
    """レジストリ未登録の libId は --method がないとエラー、あれば実行できる。"""
    client, mock_call = mock_client

    # 未登録（201）を在庫ありで明示指定
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_UNREGISTERED)
    results = run_consumable_use(client, lib_ids=[201])
    assert results[0].status == ResponseStatus.ERROR
    assert results[0].error_name == "unknownMethod"
    assert mock_call.call_count == 1

    # --method 上書きなら在庫全量（360）を消費できる
    mock_call.reset_mock()
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_UNREGISTERED),
        _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS),
    ]
    results = run_consumable_use(client, lib_ids=[201], method_override="consumableUseLootBox")
    assert results[0].status == ResponseStatus.SUCCESS
    assert results[0].consumed == 360


def test_run_consumable_use_dry_run(mock_client, mock_sleep):
    """dry-run は消費 API を呼ばずプランのみ（status=SUCCESS, consumed=0）。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_CONSUMABLE)

    results = run_consumable_use(client, dry_run=True)

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
    ]

    results = run_consumable_use(client)

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


def test_run_inventory_min_amount_filter(mock_client, capsys):
    """--min 未満の在庫は表示しない。"""
    client, mock_call = mock_client
    mock_call.return_value = _res_from(dummy.INVENTORY_GET_CONSUMABLE)

    run_inventory(client, min_amount=1000)

    out = capsys.readouterr().out
    assert "libId 17" not in out  # 327 < 1000
    assert "libId 215" not in out  # 48 < 1000
    assert "libId 20" in out  # 1,142,335 >= 1000


# --- multi（consumbale_routine / summarize_consumable） ---


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
    mock_call.side_effect = [
        _res_from(dummy.INVENTORY_GET_CONSUMABLE),
        _res_from(dummy.CONSUMABLE_USE_LOOT_BOX_SUCCESS),
    ]

    result = routine(client, "The Best")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].status == ResponseStatus.SUCCESS