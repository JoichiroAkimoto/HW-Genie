import copy
import time
import random
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, ClassVar

import requests
from requests.exceptions import Timeout, ConnectionError, HTTPError
from hw_genie.core.session_manager import SessionManager
from hw_genie.core.utils import max_energy_for_level


def _safe_int(value: Any, default: int = 0) -> int:
    """Convert value to int safely, returning default on failure."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def resolve_account(account_alias: str | None = None) -> str:
    """Resolve the effective account alias for a command.

    An explicit alias is used as-is when it exactly matches a registered
    alias. When the casing or surrounding whitespace differs, the canonical
    (registered) alias is returned so downstream ``update_config_merged``
    calls do not fail with ``Account not found`` (see run_logs failed cases
    for ``champion`` / ``Champion␣``). When ``None`` (no ``--account`` given)
    the behaviour is:

    - exactly one registered account -> that account is used automatically;
    - multiple registered accounts -> raise ``AccountAmbiguityError`` asking for
      an explicit ``--account``;
    - no registered accounts -> raise ``AccountNotFoundError``.

    An alias that is blank after stripping (e.g. ``"   "``) is treated as no
    account at all and raises ``AccountNotFoundError`` as well.

    The ``default`` pseudo-alias is gone: every account is stored under its real
    player name (or an explicitly chosen alias), so nothing falls back to a
    literal ``"default"`` row.
    """
    if account_alias:
        stripped = account_alias.strip()
        # 空白のみの入力は None 指定と同じ扱いにする: このまま通すと
        # ``Account not found`` / 空文字エイリアスの新規行作成につながる。
        if not stripped:
            # None 指定時と同じ ``AccountNotFoundError``
            raise AccountNotFoundError(f"Account alias is blank after trimming ({account_alias!r}). Pass an explicit --account.")
        accounts = SessionManager.list_accounts()
        if stripped in accounts:
            return stripped
        # case-insensitive / whitespace-insensitive fallback to canonical alias
        match = next((a for a in accounts if a.strip().lower() == stripped.lower()), None)
        if match:
            return match
        return stripped
    accounts = SessionManager.list_accounts()
    if len(accounts) == 1:
        return accounts[0]
    if len(accounts) > 1:
        raise AccountAmbiguityError(accounts)
    raise AccountNotFoundError("No accounts found in database. Register one with `auth --curl` first.")


class AccountResolutionError(Exception):
    """Raised when the effective account cannot be resolved for a command."""


class AccountNotFoundError(AccountResolutionError):
    """Raised when no account exists (or the named account is not registered)."""


class AccountAmbiguityError(AccountResolutionError):
    """Raised when ``--account`` is omitted but multiple accounts exist."""

    def __init__(self, accounts: list[str]):
        self.accounts = accounts
        super().__init__(f"Multiple accounts registered ({', '.join(accounts)}). Specify one with --account <name>.")


def load_session_headers(account_alias: str | None = None) -> dict[str, str] | None:
    """SessionManager を使用して DB からヘッダー情報を読み込む"""
    account = resolve_account(account_alias)
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
    UNKNOWN_NAME: ClassVar[str] = "Unknown"

    id: str = "Unknown"
    name: str = "Unknown"
    level: int = 0
    gold: int = 0
    gems: int = 0
    energy: int = 0
    arena_rank: int = 0
    grand_rank: int = 0
    timezone: int = 0
    next_day_ts: int = 0

    @property
    def is_valid(self) -> bool:
        """ステータス情報が正常に取得できているか判定。

        名前がデフォルト値（``Unknown``）でなく、かつレベルが 1 以上の場合にのみ
        ``True`` を返す。どちらか一方でも不正値の場合は ``False``。
        """
        return self.name != self.UNKNOWN_NAME and self.level > 0

    @property
    def max_energy(self) -> int:
        """レベルから上限を自動計算"""
        return max_energy_for_level(self.level)

    @property
    def energy_text(self) -> str:
        """表示用のエナジー文字列を生成"""
        return f"{self.energy} / {self.max_energy}"

    @classmethod
    def from_dict(cls, data: dict):
        """辞書データからインスタンスを生成"""
        # 既存データのキーに合わせてマッピング
        gems_raw = data.get("gems")
        if gems_raw is None:
            gems_raw = data.get("starMoney")
        return cls(
            id=data.get("id", "Unknown"),
            name=data.get("name", "Unknown"),
            level=_safe_int(data.get("level")),
            gold=_safe_int(data.get("gold")),
            gems=_safe_int(gems_raw),  # core.auth では gems ではなく starMoney
            energy=_safe_int(data.get("energy")),
            arena_rank=_safe_int(data.get("arena_rank", data.get("arenaPlace"))),
            grand_rank=_safe_int(data.get("grand_rank", data.get("grandPlace"))),
            timezone=_safe_int(data.get("timezone", data.get("timeZone"))),
            next_day_ts=_safe_int(data.get("next_day_ts", data.get("nextDayTs"))),
        )

    def to_dict(self) -> dict:
        """JSON保存用に辞書形式へ変換"""
        return asdict(self)


class ApiAction(str, Enum):
    ARENA_GET_ALL = "arenaGetAll"
    MISSION_GET_ALL = "missionGetAll"
    MISSION_RAID = "missionRaid"
    QUEST_GET_ALL = "questGetAll"
    QUEST_FARM = "questFarm"
    GACHA_OPEN = "gacha_open"
    HERO_ARTIFACT_LEVEL_UP = "heroArtifactLevelUp"
    TITAN_ARTIFACT_LEVEL_UP = "titanArtifactLevelUp"
    HERO_SKIN_UPGRADE = "heroSkinUpgrade"
    HERO_TITAN_GIFT_LEVEL_UP = "heroTitanGiftLevelUp"
    HERO_TITAN_GIFT_DROP = "heroTitanGiftDrop"
    CONSUMABLE_USE_STAMINA = "consumableUseStamina"
    INVENTORY_GET = "inventoryGet"
    INVENTORY_EXCHANGE_STONES = "inventoryExchangeStones"
    SHOP_GET_ALL = "shopGetAll"
    SHOP_BUY = "shopBuy"
    USER_GET_INFO = "userGetInfo"
    CLAN_RAID_GET_INFO = "clanRaid_getInfo"
    CLAN_RAID_SHOP_BUY = "clanRaid_shopBuy"
    CHAT_GET_ALL = "chatGetAll"
    # TODO: chatServerSubscribe は現在未使用（将来的なリアルタイム購読用に予約）。不要になれば削除を検討。
    CHAT_SERVER_SUBSCRIBE = "chatServerSubscribe"


class ErrorName(str, Enum):
    NOT_ENOUGH_STAMINA = "notEnoughStamina"
    LIMIT_REACHED = "limitReached"
    NOT_ENOUGH = "NotEnough"
    NOT_FOUND = "NotFound"
    NOT_AVAILABLE = "NotAvailable"


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
    AUTH_ERROR = "Session expired or invalid signature. Please update your curl or run auth-server."


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
                # actionTs の更新 (payload 内のすべての call context および params)
                now = int(time.time())
                if "calls" in payload:
                    for call_item in payload["calls"]:
                        if "context" in call_item:
                            call_item["context"]["actionTs"] = now
                        # stashClient などの内部データに含まれる actionTs も更新を試みる
                        if "args" in call_item and isinstance(call_item["args"], dict) and "data" in call_item["args"]:
                            for data_item in call_item["args"]["data"]:
                                if isinstance(data_item, dict) and "params" in data_item and "actionTs" in data_item["params"]:
                                    data_item["params"]["actionTs"] = now

                import logging
                import json

                logging.debug(f"Payload: {json.dumps(payload)}")
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
                    response_text = e.response.text if e.response is not None else ""
                    # 429 (Too Many Requests) または 5xx (Server Error) はリトライ
                    if status_code != 429 and (status_code is None or not (500 <= status_code < 600)):
                        detail = f"{str(e)} | Response: {response_text[:500]}"
                        return HWResponse(
                            status=ResponseStatus.UNEXPECTED, error_name="network_or_parse_error", detail=detail, request_id=current_request_id
                        )

                attempt += 1
                if attempt > self.MAX_RETRIES:
                    response_text = ""
                    if isinstance(e, HTTPError) and e.response is not None:
                        response_text = f" | Response: {e.response.text[:500]}"

                    detail = f"{str(e)}{response_text}"
                    return HWResponse(
                        status=ResponseStatus.UNEXPECTED, error_name="network_or_parse_error", detail=detail, request_id=current_request_id
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

    def quest_get_all(self) -> HWResponse:
        """全クエスト（デイリー/メイン/ギルド/イベント/バトルパス等）の進捗を取得"""
        payload = {"calls": [{"name": ApiAction.QUEST_GET_ALL, "args": {}, "ident": "body"}]}
        return self.call(payload)

    def quest_farm(self, quest_id: int) -> HWResponse:
        """クエストの報酬を受け取る（条件達成済み state=2 のもののみ有効）"""
        payload = {
            "calls": [
                {
                    "name": ApiAction.QUEST_FARM,
                    "args": {"questId": quest_id},
                    "ident": "body",
                }
            ]
        }
        return self.call(payload)

    def quest_operation(self, action: ApiAction, args: dict[str, Any]) -> HWResponse:
        """デイリークエストを進めるゲーム操作（強化/召喚/購入等）を実行"""
        payload = {"calls": [{"name": action, "args": args, "ident": "body"}]}
        return self.call(payload)

    def chat_get_all(
        self,
        chat_type: str = "clan",
        count: int = 50,
        last_id: str | None = None,
    ) -> HWResponse:
        """チャット履歴を取得する（chatGetAll）。

        Args:
            chat_type: チャット種別（``clan``/``training``/``xgvg``/``server``）。
            count: 取得件数（1-200 にクランプ、既定 50）。
            last_id: ページネーション用（指定 ID 以前のメッセージを取得。CLI では ``--last-id`` で指定）。
        """
        # count は 1-200 にクランプ（API 負荷とレスポンスサイズを制限）。ValueError 等はデフォルトにフォールバック。
        try:
            count_int = int(count) if count is not None else 50
        except (TypeError, ValueError):
            count_int = 50
        count_int = max(1, min(200, count_int))
        args: dict[str, Any] = {"chatType": chat_type, "count": count_int}
        if last_id is not None:
            args["lastId"] = str(last_id)
        payload = {
            "calls": [{"name": ApiAction.CHAT_GET_ALL, "args": args, "ident": "body"}]
        }
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
        level = _safe_int(user_data.get("level"))
        gold = _safe_int(user_data.get("gold"))
        gems = _safe_int(user_data.get("starMoney"))

        energy = 0
        if "refillable" in user_data:
            for item in user_data["refillable"]:
                if item.get("id") == 1:
                    energy = _safe_int(item.get("amount"))
                    break

        arena_rank = _safe_int(arena_data.get("arenaPlace"))
        grand_rank = _safe_int(arena_data.get("grandPlace"))

        return PlayerStatus(
            name=name,
            level=level,
            gold=gold,
            gems=gems,
            energy=energy,
            arena_rank=arena_rank,
            grand_rank=grand_rank,
            timezone=_safe_int(user_data.get("timeZone")),
            next_day_ts=_safe_int(user_data.get("nextDayTs")),
        )

    def sleep(self) -> None:
        """APIリクエスト間のインターバル（レートリミット回避）"""
        time.sleep(self.DEFAULT_SLEEP)
