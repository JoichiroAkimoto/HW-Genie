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

消費は以下のループで実行する（マトリョーシカ系アイテムの再帰開封と、
サーバー側の消費量キャップによる取り残しに対応）。

1. ``inventoryGet`` で在庫を取得し、在庫 > 0 の対象を全消費する
   （``max_amount`` 上限のアイテムは 1000 ずつ分割リクエスト）。
2. 再度 ``inventoryGet`` を実行して残りが無いことを確認する。
3. 消費後に再出現した対象（マトリョーシカ）や取り残しがあれば繰り返す。
   在庫が減らない場合は ``MAX_USE_ROUNDS``（既定 30 ラウンド）で打ち切り、
   残り在庫を警告する。

認証エラー（HWAuthError）は握りつぶさず再送出する（上位で共通処理）。
"""

import logging
from typing import Any

from hw_genie.core.client import Emojis, HWClient, ResponseStatus, _safe_int
from hw_genie.core.consumables import (
    CONSUMABLE_USE_TARGETS,
    display_name,
    max_amount,
    player_reward_choice_index,
    resolve_use_method,
)
from hw_genie.core.inventory import (
    ConsumableUseResult,
    fetch_inventory,
    use_consumable,
)

logger = logging.getLogger(__name__)

#: 消費ループの最大ラウンド数（マトリョーシカ系の無限再帰に対する安全弁）。
MAX_USE_ROUNDS = 30


def _item_label(lib_id: int) -> str:
    """表示名があれば ``名前 (libId N)``、なければ ``libId N`` を返す。"""
    name = display_name(lib_id)
    return f"{name} (libId {lib_id})" if name else f"libId {lib_id}"


def _reward_text(rewards: dict[str, int]) -> str:
    if not rewards:
        return "none"
    return ", ".join(f"{category} x{total}" for category, total in sorted(rewards.items()))


def _chunk_sizes(stock: int, max_amount: int) -> list[int]:
    """1 リクエストあたりの消費量上限に応じて在庫を分割する（上限なしは ``[stock]``）。"""
    if max_amount <= 0 or stock <= max_amount:
        return [stock]
    chunks: list[int] = []
    remaining = stock
    while remaining > 0:
        take = min(max_amount, remaining)
        chunks.append(take)
        remaining -= take
    return chunks


def _record_result(results: dict[int, ConsumableUseResult], new: ConsumableUseResult) -> None:
    """ラウンドをまたいだ同一 libId の結果を 1 件に集約する。

    ``consumed`` / ``rewards`` は加算し、``stock`` / ``status`` は
    直近ラウンドの値を採用する（UNEXPECTED 後に再試行で成功した場合、
    最終結果は SUCCESS になる）。
    """
    base = results.get(new.lib_id)
    if base is None:
        results[new.lib_id] = new
        return
    base.consumed += new.consumed
    for category, qty in new.rewards.items():
        base.rewards[category] = base.rewards.get(category, 0) + qty
    base.stock = new.stock
    base.status = new.status
    base.error_name = new.error_name


def _plan_consumable_use(
    client: HWClient,
    targets: list[int],
    method_override: str | None,
) -> list[ConsumableUseResult]:
    """dry-run: 在庫を 1 回取得し、消費プラン（分割リクエスト数込み）を表示する。"""
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

        chunk_size = max_amount(lib_id)
        chunks = _chunk_sizes(stock, chunk_size)
        if len(chunks) > 1:
            print(
                f"  [{index}/{len(targets)}] {label}: would consume {stock} via {method} "
                f"({len(chunks)} requests of up to {chunk_size}).",
                flush=True,
            )
        else:
            print(
                f"  [{index}/{len(targets)}] {label}: would consume {stock} via {method}.",
                flush=True,
            )
        results.append(
            ConsumableUseResult(lib_id=lib_id, name=name, stock=stock, status=ResponseStatus.SUCCESS)
        )
    return results


def run_consumable_use(
    client: HWClient,
    lib_ids: list[int] | None = None,
    method_override: str | None = None,
    dry_run: bool = False,
    account_alias: str | None = None,
    max_rounds: int = MAX_USE_ROUNDS,
) -> list[ConsumableUseResult]:
    """登録済み consumable を在庫が尽きるまで全消費する（アカウント 1 件分）。

    1 ラウンド = ``inventoryGet`` → 在庫 > 0 の対象を全消費 → 残り確認。
    マトリョーシカ系アイテム（開封で同種が再出現するもの）はラウンドを
    繰り返すことで再帰的に開封する。残りが無くなるか ``max_rounds`` に
    達するまで続行する。恒久的な失敗（``limitReached`` 等の ERROR）は
    以降のラウンドで再試行しないが、一時的な失敗（UNEXPECTED）は
    次ラウンドで再試行する。

    Args:
        client: 認証済み HWClient。
        lib_ids: 対象 libId リスト。``None`` の場合は
            ``CONSUMABLE_USE_TARGETS`` を使用する。
        method_override: 全アイテムに適用する RPC メソッドの上書き。
        dry_run: 在庫取得のみ行い、消費プランを表示する。
        account_alias: 複数アカウント実行時の出力プレフィックス。
        max_rounds: 消費ループの最大ラウンド数。

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
    if dry_run:
        return _plan_consumable_use(client, targets, method_override)

    results: dict[int, ConsumableUseResult] = {}
    failed: set[int] = set()
    round_no = 0
    while True:
        round_no += 1
        snapshot = fetch_inventory(client)
        consumables = snapshot.consumable
        pending: list[tuple[int, int]] = []
        for lib_id in targets:
            if lib_id in failed:
                continue
            stock = _safe_int(consumables.get(lib_id, 0))
            if stock > 0:
                pending.append((lib_id, stock))
        if not pending:
            if failed:
                print(
                    f"  {Emojis.INFO}{prefix}Round {round_no}: no target stock remaining "
                    "(failed items skipped).",
                    flush=True,
                )
            else:
                print(
                    f"  {Emojis.SUCCESS}{prefix}Inventory verified: no target consumables remaining.",
                    flush=True,
                )
            break
        if round_no > max_rounds:
            leftovers = ", ".join(f"{_item_label(lib_id)} x{stock}" for lib_id, stock in pending)
            print(
                f"  {Emojis.WARNING}{prefix}Stopped after {max_rounds} rounds (safety cap); "
                f"still remaining: {leftovers}",
                flush=True,
            )
            for lib_id, stock in pending:
                _record_result(
                    results,
                    ConsumableUseResult(
                        lib_id=lib_id,
                        name=display_name(lib_id),
                        stock=stock,
                        status=ResponseStatus.ERROR,
                        error_name="maxRoundsReached",
                    ),
                )
            break
        if round_no > 1:
            print(
                f"  {Emojis.STEP}{prefix}Round {round_no}: leftover target stock found, "
                "continuing...",
                flush=True,
            )

        for index, (lib_id, stock) in enumerate(pending, start=1):
            label = _item_label(lib_id)
            name = display_name(lib_id)

            method = resolve_use_method(lib_id, method_override)
            if not method:
                print(
                    f"  [{index}/{len(pending)}] {Emojis.ERROR}{label}: no registered use method. "
                    "Specify --method to override (or register in core/consumables.py).",
                    flush=True,
                )
                failed.add(lib_id)
                _record_result(
                    results,
                    ConsumableUseResult(
                        lib_id=lib_id,
                        name=name,
                        stock=stock,
                        status=ResponseStatus.ERROR,
                        error_name="unknownMethod",
                    ),
                )
                continue

            chunks = _chunk_sizes(stock, max_amount(lib_id))
            print(
                f"  [{index}/{len(pending)}] {label}: consuming {stock} via {method} "
                f"({len(chunks)} request(s))...",
                flush=True,
            )
            item = ConsumableUseResult(
                lib_id=lib_id, name=name, stock=stock, status=ResponseStatus.SUCCESS
            )
            for amount in chunks:
                result = use_consumable(
                    client,
                    lib_id,
                    amount,
                    method,
                    player_reward_choice_index=player_reward_choice_index(lib_id),
                )
                client.sleep()
                if result.status != ResponseStatus.SUCCESS:
                    item.status = result.status
                    item.error_name = result.error_name
                    print(
                        f"    {Emojis.ERROR}Failed ({result.error_name or result.status.value}) "
                        f"after consuming {item.consumed}/{stock}.",
                        flush=True,
                    )
                    logger.warning(
                        "Consumable use failed for libId %d (%s)",
                        lib_id,
                        result.error_name or result.status.value,
                    )
                    if result.status == ResponseStatus.ERROR:
                        # 恒久的な失敗（limitReached 等）は次ラウンドでも成功しないため再試行しない。
                        # UNEXPECTED（一時的な通信障害等）は次ラウンドで再試行する。
                        failed.add(lib_id)
                    break
                item.consumed += result.consumed
                for category, qty in result.rewards.items():
                    item.rewards[category] = item.rewards.get(category, 0) + qty
            if item.status == ResponseStatus.SUCCESS:
                print(
                    f"    {Emojis.SUCCESS}Consumed {item.consumed}. "
                    f"Rewards: {_reward_text(item.rewards)}",
                    flush=True,
                )
            _record_result(results, item)

    return [
        results[lib_id]
        if lib_id in results
        else ConsumableUseResult(
            lib_id=lib_id, name=display_name(lib_id), stock=0, status=ResponseStatus.SKIPPED
        )
        for lib_id in targets
    ]


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
