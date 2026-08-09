"""Asgard（ギルドレイド）ショップの自動購入。

``clanRaid_getInfo`` の ``response.shop``（slotId → 商品）から Valor Emblem
（コイン ID 30）支払いの商品を読み、Osh 週の Realm Traveler ショップに対する
固定優先度に従って ``clanRaid_shopBuy`` で購入する。

- **Osh 判定**: Osh のラインナップは固定（slot 1〜5 はゴールドバフ、
  slot 6〜21 が Valor Emblem 商品で buffId 61〜81）。``shop`` の
  buffId 集合がシグネチャ（61〜81）と一致した場合のみ Osh 週と判定し、
  Maestro 週などの不一致時はスキップする（Maestro は未対応）。
- **優先度**: 優先度 1 → 2 → 3 の slot を順に購入し、残りの未購入商品は
  価格昇順（同額は slot 昇順）で購入する。購入済み（boughtCount >= buyLimit）
  とゴールドバフは対象外。
- **残高**: ``response.coins`` の Valor Emblem 残高を追跡し、残高不足の
  商品は購入しない。さらに実際の購入失敗（NotEnough）が起きた場合も
  以降の購入をスキップする（両方併用の安全策）。
- **dry_run**: 実行計画（購入順・合計コスト）の表示のみ行い、購入はしない。

認証エラー（HWAuthError）は握りつぶさず再送出する（上位で共通処理）。
"""

import logging
from dataclasses import dataclass
from typing import Any

from hw_genie.core.client import ApiAction, Emojis, ErrorName, HWAuthError, HWClient, ResponseStatus

logger = logging.getLogger(__name__)

# Osh 週（Realm Traveler）の固定優先度。キーが優先レベル（小さいほど先）、
# 値が購入対象 slot のリスト（リスト内の順序も保持される）。
OSH_PRIORITY: dict[int, list[int]] = {
    1: [8, 17, 20, 12, 13, 19],
    2: [6, 10, 21, 18],
    3: [15, 16, 11],
}

# Osh のラインナップシグネチャ（slot 1〜5 のゴールドバフも含む全 buffId）。
# 判定は部分集合（非空）で行う: 買い切った slot が省略されたり、将来
# ラインナップが追加された場合でも Osh 週として扱えるようにする。
OSH_BUFF_IDS: frozenset[int] = frozenset(range(61, 82))

# Valor Emblem のコイン ID（cost["coin"] のキー）。
VALOR_COIN_ID = 30


class AsgardShopReadError(Exception):
    """clanRaid_getInfo の取得・パースが失敗したことを表す（認証エラーは HWAuthError のまま）。"""


@dataclass
class AsgardItem:
    """購入候補の 1 slot 分の情報。"""

    slot_id: int
    buff_id: int
    buff_value: int
    price: int

    @property
    def label(self) -> str:
        return f"[Realm Traveler] Slot:{self.slot_id} -> buff {self.buff_id} (x{self.buff_value}, {self.price} Valor Emblems)"


@dataclass
class AsgardResult:
    """購入（またはスキップ）1 件の実行結果。"""

    action: str
    status: ResponseStatus
    error: str | None = None


@dataclass
class AsgardRunResult:
    """1 アカウント分の実行結果サマリ。"""

    coins: int
    spent: int
    remaining: int
    bought: int
    skipped: bool
    items: list[AsgardResult]
    # 在庫取得失敗など、購入処理自体を実行できなかった場合の理由（成功時は None）。
    error: str | None = None

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status == ResponseStatus.ERROR)


def fetch_clan_raid_shop(client: HWClient) -> tuple[dict[str, Any], int]:
    """clanRaid_getInfo を呼び、``(shop, coins)`` を返す。

    Raises:
        HWAuthError: 認証エラー（握りつぶさず再送出）
        AsgardShopReadError: 通信・API エラー、または予期しないレスポンス形式
    """
    try:
        res = client.call(
            {"calls": [{"name": ApiAction.CLAN_RAID_GET_INFO, "args": {}, "ident": "body"}]}
        )
    except HWAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AsgardShopReadError(f"clanRaid_getInfo failed: {exc}") from exc
    if not res.is_success:
        raise AsgardShopReadError(
            f"clanRaid_getInfo failed ({res.error_name or res.status.value})"
        )
    detail = res.detail if isinstance(res.detail, dict) else {}
    response = detail.get("response")
    if not isinstance(response, dict):
        raise AsgardShopReadError("clanRaid_getInfo returned unexpected response (missing 'response' dict)")
    shop = response.get("shop")
    if not isinstance(shop, dict):
        raise AsgardShopReadError("clanRaid_getInfo returned unexpected response (missing 'shop' dict)")
    return shop, _safe_int(response.get("coins"))


