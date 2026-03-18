from typing import Any
from dataclasses import dataclass
from hw_genie.core.client import (
    ApiAction,
    Emojis,
    ErrorName,
    ResponseStatus,
)


@dataclass
class ShopResult:
    action: str
    status: ResponseStatus
    error: str | None = None


@dataclass
class BuyItem:
    shopId: int
    shopName: str
    slot: int
    reward: dict[str, Any]
    cost: dict[str, Any]
    label: str


# 購入対象とするショップ設定
TARGET_SHOP_IDS = ["4", "5", "6", "8", "9"]
SHOP_NAMES = {"4": "Arena", "5": "Grand Arena", "6": "Tower", "8": "Soul", "9": "Friend"}


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


def run_hero_shopping(client_or_headers, soul_only: bool = False):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    print(f"\n{Emojis.STEP}--- Step 1: Fetching current shop status ---", flush=True)
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

    print(f"\n{Emojis.STEP}--- Step 2: Purchasing Target Items ---", flush=True)

    buy_queue: list[BuyItem] = []

    for shop_id_str in TARGET_SHOP_IDS:
        if shop_id_str not in shops_data:
            continue

        shop = shops_data[shop_id_str]
        slots = shop.get("slots", {})
        shop_name = SHOP_NAMES.get(shop_id_str, f"Shop {shop_id_str}")

        for slot_id, item in slots.items():
            if item.get("bought") in [True, 1, "1"]:
                continue

            reward = item.get("reward", {})
            cost = item.get("cost", {})

            # 購入判定
            should_buy = False
            if "fragmentHero" in reward:  # ソウルストーン
                should_buy = True
            elif shop_id_str == "8":  # ソウルショップは全アイテム
                should_buy = True

            # --soul-only フラグがある場合はソウルストーン以外をスキップ
            if soul_only and "fragmentHero" not in reward:
                should_buy = False

            if should_buy:
                buy_queue.append(
                    BuyItem(
                        shopId=int(shop_id_str),
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
        skipped_shops_due_to_funds = set()
        for i, item in enumerate(buy_queue):
            if item.shopId in skipped_shops_due_to_funds:
                print(f"[{i + 1}/{len(buy_queue)}] {Emojis.WARNING}Skipping {item.label} (Insufficient funds).", flush=True)
                continue

            print(f"[{i + 1}/{len(buy_queue)}] Purchasing {item.label}...", flush=True)

            buy_call = {
                "name": ApiAction.SHOP_BUY,
                "args": {"shopId": item.shopId, "slot": item.slot, "cost": item.cost, "reward": item.reward},
                "ident": f"buy_{item.shopId}_{item.slot}",
            }

            res = client.call({"calls": [buy_call]})

            if res.is_success:
                print(f"  Result: {Emojis.SUCCESS}Success", flush=True)
                results.append(ShopResult(action=item.label, status=ResponseStatus.SUCCESS))
            else:
                error_name = res.error_name or "unknown"
                print(f"  Result: {Emojis.ERROR}Failed ({error_name})", flush=True)
                results.append(ShopResult(action=item.label, status=ResponseStatus.ERROR, error=error_name))
                if error_name == ErrorName.NOT_ENOUGH:
                    print(f"  -> {Emojis.WARNING}Insufficient funds for {item.shopName}. Skipping rest of this shop.", flush=True)
                    skipped_shops_due_to_funds.add(item.shopId)

            client.sleep()

    return results, None  # exchange_info は上位で処理
