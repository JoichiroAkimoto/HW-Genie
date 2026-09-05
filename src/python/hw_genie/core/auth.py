import json
import re
from datetime import datetime, timezone
from typing import TypedDict, Optional
import requests
from hw_genie.core.client import PlayerStatus, load_session_headers
from hw_genie.core.session_manager import SessionManager


class SessionData(TypedDict, total=False):
    headers: dict[str, str]
    status: str
    last_updated: str
    player: PlayerStatus
    message: str


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
        except Exception:
            pass
    return None


AUTH_EXPIRED_MESSAGE = "Session expired or invalid signature. Please update your curl or run auth-server."


def _unexpected_response_message(response) -> str:
    """JSON でない API レスポンスからユーザー向けエラーメッセージを組み立てる。"""
    text = (response.text or "").strip()
    if "Invalid signature" in text:
        return AUTH_EXPIRED_MESSAGE
    if text:
        return f"Unexpected response from API: {text[:200]}"
    return "Empty response from API"


def get_user_info(headers: dict[str, str]) -> SessionData:
    """ゲーム API からプレイヤー情報を取得する。

    失敗時は ``{"status": "error", "message": ...}`` を返す。セッション失効
    （HTTP 401 / "Invalid signature" / ``InvalidSession`` エラー）は原因が
    特定できるメッセージで報告し、JSON でない応答は本文の一部を表示する。
    """
    url = "https://heroes-wb.nextersglobal.com/api/"
    headers["x-request-id"] = str(int(headers.get("x-request-id", 100)) + 1)
    payload = {"calls": [{"name": "userGetInfo", "args": {}, "ident": "body"}, {"name": "arenaGetAll", "args": {}, "ident": "arena"}]}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 401:
            return {"status": "error", "message": AUTH_EXPIRED_MESSAGE}
        response.raise_for_status()
        try:
            res_data = response.json()
        except ValueError:
            return {"status": "error", "message": _unexpected_response_message(response)}
        if isinstance(res_data, dict):
            error = res_data.get("error")
            error_name = error.get("name") if isinstance(error, dict) else error
            if error_name in ("auth", "InvalidSession"):
                return {"status": "error", "message": AUTH_EXPIRED_MESSAGE}
        if "results" in res_data:
            user_info = {}
            arena_info = {}
            for item in res_data["results"]:
                if item["ident"] == "body":
                    user_info = item["result"]["response"]
                elif item["ident"] == "arena":
                    arena_info = item["result"]["response"]
            player = PlayerStatus(
                id=user_info.get("id", "Unknown"),
                name=user_info.get("name", "Unknown"),
                level=user_info.get("level", 0),
                gold=user_info.get("gold", 0),
                gems=user_info.get("starMoney", 0),
                energy=next((i["amount"] for i in user_info.get("refillable", []) if i.get("id") == 1), 0),
                arena_rank=int(arena_info.get("arenaPlace", 0)),
                grand_rank=int(arena_info.get("grandPlace", 0)),
                timezone=int(user_info.get("timeZone", 0) or 0),
                next_day_ts=int(user_info.get("nextDayTs", 0) or 0),
            )
            return {"headers": headers, "status": "success", "last_updated": datetime.now(timezone.utc).isoformat(), "player": player}
        return {"status": "error", "message": "API error"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def save_session(data: SessionData, account: str = "default") -> None:
    SessionManager.save(account, data)


def load_session(account: str = "default") -> Optional[SessionData]:
    data = SessionManager.load(account)
    if not data:
        return None

    # PlayerStatus オブジェクトへの変換
    if "player" in data and isinstance(data["player"], dict):
        data["player"] = PlayerStatus.from_dict(data["player"])
    return data


def update_session_with_headers(headers: dict[str, str], account_alias: str | None = None) -> SessionData:
    info = get_user_info(headers)
    if info["status"] == "success":
        player_name = info["player"].name
        save_session(info, player_name)
        # 別名（-a）が明示されていて実名と異なる場合のみ追加保存する。
        # None / "default"（旧エイリアス）は「別名なし＝実名のみ」として扱う。
        if account_alias and account_alias != "default" and account_alias != player_name:
            save_session(info, account_alias)
    return info


def _refresh_one(account: str) -> str | None:
    """Fetch fresh status for a single account; returns an error message or None."""
    headers = load_session_headers(account)
    if not headers:
        return f"Session not found for account '{account}'."
    info = get_user_info(headers)
    if info["status"] != "success":
        return f"{account}: {info.get('message', 'API error')}"
    # 対象アカウントへ 1 書き込みのみ（--fresh の並列更新ではアカウント 1 件
    # につき 1 書き込みに抑え、WAL 競合の機会と途中終了時のアカウント名
    # 書き換えを防ぐ）。
    # 旧 "default" エイリアスは実名へリネームする: player_id は UNIQUE 制約の
    # ため、実名で保存すると同じ行の alias が実名に更新される。
    if account == "default":
        save_session(info, info["player"].name)
    else:
        save_session(info, account)
    return None


def refresh_all_accounts(accounts: list[str], max_parallel: int = 4) -> list[tuple[str, str | None]]:
    """Fetch the latest player status for ``accounts`` from the game API (parallel).

    Each account is refreshed and persisted to the DB independently; a failure
    (expired session, network error, ...) is captured as an error message
    instead of aborting the other accounts.

    Args:
        max_parallel: 同時実行の上限。既定 4。``cmd_auth`` はこの既定値でなく
            ``runner.resolve_max_parallel``（``HW_MAX_PARALLEL`` を尊重）
            から導出した値を渡す。

    Returns:
        list of ``(account, error_message_or_None)``.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not accounts:
        return []
    workers = max(1, min(max_parallel, len(accounts)))
    results: list[tuple[str, str | None]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_refresh_one, acc): acc for acc in accounts}
        for fut in as_completed(futures):
            acc = futures[fut]
            try:
                err = fut.result()
            except Exception as exc:  # noqa: BLE001 - isolate per-account failures
                # 空メッセージの例外でも失敗として扱えるよう例外型名を併記する
                err = str(exc) or type(exc).__name__
            results.append((acc, err))
    return results
