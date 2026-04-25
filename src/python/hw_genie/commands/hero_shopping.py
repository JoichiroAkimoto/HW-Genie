from typing import Any
from dataclasses import dataclass
from enum import Enum
from hw_genie.core.client import (
    ApiAction,
    Emojis,
    ErrorName,
    ResponseStatus,
)

class ShopId(Enum):
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
TARGET_SHOP_IDS = [ShopId.ARENA, ShopId.GRAND_ARENA, ShopId.TOWER, ShopId.SOUL, ShopId.FRIEND]
SHOP_NAMES = {
    ShopId.ARENA: "Arena",
    ShopId.GRAND_ARENA: "Grand Arena",
    ShopId.TOWER: "Tower",
    ShopId.SOUL: "Soul",
    ShopId.FRIEND: "Friend",
    ShopId.PET_SOUL: "Pet Soul",
}

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
    buy_pet_potions: bool = True,
):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    print(f"\n{Emojis.STEP}--- Fetching current shop status ---", flush=True)
    results: list[ShopResult] = []

    # 全ショップ情報を取得
    get_all_payload = {"calls": [{"name": ApiAction.SHOP_GET_ALL, "args": {}, "ident": "shopGetAll"}]}
    res_all = client.call(get_all_payload)

    if not res_all.is_success:
        print(f"{Emojis.ERROR}Error: Failed to fetch shop data. {res_all.error_name}", flush=True)
        return [ShopResult(action="Fetch Shop Status", status=ResponseStatus.ERROR, error=res_all.error_name)], None

    try:
        shops_data = res_all.detail["response"]
    except (KeyError, TypeError):
        print(f"{Emojis.ERROR}Error: Unexpected response format.", flush=True)
        return [ShopResult(action="Fetch Shop Status", status=ResponseStatus.ERROR, error="Invalid format")], None

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

    # ソートして順番を安定させる（ShopIdの定義順など）
    # 元々の TARGET_SHOP_IDS の順序を尊重したい場合は工夫が必要だが、一旦セットからリスト化
    sorted_shop_ids = sorted(list(shop_ids_to_check), key=lambda x: x.value)

    for shop_id_enum in sorted_shop_ids:
        shop_id_str = shop_id_enum.value
        if shop_id_str not in shops_data:
            continue

        shop = shops_data[shop_id_str]
        slots = shop.get("slots", {})
        shop_name = SHOP_NAMES.get(shop_id_enum, f"Shop {shop_id_str}")

        for slot_id, item in slots.items():
            if item.get("bought") in [True, 1, "1"]:
                continue

            reward = item.get("reward", {})
            cost = item.get("cost", {})
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
            if shop_id_enum == ShopId.PET_SOUL and slot_id == "3" and buy_pet_potions:
                should_buy = True

            if should_buy:
                buy_queue.append(
                    BuyItem(
                        shopId=shop_id_enum,
                        shopName=shop_name,
                        slot=int(slot_id),
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
