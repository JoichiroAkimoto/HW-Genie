import json
import os
import time
from datetime import datetime
import requests

# パッケージのルートディレクトリ（session.jsonを置く場所）を取得
# HW-Genie/src/python/hw_genie/core/auth.py -> HW-Genie/
PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def get_session_path(account="default"):
    if account == "default":
        return os.path.join(PKG_ROOT, "session.json")
    return os.path.join(PKG_ROOT, f"session.{account}.json")


def get_user_info(headers):
    url = "https://heroes-wb.nextersglobal.com/api/"

    # リクエストIDのインクリメント
    request_id = int(headers.get("x-request-id", 100)) + 1
    headers["x-request-id"] = str(request_id)

    payload = {"calls": [{"name": "userGetInfo", "args": {}, "context": {"actionTs": int(time.time())}, "ident": "body"}]}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        res_data = response.json()

        if "results" in res_data and len(res_data["results"]) > 0:
            result = res_data["results"][0].get("result", {}).get("response", {})
            name = result.get("name", "Unknown")
            level = int(result.get("level", 1))

            # スタミナ情報の抽出
            energy = 0
            for item in result.get("refillable", []):
                if item.get("id") == 1:
                    energy = item.get("amount", 0)
                    break

            return {
                "status": "success",
                "last_updated": datetime.now().isoformat(),
                "player": {
                    "name": name,
                    "level": level,
                    "gold": result.get("gold", 0),
                    "gems": result.get("starMoney", 0),
                    "energy": energy,
                    "energy_max": level + 60,
                },
                "headers": headers,
            }
        return {"status": "error", "message": "Failed to parse API response"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def save_session(data, account="default"):
    path = get_session_path(account)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved session to: {os.path.basename(path)}")


def load_session(account="default"):
    path = get_session_path(account)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def update_session_with_headers(headers, account_alias="default"):
    """ヘッダー情報を元にユーザー情報を取得し、session.json と個別のセッションファイルを更新する"""
    info = get_user_info(headers)
    if info["status"] == "success":
        # 1. session.json (default) を保存
        save_session(info, "default")

        # 2. プレイヤー名でのセッションファイルを保存
        player_name = info["player"]["name"]
        save_session(info, player_name)

        # 3. エイリアス指定があればそれも保存
        if account_alias != "default" and account_alias != player_name:
            save_session(info, account_alias)

        return info
    return info
