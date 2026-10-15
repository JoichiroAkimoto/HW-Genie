import logging
from typing import Any
from dataclasses import dataclass
from enum import Enum
from hw_genie.core.client import (
    ApiAction,
    Emojis,
    ErrorName,
    ResponseStatus,
)
from hw_genie.core.shop import ShopNotFoundError, ShopReadError, fetch_shops, get_shop_slots, is_bought

logger = logging.getLogger(__name__)

class ShopId(Enum):
    TOWN = "1"
    ARENA = "4"
    GRAND_ARENA = "5"
    TOWER = "6"
    SOUL = "8"
    FRIEND = "9"
    PET_SOUL = "17"

@dataclass
class ShopResult:
    action: str
    status: ResponseStatus
    error: str | None = None

@dataclass
class BuyItem:
    shopId: ShopId
    shopName: str
    slot: int
    reward: dict[str, Any]
    cost: dict[str, Any]
    label: str

# 購入対象とするショップ設定
# NOTE: TOWN は含めない。hero_shop_ids はヒーローソウル枠（fragmentHero のある
# スロットを買う枠）であり、Town は固定スロット枠（buy_town_shop_items 制御の
# 別枠）のため。混同防止のため定義を分けている。
TARGET_SHOP_IDS = [ShopId.ARENA, ShopId.GRAND_ARENA, ShopId.TOWER, ShopId.SOUL, ShopId.FRIEND]
SHOP_NAMES = {
    ShopId.TOWN: "Town",
    ShopId.ARENA: "Arena",
    ShopId.GRAND_ARENA: "Grand Arena",
    ShopId.TOWER: "Tower",
    ShopId.SOUL: "Soul",
    ShopId.FRIEND: "Friend",
    ShopId.PET_SOUL: "Pet Soul",
}

# ショップ固有の特定スロットID
PET_POTION_SLOT_ID = "3"
# Town ショップで購入するスロットID（ゴールド品の定番枠）
TOWN_SHOP_SLOT_IDS = frozenset({"2", "4", "5", "6"})

def _shop_sort_key(shop_id: ShopId) -> tuple[int, str]:
    """ショップIDの数値順ソートキー（1,4,5,6,8,9,17）。

    ShopId.value は数字文字列を想定するが、将来の非数値 value が
    追加されてもソートで落ちないよう、数値化失敗時は末尾に回す。
    """
    try:
        return (int(shop_id.value), "")
    except (TypeError, ValueError):
        return (10**9, str(shop_id.value))

def format_reward_desc(reward_dict: dict[str, Any]) -> str:
    if not reward_dict:
        return "Unknown Item"
    descriptions = []
    for item_type, item_data in reward_dict.items():
        if isinstance(item_data, dict):
            for item_id, amount in item_data.items():
                descriptions.append(f"{item_type}:{item_id} (x{amount})")
        else:
            descriptions.append(f"{item_type}:{item_data}")
    return ", ".join(descriptions)

