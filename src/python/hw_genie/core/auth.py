import json
import os
import re
import time
from datetime import datetime
from typing import TypedDict, Optional
import requests
from hw_genie.core.client import PlayerStatus
from hw_genie.core.session_manager import SessionManager

class SessionData(TypedDict, total=False):
    headers: dict[str, str]
    status: str
    last_updated: str
    player: PlayerStatus
    message: str

PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

def extract_headers_from_curl(curl_command):
    """curlコマンドから x-auth-* ヘッダーを抽出する"""
    headers = {}
    matches = re.findall(r"-H\s+['\"]([^'\"]+)['\"]", curl_command)
    for match in matches:
        if ":" in match:
            key, value = match.split(":", 1)
            key = key.strip().lower()
            if key.startswith("x-auth-"):
                headers[key] = value.strip()
        elif match.strip().lower().startswith("x-auth-"):
            key = match.strip().rstrip(";").lower()
            headers[key] = ""
    return headers

def extract_payload_from_curl(curl_command):
    """curlコマンドから JSON ペイロードを抽出し、stashClient などの不要な命令のみを除去する"""
    payload_str = None
    match = re.search(r"--data(?:-raw)?\s+'({.*})'", curl_command, re.DOTALL)
    if not match:
        match = re.search(r"--data(?:-raw)?\s+\"({.*})\"", curl_command, re.DOTALL)
    if not match:
        match = re.search(r"--data(?:-raw)?\s+({.*})", curl_command, re.DOTALL)
    if match:
        payload_str = match.group(1).strip()
        if payload_str.endswith("'") or payload_str.endswith('"'):
            payload_str = payload_str[:-1].strip()
    if payload_str:
        try:
            full_payload = json.loads(payload_str)
            if "calls" in full_payload:
                noise_names = ["stashClient", "trackEvent", "billingGetLast"]
                filtered_calls = [c for c in full_payload["calls"] if c.get("name") not in noise_names]
                if filtered_calls:
                    return {"calls": filtered_calls}
            return full_payload
        except Exception: pass
    return None

def get_session_path(account="default"):
    if account == "default":
        return os.path.join(PKG_ROOT, "session.json")
    return os.path.join(PKG_ROOT, f"session.{account}.json")

def get_user_info(headers: dict[str, str]) -> SessionData:
    url = "https://heroes-wb.nextersglobal.com/api/"
    headers["x-request-id"] = str(int(headers.get("x-request-id", 100)) + 1)
    payload = {"calls": [{"name": "userGetInfo", "args": {}, "ident": "body"}]}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        res_data = response.json()
        if "results" in res_data:
            user_info = res_data["results"][0]["result"]["response"]
            player = PlayerStatus.from_dict(user_info)
            return {"headers": headers, "status": "success", "last_updated": datetime.now().isoformat(), "player": player}
        return {"status": "error", "message": "API error"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def save_session(data: SessionData, account: str = "default") -> None:
    path = get_session_path(account)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing_data = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                existing_data = json.load(f)
        except Exception: pass

    save_data = data.copy()
    if hasattr(save_data.get("player"), "to_dict"):
        save_data["player"] = save_data["player"].to_dict()

    mission_id = SessionManager.get_last_mission_id(account=account) or existing_data.get("last_item_raid_mission_id")
    if mission_id is not None:
        save_data["last_item_raid_mission_id"] = mission_id
        
    with open(path, "w") as f:
        json.dump(save_data, f, indent=2)

def load_session(account: str = "default") -> Optional[SessionData]:
    path = get_session_path(account)
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
            if "player" in data and isinstance(data["player"], dict):
                data["player"] = PlayerStatus.from_dict(data["player"])
            return data
    return None

def update_session_with_headers(headers: dict[str, str], account_alias: str = "default") -> SessionData:
    info = get_user_info(headers)
    if info["status"] == "success":
        player_name = info["player"].name
        save_session(info, "default")
        save_session(info, player_name)
        if account_alias != "default" and account_alias != player_name:
            save_session(info, account_alias)
    return info
