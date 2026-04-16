import copy
import time
import random
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any

import requests
from requests.exceptions import Timeout, ConnectionError, HTTPError
from hw_genie.core.session_manager import SessionManager


def load_session_headers(account_alias: str | None = None) -> dict[str, str] | None:
    """SessionManager を使用して DB からヘッダー情報を読み込む"""
    account = account_alias or "default"
    data = SessionManager.load(account)
    return data.get("headers") if data else None


class ResponseStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    UNEXPECTED = "unexpected"
    LIMIT_REACHED = "limit_reached"
    STAMINA_ERROR = "stamina_error"
    AUTH_ERROR = "auth_error"
    SKIPPED = "skipped"


@dataclass
class PlayerStatus:
    name: str = "Unknown"
    level: int = 0
    gold: int = 0
    gems: int = 0
    energy: int = 0
    arena_rank: int = 0
    grand_rank: int = 0

    @property
    def max_energy(self) -> int:
        """レベルから上限を自動計算"""
        return int(self.level) + 60

    @property
    def energy_text(self) -> str:
        """表示用のエナジー文字列を生成"""
        return f"{self.energy} / {self.max_energy}"

    @classmethod
    def from_dict(cls, data: dict):
        """辞書データからインスタンスを生成"""
        # 既存データのキーに合わせてマッピング
        return cls(
            name=data.get("name", "Unknown"),
            level=int(data.get("level", 0)),
            gold=int(data.get("gold", 0)),
            gems=data.get("gems", data.get("starMoney", 0)),  # core.auth では gems ではなく starMoney
            energy=int(data.get("energy", 0)),
            arena_rank=int(data.get("arena_rank", data.get("arenaPlace", 0))),
            grand_rank=int(data.get("grand_rank", data.get("grandPlace", 0))),
        )

    def to_dict(self) -> dict:
        """JSON保存用に辞書形式へ変換"""
        return asdict(self)


class ApiAction(str, Enum):
    ARENA_GET_ALL = "arenaGetAll"
    MISSION_GET_ALL = "missionGetAll"
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


class Messages:
    AUTH_ERROR = "Session expired. Please update your curl or run auth_manager."


class HWAuthError(Exception):
    """認証エラー（セッション切れなど）を示す例外"""

    pass


