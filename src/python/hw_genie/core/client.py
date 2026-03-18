import copy
import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import requests


def load_session_headers() -> dict[str, str] | None:
    """session.jsonからヘッダー情報を読み込む"""
    # 探索順序:
    # 1. カレントディレクトリの session.json
    # 2. パッケージインストール先 (HW-Genie/src/python/hw_genie/core/../../../session.json)
    # 3. ユーザーホームの .hw-genie/session.json (将来用)

    search_paths = [
        "session.json",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "session.json")),
        os.path.expanduser("~/.hw-genie/session.json"),
    ]

    for session_path in search_paths:
        if os.path.exists(session_path):
            try:
                with open(session_path, "r") as f:
                    data = json.load(f)
                    return data.get("headers")
            except Exception:
                pass
    return None


class ResponseStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    UNEXPECTED = "unexpected"
    LIMIT_REACHED = "limit_reached"
    STAMINA_ERROR = "stamina_error"
    AUTH_ERROR = "auth_error"


class ApiAction(str, Enum):
    MISSION_RAID = "missionRaid"
    CONSUMABLE_USE_STAMINA = "consumableUseStamina"
    INVENTORY_EXCHANGE_STONES = "inventoryExchangeStones"
    SHOP_GET_ALL = "shopGetAll"
    SHOP_BUY = "shopBuy"
    USER_GET_INFO = "userGetInfo"


class ErrorName(str, Enum):
    NOT_ENOUGH_STAMINA = "notEnoughStamina"
    LIMIT_REACHED = "limitReached"
    NOT_ENOUGH = "NotEnough"


@dataclass
class ExchangeInfo:
    stones: int


@dataclass
class HWResponse:
    status: ResponseStatus
    detail: Any = None
    error_name: str | None = None
    request_id: int | None = None
    exchange_info: ExchangeInfo | None = None

    @property
    def is_success(self) -> bool:
        return self.status == ResponseStatus.SUCCESS


class Emojis:
    SUCCESS = "✅ "
    ERROR = "❌ "
    WARNING = "⚠️  "
    INFO = "ℹ️  "
    START = "🚀 "
    FINISH = "🏁 "
    STEP = "🔹 "
    RECOVERY = "⚡️ "
    SOUL_STONE = "💎 "
    AUTH_MSG = "Session expired. Please update your curl or run auth_manager."


class HWClient:
    """Hero Wars API の共通クライアント"""

    API_URL = "https://heroes-wb.nextersglobal.com/api/"
    DEFAULT_TIMEOUT = 15
    DEFAULT_SLEEP = 0.5

    def __init__(self, headers: dict[str, str], session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.headers = dict(headers)
        self.request_id = int(headers.get("x-request-id", 100))

    def get_headers(self) -> dict[str, str]:
        """現在の request-id を反映したヘッダーを取得"""
        self.request_id += 1
        headers = self.headers.copy()
        headers["x-request-id"] = str(self.request_id)
        return headers

    def call(self, payload: dict[str, Any]) -> HWResponse:
        """
        APIを呼び出し、統一されたエラーハンドリングを行う。

        Returns:
            HWResponse オブジェクト
        """
        headers = self.get_headers()
        current_request_id = self.request_id

        try:
            # actionTs の更新 (payload 内のすべての call context)
            if "calls" in payload:
                for call_item in payload["calls"]:
                    if "context" in call_item:
                        call_item["context"]["actionTs"] = int(time.time())

            response = self.session.post(self.API_URL, headers=headers, json=payload, timeout=self.DEFAULT_TIMEOUT)

            # 0. Auth error check (HTTP 401)
            if response.status_code == 401:
                return HWResponse(status=ResponseStatus.AUTH_ERROR, error_name="auth", request_id=current_request_id)

            response.raise_for_status()
            res_data = response.json()

            # 1. Global error check
            if "error" in res_data:
                error = res_data["error"]
                error_name = error.get("name") if isinstance(error, dict) else str(error)

                status = ResponseStatus.AUTH_ERROR if error_name in ["auth", "InvalidSession"] else ResponseStatus.ERROR
                return HWResponse(status=status, error_name=error_name, detail=error, request_id=current_request_id)

            # 2. Call-level response check
            if "results" in res_data and len(res_data["results"]) > 0:
                call_result = res_data["results"][0]

                if "error" in call_result:
                    error = call_result["error"]
                    error_name = error.get("name") if isinstance(error, dict) else str(error)

                    status = ResponseStatus.AUTH_ERROR if error_name in ["auth", "InvalidSession"] else ResponseStatus.ERROR
                    return HWResponse(status=status, error_name=error_name, detail=error, request_id=current_request_id)
                elif "result" in call_result:
                    return HWResponse(status=ResponseStatus.SUCCESS, detail=call_result["result"], request_id=current_request_id)
                else:
                    return HWResponse(
                        status=ResponseStatus.UNEXPECTED, error_name="unknown_format", detail=call_result, request_id=current_request_id
                    )
            else:
                return HWResponse(status=ResponseStatus.UNEXPECTED, error_name="empty_results", detail=res_data, request_id=current_request_id)

        except Exception as e:
            return HWResponse(status=ResponseStatus.UNEXPECTED, error_name="network_or_parse_error", detail=str(e), request_id=current_request_id)

    def build_mission_payload(self, mission_id: int, times: int = 3) -> dict[str, Any]:
        """ミッションレイド用ペイロードを生成"""
        return {"calls": [{"name": ApiAction.MISSION_RAID, "args": {"id": mission_id, "times": times}, "context": {"actionTs": 0}, "ident": "body"}]}

    def prepare_item_payload(self, payload_template: dict[str, Any]) -> dict[str, Any]:
        """アイテムレイド用ペイロードをディープコピー（actionTsの更新はcall()で行うためコピーのみ）"""
        return copy.deepcopy(payload_template)

    def recover_stamina(self, lib_id: int = 17, amount: int = 1) -> HWResponse:
        """スタミナポーションを使用してスタミナを回復する"""
        payload = {
            "calls": [
                {
                    "name": ApiAction.CONSUMABLE_USE_STAMINA,
                    "args": {"libId": lib_id, "amount": amount},
                    "context": {"actionTs": 0},
                    "ident": "stamina_recovery",
                }
            ]
        }
        return self.call(payload)

    def exchange_stones(self) -> HWResponse:
        """ソウルストーンを換金する。結果にストーン数情報を含めて返す。

        Returns:
            HWResponse オブジェクト (成功時は exchange_info に ExchangeInfo が入る)
        """
        payload = {"calls": [{"name": ApiAction.INVENTORY_EXCHANGE_STONES, "args": {}, "ident": "exchange_stones"}]}
        res = self.call(payload)

        if res.is_success:
            try:
                # 換金されたストーンの情報を抽出 (response -> cost -> fragmentHero)
                response = res.detail.get("response", {}) if res.detail else {}
                cost = response.get("cost", {})
                fragment_hero = cost.get("fragmentHero", {})

                # 複数のヒーローIDが含まれる場合でも、すべての個数の総和を計算
                total_stones = sum(fragment_hero.values()) if isinstance(fragment_hero, dict) else 0
                res.exchange_info = ExchangeInfo(stones=total_stones)
            except Exception:
                # 解析に失敗した場合は 0 stones として扱う
                res.exchange_info = ExchangeInfo(stones=0)

        return res

    def sleep(self) -> None:
        """APIリクエスト間のインターバル（レートリミット回避）"""
        time.sleep(self.DEFAULT_SLEEP)
