from hw_genie.core.client import Emojis
from hw_genie.commands.hero_raid import run_hero_raid
from hw_genie.commands.hero_shopping import run_hero_shopping
from hw_genie.commands.item_raid import run_item_raid
from hw_genie.core.utils import print_player_status
from hw_genie.core.session_manager import SessionManager

# デフォルトのミッションリスト
HERO_MISSION_IDS = [76, 116, 193, 198, 203, 204, 214]


def run_daily_raid(client_or_headers, item_payload=None, account_alias=None):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
        # Use provided alias or fallback to player ID
        account = account_alias or client.headers.get("x-auth-user-id", "default")
    else:
        client = client_or_headers
        # Use provided alias or fallback to player ID
        account = account_alias or getattr(client, "headers", {}).get("x-auth-user-id", "default")


    print(f"\n{Emojis.START}Starting Daily Routine...", flush=True)

    # 1. Hero Raids (デイリーでは自動回復を無効にする)
    hero_res, recovery_count, ex_info = run_hero_raid(client, HERO_MISSION_IDS, times=3, allow_recovery=False)

    # ステータスチェック (認証エラーは例外で止まるのでスタミナのみチェック)
    from hw_genie.core.client import ResponseStatus

    if any(r.status == ResponseStatus.STAMINA_ERROR for r in hero_res):
        print(f"{Emojis.WARNING}Stamina empty in Phase 1. Stopping Daily Routine.", flush=True)
        return (hero_res, recovery_count, ex_info), (None, None)

    # 2. Item Raid
    if item_payload is not None:
        # Mission ID の抽出 (top-level or calls)
        mission_id = item_payload.get("mission_id")
        if not mission_id:
            for call in item_payload.get("calls", []):
                if call.get("name") == "missionRaid":
                    mission_id = call["args"].get("id")
                    break
        
        # DBから補完
        if not mission_id:
            mission_id = SessionManager.get_last_mission_id(account=account)
        
        if not mission_id:
            print(f"\n{Emojis.INFO}No item raid mission ID configured. Skipping Item Raid.", flush=True)
        else:
            # 明示的に mission_id をセット
            item_payload["mission_id"] = mission_id
            print(f"\n{Emojis.STEP}Executing Item Raids (Stamina Limit)...", flush=True)
            client.sleep()
            run_item_raid(client, item_payload, account=account)

    # 3. Soul Shop Items (Non-Hero)
    client.sleep()
    shop_res, shop_ex_info = run_hero_shopping(client, buy_soul_shop_items=True, hero_shop_ids=None)

    # Status
    status = client.fetch_player_status()
    print_player_status(status)

    print(f"\n{Emojis.FINISH}Daily Routine Completed.", flush=True)
    return (hero_res, recovery_count, ex_info), (shop_res, shop_ex_info)
