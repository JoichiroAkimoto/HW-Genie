from hw_genie.core.client import Emojis
from hw_genie.core.utils import print_player_status
from hw_genie.core.session_manager import SessionManager


def run_item_raid(client_or_headers, payload_template, max_iterations=9999):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
        # ヘッダーからアカウント名を推測する（x-auth-user-id などを使うのが確実だが、ここでは簡易的に）
        account = client.headers.get("x-auth-user-id", "default")
    else:
        client = client_or_headers
        account = "default"

    # ミッションIDの決定: payload_template > SessionManager
    mission_id = payload_template.get("mission_id")
    if mission_id:
        SessionManager.set_last_mission_id(mission_id, account=account)
    else:
        mission_id = SessionManager.get_last_mission_id(account=account)
        # payload_template にも反映しておく
        payload_template["mission_id"] = mission_id

    print(f"\n{Emojis.START}Starting Item Raid (Max: {max_iterations}, Mission ID: {mission_id})...", flush=True)

    success_count = 0
    for i in range(max_iterations):
        print(f"{Emojis.STEP}Iteration {i + 1}: Executing Request...", end=" ", flush=True)

        payload = client.prepare_item_payload(payload_template)
        
        # mission_id がある場合はペイロードの適切な場所に埋め込む必要がある
        for call in payload.get("calls", []):
            if call.get("name") == "missionRaid":
                call["args"]["id"] = mission_id

        res = client.call(payload)

        if res.is_success:
            print(f"{Emojis.SUCCESS}Success", flush=True)
            success_count += 1
        elif res.error_name in ["notEnoughStamina", "limitReached"]:
            print(f"{Emojis.WARNING}Stopping: {res.error_name}", flush=True)
            break
        else:
            print(f"{Emojis.ERROR}Failed ({res.error_name})", flush=True)
            break

        client.sleep()

    print(f"\n{Emojis.FINISH}Item Raid Completed. {success_count} successful raids.", flush=True)

    # Status
    status = client.fetch_player_status()
    print_player_status(status)
