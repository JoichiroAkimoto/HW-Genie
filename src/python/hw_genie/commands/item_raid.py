from hw_genie.core.client import Emojis
from hw_genie.core.utils import print_player_status
from hw_genie.core.session_manager import SessionManager


def run_item_raid(client_or_headers, payload_template, max_iterations=9999, account=None):
    if isinstance(client_or_headers, dict):
        from hw_genie.core.client import HWClient

        client = HWClient(client_or_headers)
        # プレイヤー名を取得してアカウント名として使用
        try:
            status = client.fetch_player_status()
            actual_account = status.name
        except Exception:
            actual_account = "default"
        
        if account:
            account = account
        else:
            account = actual_account
    else:
        client = client_or_headers
        account = account or "default"


    # ミッションIDの決定: payload_template > SessionManager
    mission_id = payload_template.get("mission_id")
    
    # payload_template の calls からも取得を試みる (curl データ対応)
    if not mission_id:
        for call in payload_template.get("calls", []):
            if call.get("name") == "missionRaid":
                mission_id = call["args"].get("id")
                break
    
    if mission_id:
        SessionManager.set_last_mission_id(mission_id, account=account)
    else:
        mission_id = SessionManager.get_last_mission_id(account=account)
        # payload_template にも反映しておく
        payload_template["mission_id"] = mission_id

    # ペイロードに calls がない場合、デフォルトの missionRaid ペイロードを生成する
    if not payload_template.get("calls") and mission_id:
        payload_template["calls"] = [
            {
                "name": "missionRaid",
                "args": {"id": mission_id, "times": 10},
                "context": {"actionTs": 0},
                "ident": "body",
            }
        ]


    print(f"\n{Emojis.START}Starting Item Raid (Max: {max_iterations}, Mission ID: {mission_id})...", flush=True)

    success_count = 0
    for i in range(max_iterations):
        print(f"{Emojis.STEP}Iteration {i + 1}: Executing Request...", end=" ", flush=True)

        payload = client.prepare_item_payload(payload_template)
        
        # mission_id がある場合はペイロードの適切な場所に埋め込む必要がある
        if mission_id:
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
            detail_msg = f": {res.detail}" if res.detail else ""
            print(f"{Emojis.ERROR}Failed ({res.error_name}){detail_msg}", flush=True)
            break

        client.sleep()

    print(f"\n{Emojis.FINISH}Item Raid Completed. {success_count} successful raids.", flush=True)

    # Status
    status = client.fetch_player_status()
    print_player_status(status)