def _safe_int(value: Any, default: int = 0) -> int:
    """int 安全変換（失敗時は default）。

    client.py の同名関数と異なり default を指定でき、bool（True/False）も
    数値（1/0）に変換する（API レスポンスの型の揺れはこちらで吸収する）。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _slot_buff_ids(shop: dict[str, Any]) -> set[int]:
    """shop 内の全 slot の buffId 集合（Osh 判定用）。"""
    buff_ids: set[int] = set()
    for item in shop.values():
        if isinstance(item, dict) and item.get("buffId") is not None:
            buff_ids.add(_safe_int(item["buffId"]))
    return buff_ids


def is_osh_shop(shop: dict[str, Any]) -> bool:
    """Osh 週のショップかどうかを buffId シグネチャで判定する。

    部分集合（非空）で判定するため、買い切った slot が shop から省略された
    場合やラインナップが将来追加された場合でも Osh 週として扱える。
    Maestro 週など Osh と異なるラインナップの場合は False（現状スキップ対象）。
    """
    buff_ids = _slot_buff_ids(shop)
    return bool(buff_ids) and buff_ids.issubset(OSH_BUFF_IDS)


def parse_slot(slot_id: Any, item: Any) -> AsgardItem | None:
    """slot を Valor Emblem 商品としてパースする。

    ゴールドバフ（cost に gold のみ）、価格が 0 以下（パース失敗を含む）、
    構造不正の slot は ``None`` を返す（購入候補から除外される）。
    """
    if not isinstance(item, dict):
        return None
    cost = item.get("cost")
    if not isinstance(cost, dict):
        return None
    coins = cost.get("coin")
    if not isinstance(coins, dict) or str(VALOR_COIN_ID) not in coins:
        return None
    price = _safe_int(coins[str(VALOR_COIN_ID)])
    if price <= 0:
        return None
    return AsgardItem(
        slot_id=_safe_int(slot_id),
        buff_id=_safe_int(item.get("buffId")),
        buff_value=_safe_int(item.get("buffValue")),
        price=price,
    )


def is_bought(item: Any) -> bool:
    """slot の購入済み判定（boughtCount >= buyLimit で購入済みとみなす）。"""
    if not isinstance(item, dict):
        return True
    bought_count = _safe_int(item.get("boughtCount"))
    buy_limit = _safe_int(item.get("buyLimit"), 1)
    return bought_count >= buy_limit


def build_buy_queue(shop: dict[str, Any]) -> list[AsgardItem]:
    """未購入の Valor Emblem 商品を優先度順に並べた購入キューを構築する。

    優先度 1〜3 に含まれる slot はその優先度・リスト順で先頭に並び、
    それ以外の商品は価格昇順（同額は slot 昇順）で末尾に続く。
    購入済み・ゴールドバフ・構造不正の slot は除外される。
    """
    shop_items = {
        _safe_int(slot_id): parse_slot(slot_id, item)
        for slot_id, item in shop.items()
        if not is_bought(item)
    }
    candidates = {k: v for k, v in shop_items.items() if v is not None}

    queue: list[AsgardItem] = []
    queued_slots: set[int] = set()
    for level in sorted(OSH_PRIORITY):
        for slot_id in OSH_PRIORITY[level]:
            item = candidates.get(slot_id)
            if item is not None:
                queue.append(item)
                queued_slots.add(slot_id)

    remaining = sorted(
        (item for slot_id, item in candidates.items() if slot_id not in queued_slots),
        key=lambda item: (item.price, item.slot_id),
    )
    queue.extend(remaining)
    return queue


def _build_purchase_plan(shop: dict[str, Any]) -> list[AsgardItem]:
    """購入キューを構築する（dry_run も同じ計画を使う）。"""
    return build_buy_queue(shop)


def run_asgard_shop(client: HWClient, dry_run: bool = False, account_alias: str | None = None) -> AsgardRunResult:
    """Asgard ショップの購入を実行（または計画表示）する。

    Args:
        client: HWClient インスタンス。
        dry_run: True の場合は購入せず計画（購入順・合計コスト）のみ表示。
        account_alias: 表示用のアカウント名（None なら省略）。

    Returns:
        AsgardRunResult。Maestro 週等でスキップした場合は ``skipped=True``。
    """
    prefix = f"[{account_alias}] " if account_alias else ""

    print(f"\n{Emojis.STEP}{prefix}--- Fetching current Asgard shop status ---", flush=True)
    try:
        shop, coins = fetch_clan_raid_shop(client)
    except AsgardShopReadError as exc:
        print(f"{Emojis.ERROR}{prefix}Error: Failed to fetch Asgard shop data. {exc}", flush=True)
        return AsgardRunResult(
            coins=0, spent=0, remaining=0, bought=0, skipped=False, items=[], error=str(exc)
        )

    if not is_osh_shop(shop):
        print(
            f"{Emojis.INFO}{prefix}Current Guild Raid shop is not the Osh lineup "
            "(Maestro or unknown) - skipping (Osh only for now).",
            flush=True,
        )
        return AsgardRunResult(coins=coins, spent=0, remaining=coins, bought=0, skipped=True, items=[])

    plan = _build_purchase_plan(shop)
    total_cost = sum(item.price for item in plan)
    print(f"{Emojis.INFO}{prefix}Osh week detected: {len(plan)} item(s) available, budget: {coins} Valor Emblems "
          f"(total cost: {total_cost}).", flush=True)

    if dry_run:
        print(f"\n{Emojis.STEP}{prefix}--- Purchase Plan (dry-run) ---", flush=True)
        results: list[AsgardResult] = []
        budget = coins
        planned_bought = 0
        planned_spent = 0
        for i, item in enumerate(plan):
            affordable = item.price <= budget
            if affordable:
                budget -= item.price
                planned_bought += 1
                planned_spent += item.price
                results.append(AsgardResult(action=item.label, status=ResponseStatus.SUCCESS))
            else:
                results.append(
                    AsgardResult(action=item.label, status=ResponseStatus.SKIPPED, error="Insufficient budget")
                )
            mark = "✅" if affordable else "⏭ "
            print(f"  [{i + 1}/{len(plan)}] {mark} {item.label}", flush=True)
        print(
            f"\n{Emojis.FINISH}{prefix}--- Plan Summary --- "
            f"Planned: {planned_bought} item(s) for {planned_spent} Valor Emblems "
            f"(remaining budget: {budget}).",
            flush=True,
        )
        return AsgardRunResult(
            coins=coins, spent=planned_spent, remaining=budget, bought=planned_bought, skipped=False, items=results
        )

    print(f"\n{Emojis.STEP}{prefix}--- Purchasing Target Items ---", flush=True)
    results = []
    budget = coins
    bought = 0
    spent = 0
    funds_exhausted = False
    for i, item in enumerate(plan):
        if funds_exhausted or item.price > budget:
            print(
                f"[{i + 1}/{len(plan)}] {Emojis.WARNING}Skipping {item.label} (Insufficient Valor Emblems).",
                flush=True,
            )
            results.append(AsgardResult(action=item.label, status=ResponseStatus.SKIPPED, error="Insufficient budget"))
            continue

        print(f"[{i + 1}/{len(plan)}] Purchasing {item.label}...", flush=True)
        buy_call = {
            "name": ApiAction.CLAN_RAID_SHOP_BUY,
            "args": {"slotId": item.slot_id},
            "context": {"actionTs": 0},
            "ident": f"buy_{item.slot_id}",
        }
        res = client.call({"calls": [buy_call]})
        if res.is_success:
            print(f"  Result: {Emojis.SUCCESS}Success", flush=True)
            results.append(AsgardResult(action=item.label, status=ResponseStatus.SUCCESS))
            budget -= item.price
            spent += item.price
            bought += 1
        else:
            error_name = res.error_name or "unknown"
            print(f"  Result: {Emojis.ERROR}Failed ({error_name})", flush=True)
            results.append(AsgardResult(action=item.label, status=ResponseStatus.ERROR, error=error_name))
            if error_name == ErrorName.NOT_ENOUGH:
                print(f"  -> {Emojis.WARNING}Insufficient Valor Emblems. Skipping rest of the shop.", flush=True)
                funds_exhausted = True
        client.sleep()

    print(f"\n{Emojis.FINISH}{prefix}--- Asgard Shop Results Summary ---", flush=True)
    print(f"  {Emojis.SUCCESS}Bought: {bought} item(s), Spent: {spent} / {coins}, Remaining: {budget}", flush=True)
    return AsgardRunResult(coins=coins, spent=spent, remaining=budget, bought=bought, skipped=False, items=results)