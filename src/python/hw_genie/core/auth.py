import json
import os
import re
import time
from datetime import datetime
from typing import TypedDict
import requests
from hw_genie.core.client import PlayerStatus

class SessionData(TypedDict, total=False):
    headers: dict[str, str]
    status: str  # "success" or "error"
    last_updated: str
    player: PlayerStatus
    message: str

# パッケージのルートディレクトリ（session.jsonを置く場所）を取得
# HW-Genie/src/python/hw_genie/core/auth.py -> HW-Genie/
PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def extract_headers_from_curl(curl_command):
    """curlコマンドから x-auth-* ヘッダーを抽出する"""
    headers = {}
    # -H 'Key: Value' または -H "Key: Value" を抽出
    matches = re.findall(r"-H\s+['\"]([^'\"]+)['\"]", curl_command)
    for match in matches:
        if ":" in match:
            key, value = match.split(":", 1)
            key = key.strip().lower()
            if key.startswith("x-auth-"):
                headers[key] = value.strip()
        elif match.strip().lower().startswith("x-auth-"):
            # x-auth-session-key; のようなケース
            key = match.strip().rstrip(";").lower()
            headers[key] = ""
    
    return headers


def extract_payload_from_curl(curl_command):
    """curlコマンドから JSON ペイロードを抽出し、stashClient などの不要な命令のみを除去する"""
    # --data-raw '...' または --data '...' または -d '...' を抽出
    # 最短一致 (.*?) ではなく、引用符の間のすべてを取得するように修正
    payload_str = None
    
    # シングルクォートで囲まれたケース
    match = re.search(r"--data(?:-raw)?\s+'({.*})'", curl_command, re.DOTALL)
    if not match:
        # ダブルクォートで囲まれたケース
        match = re.search(r"--data(?:-raw)?\s+\"({.*})\"", curl_command, re.DOTALL)
    if not match:
        # 引用符がないケース（または行末までのケース）
        match = re.search(r"--data(?:-raw)?\s+({.*})", curl_command, re.DOTALL)

    if match:
        payload_str = match.group(1).strip()
        # 末尾の引用符がグループ内に残ってしまう場合の修正
        if payload_str.endswith("'") or payload_str.endswith('"'):
            payload_str = payload_str[:-1].strip()

    if payload_str:
        try:
            full_payload = json.loads(payload_str)
            if "calls" in full_payload:
                # 除外対象のノイズ（クライアント側のログ同期やトラッキングなど、ゲームロジックに不要なもの）
                noise_names = ["stashClient", "trackEvent", "billingGetLast"]
                
                # ノイズ以外の有効な命令をすべて抽出
                filtered_calls = [c for c in full_payload["calls"] if c.get("name") not in noise_names]

                if filtered_calls:
                    return {"calls": filtered_calls}
            return full_payload
        except Exception:
            pass
    return None


def get_session_path(account="default"):
    if account == "default":
        return os.path.join(PKG_ROOT, "session.json")
    return os.path.join(PKG_ROOT, f"session.{account}.json")


def get_user_info(headers: dict[str, str]) -> SessionData:
    url = "https://heroes-wb.nextersglobal.com/api/"

    # リクエストIDのインクリメント
    request_id = int(headers.get("x-request-id", 100)) + 1
    headers["x-request-id"] = str(request_id)

    payload = {
        "calls": [
            {"name": "userGetInfo", "args": {}, "context": {"actionTs": int(time.time())}, "ident": "body"},
            {"name": "arenaGetAll", "args": {}, "context": {"actionTs": int(time.time())}, "ident": "arena"},
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        res_data = response.json()

        if "results" in res_data and len(res_data["results"]) > 0:
            user_info = {}
            arena_info = {}

            for item in res_data["results"]:
                if item.get("ident") == "body":
                    user_info = item.get("result", {}).get("response", {})
                elif item.get("ident") == "arena":
                    arena_info = item.get("result", {}).get("response", {})

            name = user_info.get("name", "Unknown")
            level = int(user_info.get("level", 1))

            # スタミナ情報の抽出
            energy = 0
            for item in user_info.get("refillable", []):
                if item.get("id") == 1:
                    energy = item.get("amount", 0)
                    break

            # アリーナ順位の抽出
            arena_rank = int(arena_info.get("arenaPlace", 0)) if arena_info.get("arenaPlace") else 0
            grand_rank = int(arena_info.get("grandPlace", 0)) if arena_info.get("grandPlace") else 0

            player = PlayerStatus(
                name=name,
                level=level,
                gold=user_info.get("gold", 0),
                gems=user_info.get("starMoney", 0),
                energy=energy,
                arena_rank=arena_rank,
                grand_rank=grand_rank,
            )

            return {
                "headers": headers,
                "status": "success",
                "last_updated": datetime.now().isoformat(),
                "player": player,
            }
        return {"status": "error", "message": "Failed to parse API response"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def save_session(data: SessionData, account: str = "default") -> None:
    path = get_session_path(account)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    # 既存のデータを読み込んで mission_id を保持する
    existing_data = {}
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                content = f.read()
                if content:
                    existing_data = json.loads(content)
        except (json.JSONDecodeError, IOError, TypeError):
            pass

    # PlayerStatus オブジェクトが含まれる場合は辞書に変換
    save_data = data.copy()
    if "player" in save_data and hasattr(save_data["player"], "to_dict"):
        save_data["player"] = save_data["player"].to_dict()

    # mission_id が存在すればマージ
    if "last_item_raid_mission_id" in existing_data:
        save_data["last_item_raid_mission_id"] = existing_data["last_item_raid_mission_id"]
        
    with open(path, "w") as f:
        json.dump(save_data, f, indent=2)
    print(f"Saved session to: {os.path.basename(path)}")


def load_session(account: str = "default") -> SessionData | None:
    path = get_session_path(account)
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
            # player 辞書を PlayerStatus インスタンスに復元
            if "player" in data and isinstance(data["player"], dict):
                data["player"] = PlayerStatus.from_dict(data["player"])
            return data
    return None


def update_session_with_headers(headers: dict[str, str], account_alias: str = "default") -> SessionData:
    """ヘッダー情報を元にユーザー情報を取得し、session.json と個別のセッションファイルを更新する"""
    info = get_user_info(headers)
    if info["status"] == "success":
        # 1. session.json (default) を保存
        save_session(info, "default")

        # 2. プレイヤー名でのセッションファイルを保存
        player_name = info["player"].name
        save_session(info, player_name)

        # 3. エイリアス指定があればそれも保存
        if account_alias != "default" and account_alias != player_name:
            save_session(info, account_alias)

        return info
    return info
