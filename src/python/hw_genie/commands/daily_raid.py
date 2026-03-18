from hw_genie.core.client import Emojis
from hw_genie.commands.hero_raid import run_hero_raid
from hw_genie.commands.hero_shopping import run_hero_shopping
from hw_genie.commands.item_raid import run_item_raid

# デフォルトのミッションリスト
HERO_MISSION_IDS = [76, 116, 193, 198, 203, 204, 214]


def run_daily_raid(client_or_headers, item_payload=None):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
    else:
        client = client_or_headers

    print(f"\n{Emojis.START}Starting Daily Routine...", flush=True)

    # 1. Hero Raids (デイリーでは自動回復を無効にする)
    hero_res, recovery_count, ex_info = run_hero_raid(client, HERO_MISSION_IDS, times=3, allow_recovery=False)

    # ステータスチェック
    from hw_genie.core.client import ResponseStatus

    if any(r.status in [ResponseStatus.STAMINA_ERROR, ResponseStatus.AUTH_ERROR] for r in hero_res):
        print(f"{Emojis.WARNING}Critical issue in Phase 1. Stopping Daily Routine.", flush=True)
        return (hero_res, recovery_count, ex_info), (None, None)

    # 2. Item Raid
    if item_payload:
        client.sleep()
        run_item_raid(client, item_payload, max_iterations=10)

    # 3. Soul Shop Items (Non-Hero)
    client.sleep()
    shop_res, shop_ex_info = run_hero_shopping(client, buy_soul_shop_items=True, hero_shop_ids=None)

    print(f"\n{Emojis.FINISH}Daily Routine Completed.", flush=True)
    return (hero_res, recovery_count, ex_info), (shop_res, shop_ex_info)
