"""consumable 在庫の表示（inventory）と一括消費（consumable run）。

- ``hw-genie inventory``: ``inventoryGet`` で在庫を取得し、consumable を
  `表示名 (libId): 個数` 形式で表示する（``--all`` で全カテゴリ）。
- ``hw-genie consumable run``: ``CONSUMABLE_USE_TARGETS`` に登録された
  libId を順に全消費する（位置引数 ``lib_ids`` で対象指定、``--method`` で RPC
  メソッドを上書き、``--dry-run`` でプラン表示のみ）。

消費量は引数ではなく、実行直前に ``inventoryGet`` で取得した実在庫数
（サーバー不整合を防ぐ shopBuy と同じ思想）をそのまま使う。在庫が無い
アイテムはスキップし、メソッドが解決できないアイテムはエラーとして
報告する（レジストリ未登録時は ``--method`` で補える）。

認証エラー（HWAuthError）は握りつぶさず再送出する（上位で共通処理）。
"""

import logging
from typing import Any

from hw_genie.core.client import Emojis, HWClient, ResponseStatus, _safe_int
from hw_genie.core.consumables import (
    CONSUMABLE_USE_TARGETS,
    display_name,
    resolve_use_method,
)
from hw_genie.core.inventory import (
    ConsumableUseResult,
    fetch_inventory,
    use_consumable,
)

logger = logging.getLogger(__name__)


def _item_label(lib_id: int) -> str:
    """表示名があれば ``名前 (libId N)``、なければ ``libId N`` を返す。"""
    name = display_name(lib_id)
    return f"{name} (libId {lib_id})" if name else f"libId {lib_id}"


def _reward_text(rewards: dict[str, int]) -> str:
    if not rewards:
        return "none"
    return ", ".join(f"{category} x{total}" for category, total in sorted(rewards.items()))


def run_consumable_use(
    client: HWClient,
    lib_ids: list[int] | None = None,
    method_override: str | None = None,
    dry_run: bool = False,
    account_alias: str | None = None,
) -> list[ConsumableUseResult]:
    """登録済み consumable を順に全消費する（アカウント 1 件分）。

    Args:
        client: 認証済み HWClient。
        lib_ids: 対象 libId リスト。``None`` の場合は
            ``CONSUMABLE_USE_TARGETS`` を使用する。
        method_override: 全アイテムに適用する RPC メソッドの上書き。
        dry_run: 在庫取得のみ行い、消費プランを表示する。
        account_alias: 複数アカウント実行時の出力プレフィックス。

    Raises:
        HWAuthError: 認証エラー（握りつぶさず再送出）
        InventoryReadError: 在庫取得失敗（消費可否の判定不能として伝播）
    """
    prefix = f"[{account_alias}] " if account_alias else ""
    targets = list(dict.fromkeys(lib_ids)) if lib_ids else list(CONSUMABLE_USE_TARGETS)
    if not targets:
        print(
            f"{Emojis.WARNING}{prefix}No consumable use targets "
            "(CONSUMABLE_USE_TARGETS is empty; pass <libId> positional args or register in core/consumables.py).",
            flush=True,
        )
        return []

    verb = "Planning" if dry_run else "Executing"
    print(
        f"\n{Emojis.STEP}{prefix}{verb} consumable use for {len(targets)} item(s)...",
        flush=True,
    )
    snapshot = fetch_inventory(client)
    consumables = snapshot.consumable

    results: list[ConsumableUseResult] = []
    for index, lib_id in enumerate(targets, start=1):
        label = _item_label(lib_id)
        name = display_name(lib_id)

        method = resolve_use_method(lib_id, method_override)
        if not method:
            print(
                f"  [{index}/{len(targets)}] {Emojis.ERROR}{label}: no registered use method. "
                "Specify --method to override (or register in core/consumables.py).",
                flush=True,
            )
            results.append(
                ConsumableUseResult(
                    lib_id=lib_id, name=name, status=ResponseStatus.ERROR, error_name="unknownMethod"
                )
            )
            continue

        stock = _safe_int(consumables.get(lib_id, 0))
        if stock <= 0:
            print(f"  [{index}/{len(targets)}] {Emojis.INFO}{label}: no stock, skipped.", flush=True)
            results.append(
                ConsumableUseResult(lib_id=lib_id, name=name, stock=0, status=ResponseStatus.SKIPPED)
            )
            continue

        if dry_run:
            print(
                f"  [{index}/{len(targets)}] {label}: would consume {stock} via {method}.",
                flush=True,
            )
            results.append(
                ConsumableUseResult(
                    lib_id=lib_id, name=name, stock=stock, status=ResponseStatus.SUCCESS
                )
            )
            continue

        print(
            f"  [{index}/{len(targets)}] {label}: consuming all {stock} via {method}...",
            flush=True,
        )
        result = use_consumable(client, lib_id, stock, method)
        result.name = name
        results.append(result)
        if result.status == ResponseStatus.SUCCESS:
            print(
                f"    {Emojis.SUCCESS}Consumed {result.consumed}. Rewards: {_reward_text(result.rewards)}",
                flush=True,
            )
        else:
            print(
                f"    {Emojis.ERROR}Failed ({result.error_name or result.status.value}).",
                flush=True,
            )
            logger.warning(
                "Consumable use failed for libId %d (%s)",
                lib_id,
                result.error_name or result.status.value,
            )
        client.sleep()

    return results


def run_inventory(client: HWClient, show_all: bool = False, min_amount: int = 0) -> dict[str, Any]:
    """inventoryGet の結果を表形式で表示し、raw を返す。"""
    snapshot = fetch_inventory(client)
    categories = snapshot.categories
    if not categories:
        print("Inventory is empty.")
        return snapshot.raw

    target_categories = sorted(categories) if show_all else ["consumable"]
    for category in target_categories:
        entries = categories.get(category)
        if not entries:
            continue
        print(f"\n=== {category} ({len(entries)} kind(s)) ===")
        rows = []
        for lib_id, qty in sorted(entries.items(), key=lambda item: item[0] or 0):
            count = _safe_int(qty)
            if min_amount and count < min_amount:
                continue
            label = _item_label(lib_id)
            rows.append((label, count))
        if not rows:
            print("  (none)")
            continue
        width = max(len(label) for label, _ in rows)
        for label, count in rows:
            print(f"  {label.ljust(width)} : {count:,}")
    return snapshot.raw