def run_hero_shopping(
    client_or_headers,
    buy_soul_shop_items: bool = True,
    hero_shop_ids: list[ShopId] | None = None,
    buy_pet_potions: bool = False,
    buy_town_shop_items: bool = True,
):
    """
    ヒーローソウルおよび特定アイテムをショップで購入する。

    Args:
        client_or_headers: HWClientインスタンスまたはヘッダー辞書。
        buy_soul_shop_items: ソウルショップの全アイテムを購入するかどうか。
        hero_shop_ids: ヒーローソウルを購入する対象ショップのリスト。
        buy_pet_potions: ペットソウルショップでペットポーションを購入するかどうか。
        buy_town_shop_items: タウンショップの固定スロット（2,4,5,6）を購入するかどうか。
            デフォルト True（ゴールド消費あり）。
    """
    # TODO: 購入対象の on/off を CLI フラグ化する場合はここの引数を公開する（現状は呼出側の固定値）。
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    print(f"\n{Emojis.STEP}--- Fetching current shop status ---", flush=True)
    results: list[ShopResult] = []

    # 全ショップ情報を取得（shopGetAll の取得・パースは共通ヘルパーに一元化）
    try:
        shops_data = fetch_shops(client)
    except ShopReadError as exc:
        error = exc.error_name or f"Invalid format: {exc}"
        print(f"{Emojis.ERROR}Error: Failed to fetch shop data. {exc}", flush=True)
        return [ShopResult(action="Fetch Shop Status", status=ResponseStatus.ERROR, error=error)], None

    print(f"\n{Emojis.STEP}--- Purchasing Target Items ---", flush=True)

    buy_queue: list[BuyItem] = []
    # 調査対象とするショップを特定
    shop_ids_to_check = set()
    if hero_shop_ids:
        shop_ids_to_check.update(hero_shop_ids)
    if buy_soul_shop_items:
        shop_ids_to_check.add(ShopId.SOUL)
    if buy_pet_potions:
        shop_ids_to_check.add(ShopId.PET_SOUL)
    if buy_town_shop_items:
        shop_ids_to_check.add(ShopId.TOWN)

    # ショップIDの数値順（1,4,5,6,8,9,17）に安定ソートする。
    sorted_shop_ids = sorted(shop_ids_to_check, key=_shop_sort_key)

    # shops_data のキーは通常 str だが、int 型で返る実装差異に備えて
    # str キーに正規化する（fetch_shops は dict を保証するが防御的に確認）。
    normalized_shops = (
        {str(k): v for k, v in shops_data.items()} if isinstance(shops_data, dict) else {}
    )

    for shop_id_enum in sorted_shop_ids:
        shop_id_str = shop_id_enum.value
        try:
            slots = get_shop_slots(normalized_shops, shop_id_str)
        except ShopNotFoundError:
            continue
        shop_name = SHOP_NAMES.get(shop_id_enum, f"Shop {shop_id_str}")

        for slot_id, item in slots.items():
            if is_bought(item):
                continue

            if not isinstance(item, dict):
                logger.warning("Skipping [%s] slot %r: slot data is not a dict.", shop_name, slot_id)
                continue
            reward = item.get("reward", {})
            cost = item.get("cost", {})
            if not isinstance(cost, dict):
                cost = {}
            # reward 空（falsy）のスロットはゴミ購入防止のためスキップ。
            # cost 空は購入対象に含める（Soul 系テストで cost={} の正規ケースがあるため）。
            if not isinstance(reward, dict) or not reward:
                logger.warning("Skipping [%s] slot %r: empty reward.", shop_name, slot_id)
                continue
            try:
                slot_int = int(slot_id)
            except (TypeError, ValueError):
                logger.warning("Skipping [%s] slot %r: non-numeric slot id.", shop_name, slot_id)
                continue
            is_hero = "fragmentHero" in reward

            # 購入判定
            should_buy = False

            # 1. ヒーロー購入判定
            if is_hero and hero_shop_ids and shop_id_enum in hero_shop_ids:
                should_buy = True

            # 2. ソウルショップ非ヒーローアイテム判定
            if not is_hero and shop_id_enum == ShopId.SOUL and buy_soul_shop_items:
                should_buy = True

            # 3. ペットポーション購入判定 (PET_SOUL ショップのスロット 3 はペットポーション固定)
            if shop_id_enum == ShopId.PET_SOUL and str(slot_id) == PET_POTION_SLOT_ID and buy_pet_potions:
                should_buy = True

            # 4. タウンショップ購入判定（固定スロット 2,4,5,6。購入済みは上部 is_bought で除外済み）
            if shop_id_enum == ShopId.TOWN and str(slot_id) in TOWN_SHOP_SLOT_IDS and buy_town_shop_items:
                should_buy = True

            if should_buy:
                buy_queue.append(
                    BuyItem(
                        shopId=shop_id_enum,
                        shopName=shop_name,
                        slot=slot_int,
                        reward=reward,
                        cost=cost,
                        label=f"[{shop_name}] Slot:{slot_id} -> {format_reward_desc(reward)}",
                    )
                )

    if not buy_queue:
        print(f"{Emojis.INFO}No items to purchase at this time.", flush=True)
    else:
        total_souls_bought = 0
        skipped_shops_due_to_funds = set()
        for i, item in enumerate(buy_queue):
            if item.shopId in skipped_shops_due_to_funds:
                print(f"[{i + 1}/{len(buy_queue)}] {Emojis.WARNING}Skipping {item.label} (Insufficient funds).", flush=True)
                continue

            print(f"[{i + 1}/{len(buy_queue)}] Purchasing {item.label}...", flush=True)

            buy_call = {
                "name": ApiAction.SHOP_BUY,
                "args": {"shopId": int(item.shopId.value), "slot": item.slot, "cost": item.cost, "reward": item.reward},
                "ident": f"buy_{item.shopId.value}_{item.slot}",
            }

            res = client.call({"calls": [buy_call]})

            if res.is_success:
                print(f"  Result: {Emojis.SUCCESS}Success", flush=True)
                results.append(ShopResult(action=item.label, status=ResponseStatus.SUCCESS))
                # ヒーローソウルの個数を集計
                for item_type, item_data in item.reward.items():
                    if item_type == "fragmentHero":
                        if isinstance(item_data, dict):
                            for _item_id, amount in item_data.items():
                                total_souls_bought += int(amount)
                        else:
                            total_souls_bought += int(item_data)
            else:
                error_name = res.error_name or "unknown"
                print(f"  Result: {Emojis.ERROR}Failed ({error_name})", flush=True)
                results.append(ShopResult(action=item.label, status=ResponseStatus.ERROR, error=error_name))
                if error_name == ErrorName.NOT_ENOUGH:
                    print(f"  -> {Emojis.WARNING}Insufficient funds for {item.shopName}. Skipping rest of this shop.", flush=True)
                    skipped_shops_due_to_funds.add(item.shopId)

            client.sleep()
        
        print(f"\n{Emojis.FINISH}--- Shopping Results Summary ---", flush=True)
        print(f"  {Emojis.SUCCESS}Total Hero Souls Purchased: {total_souls_bought}", flush=True)

    return results, None  # exchange_info は上位で処理