class HWClient:
    """Hero Wars API の共通クライアント"""

    API_URL = "https://heroes-wb.nextersglobal.com/api/"
    DEFAULT_TIMEOUT = 15
    DEFAULT_SLEEP = 0.3
    MAX_RETRIES = 3
    RETRY_BACKOFF = 1.0  # Initial backoff in seconds

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
        一時的なネットワークエラーやレートリミットに対してはリトライを行う。

        Returns:
            HWResponse オブジェクト
        Raises:
            HWAuthError: 認証エラーが発生した場合
        """
        headers = self.get_headers()
        current_request_id = self.request_id

        attempt = 0
        while True:
            try:
                # actionTs の更新 (payload 内のすべての call context)
                if "calls" in payload:
                    for call_item in payload["calls"]:
                        if "context" in call_item:
                            call_item["context"]["actionTs"] = int(time.time())

                response = self.session.post(self.API_URL, headers=headers, json=payload, timeout=self.DEFAULT_TIMEOUT)

                # 0. Auth error check (HTTP 401)
                if response.status_code == 401:
                    raise HWAuthError(Messages.AUTH_ERROR)

                response.raise_for_status()
                res_data = response.json()

                # 1. Global error check
                if "error" in res_data:
                    error = res_data["error"]
                    error_name = error.get("name") if isinstance(error, dict) else str(error)

                    if error_name in ["auth", "InvalidSession"]:
                        raise HWAuthError(Messages.AUTH_ERROR)

                    return HWResponse(status=ResponseStatus.ERROR, error_name=error_name, detail=error, request_id=current_request_id)

                # 2. Call-level response check
                if "results" in res_data and len(res_data["results"]) > 0:
                    results = res_data["results"]

                    # 単一コールの場合は従来通り最初の結果を返す (後方互換性)
                    if len(results) == 1:
                        call_result = results[0]
                        if "error" in call_result:
                            error = call_result["error"]
                            error_name = error.get("name") if isinstance(error, dict) else str(error)

                            if error_name in ["auth", "InvalidSession"]:
                                raise HWAuthError(Messages.AUTH_ERROR)

                            return HWResponse(status=ResponseStatus.ERROR, error_name=error_name, detail=error, request_id=current_request_id)
                        elif "result" in call_result:
                            return HWResponse(status=ResponseStatus.SUCCESS, detail=call_result["result"], request_id=current_request_id)
                        else:
                            return HWResponse(
                                status=ResponseStatus.UNEXPECTED, error_name="unknown_format", detail=call_result, request_id=current_request_id
                            )

                    # 複数コールの場合は ident をキーにした辞書を返す
                    results_map = {}
                    for i, call_result in enumerate(results):
                        # ident がない場合はインデックスを使用
                        # 注意: payload の calls リストと results リストの順序は一致している
                        # ここでは payload を辿って ident を取得するのが確実だが、
                        # 簡易的に results 内に ident が含まれているか確認 (API 仕様に依存)
                        # もし含まれていない場合は、呼び出し側で index 指定で取得してもらう
                        ident = call_result.get("ident", str(i))
                        results_map[ident] = call_result

                    return HWResponse(status=ResponseStatus.SUCCESS, detail=results_map, request_id=current_request_id)
                else:
                    return HWResponse(status=ResponseStatus.UNEXPECTED, error_name="empty_results", detail=res_data, request_id=current_request_id)

            except HWAuthError:
                raise
            except (Timeout, ConnectionError, HTTPError) as e:
                # HTTPError の場合、リトライすべきステータスコードか確認
                if isinstance(e, HTTPError):
                    status_code = e.response.status_code if e.response is not None else None
                    # 429 (Too Many Requests) または 5xx (Server Error) はリトライ
                    if status_code != 429 and (status_code is None or not (500 <= status_code < 600)):
                        return HWResponse(
                            status=ResponseStatus.UNEXPECTED, error_name="network_or_parse_error", detail=str(e), request_id=current_request_id
                        )

                attempt += 1
                if attempt > self.MAX_RETRIES:
                    return HWResponse(
                        status=ResponseStatus.UNEXPECTED, error_name="network_or_parse_error", detail=str(e), request_id=current_request_id
                    )

                # Exponential backoff with jitter
                sleep_time = (self.RETRY_BACKOFF * (2 ** (attempt - 1))) + (random.random() * 0.1)
                time.sleep(sleep_time)

                # リトライ時は request-id を更新して新しいリクエストとして送る
                headers = self.get_headers()
                current_request_id = self.request_id

            except Exception as e:
                return HWResponse(status=ResponseStatus.UNEXPECTED, error_name="network_or_parse_error", detail=str(e), request_id=current_request_id)

    def mission_get_all(self) -> HWResponse:
        """キャンペーン（ストーリーモード）の各ステージクリア状況を取得"""
        payload = {"calls": [{"name": ApiAction.MISSION_GET_ALL, "args": {}, "ident": "body"}]}
        return self.call(payload)

    def build_mission_payload(self, mission_id: int, times: int = 3) -> dict[str, Any]:
        """ミッションレイド用ペイロードを生成"""
        if not (1 <= times <= 3):
            raise ValueError(f"times must be between 1 and 3, but got {times}")
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

    def fetch_player_status(self) -> PlayerStatus:
        """
        現在のプレイヤー情報（名前、レベル、リソース、アリーナ順位）を取得して辞書で返す。
        失敗した項目は None または 0 が入る。
        """
        # ユーザー情報とアリーナ情報を1回のリクエストでまとめて取得 (通信効率化)
        payload = {
            "calls": [
                {"name": ApiAction.USER_GET_INFO, "args": {}, "ident": "user"},
                {"name": ApiAction.ARENA_GET_ALL, "args": {}, "ident": "arena"},
            ]
        }
        res = self.call(payload)

        user_data = {}
        arena_data = {}

        if res.is_success and isinstance(res.detail, dict):
            # ident による結果の抽出
            user_res = res.detail.get("user", {})
            arena_res = res.detail.get("arena", {})

            user_data = user_res.get("result", {}).get("response", {}) if "result" in user_res else {}
            arena_data = arena_res.get("result", {}).get("response", {}) if "result" in arena_res else {}

        # データの抽出
        name = user_data.get("name", "Unknown")
        level = user_data.get("level", 0)
        gold = user_data.get("gold", 0)
        gems = user_data.get("starMoney", 0)

        energy = 0
        if "refillable" in user_data:
            for item in user_data["refillable"]:
                if item.get("id") == 1:
                    energy = item.get("amount", 0)
                    break

        arena_rank = arena_data.get("arenaPlace", 0)
        grand_rank = arena_data.get("grandPlace", 0)

        return PlayerStatus(
            name=name,
            level=level,
            gold=gold,
            gems=gems,
            energy=energy,
            arena_rank=arena_rank,
            grand_rank=grand_rank,
        )

    def sleep(self) -> None:
        """APIリクエスト間のインターバル（レートリミット回避）"""
        time.sleep(self.DEFAULT_SLEEP)
