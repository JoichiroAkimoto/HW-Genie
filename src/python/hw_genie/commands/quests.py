"""クエスト（特にデイリー）の取得・表示。

Hero Wars の ``questGetAll`` レスポンスにはクエスト名・カテゴリ・目標値が
含まれないため、:data:`QUEST_MASTER`（ゲーム UI との照合で確定した
ID → 名称/カテゴリ/目標値）を正引きテーブルとして使う。
未確定の ID は ID ファミリの規則でカテゴリだけ推定する。
"""

import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from hw_genie.core.client import (
    ApiAction,
    ErrorName,
    HWClient,
    HWResponse,
    ResponseStatus,
    resolve_account,
)
from hw_genie.core.session_manager import SessionManager
from hw_genie.core.shop import ShopInventory, ShopNotFoundError, get_shop_slots, is_bought
from hw_genie.core.utils import format_timestamp_for_display

logger = logging.getLogger(__name__)

# --- クエスト状態 ---
# 1 = 進行中, 2 = 条件達成済み・報酬受取可能（questFarm で受領できる）,
# 3 = 報酬受領済み（questGetAll には通常現れず、questFarm 応答の quests 配列で見られる）
STATE_ACTIVE = 1
STATE_CLAIMABLE = 2
STATE_DONE = 3

# --- カテゴリ定義 ---
CATEGORIES = ["daily", "weekly", "guild", "main", "event", "battlepass", "one_time", "unknown"]

CATEGORY_LABELS: dict[str, str] = {
    "daily": "Daily Quests",
    "weekly": "Weekly Quests",
    "guild": "Guild Quests",
    "main": "Main Quests",
    "event": "Event Quests",
    "battlepass": "Battle Pass / Season",
    "one_time": "One-time Quests",
    "unknown": "Unclassified",
}

# --- マスタデータ（ID はアカウント共通） ---
# 名称・目標値はゲーム UI との照合で確定したもののみ記載。
# questGetAll のレスポンスには含まれないため、ここが唯一の正引きテーブル。
QUEST_MASTER: dict[int, dict[str, Any]] = {
    # デイリー（Daily タブ）
    10004: {
        "category": "daily",
        "name": "Fight 3 times in the Arena or Grand Arena",
        "target": 3,
    },
    10006: {
        "category": "daily",
        "name": "Use emerald exchange",
        "target": 1,
    },
    10007: {
        "category": "daily",
        "name": "Perform 1 summon in the Soul Atrium",
        "target": 1,
    },
    10024: {
        "category": "daily",
        "name": "Level up any Hero's Artifact 1 time",
        "target": 1,
    },
    10028: {
        "category": "daily",
        "name": "Level up any Titan Artifact",
        "target": 1,
    },
    10030: {
        "category": "daily",
        "name": "Upgrade any hero's skin 1 time",
        "target": 1,
    },
    10050: {
        "category": "daily",
        "name": "Earn 1750 Guild Activity points",
        "target": 1750,
    },
    10033: {
        "category": "unknown",
        "name": "要確認（dungeonActivity 報酬、Daily タブ非表示）",
        "target": None,
    },
    10023: {
        "category": "daily",
        "name": "要確認（heroTitanGift 系クエスト。Daily タブの名前は未確定）",
        "target": None,
    },
}

# ID ファミリごとのカテゴリ推定規則（マスタ未登録 ID 向け）
# 先頭が "100" の ID はデイリー（既知: 10004〜10050）。判定は _FAMILY_RULES の
# 先頭一致なので、より長い prefix を持つ規則（"2000" 等）を先に置くこと。
_FAMILY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("2000", "2001"), "guild"),
    (("110",), "weekly"),
    (("232",), "main"),
    (("398",), "event"),
    (("26", "27"), "battlepass"),
    (("100",), "daily"),
)

# --- クリア条件マップ（クエストID → クリアするための操作レシピ） ---
# この定義は「実行方法のレシピ」と「初期値」を提供するだけ。実際に実行するか
# どうかは account_configs の ``quest_defaults``（config_key="quest_defaults"）の
# ``enabled`` フラグでアカウントごとに制御する。
#   - ``quest_defaults[quest_id]`` の ``enabled == true`` のときのみ実行
#   - 空 / 未設定 / false の場合は実行しない（初期状態は全て無効）
# ``steps`` の各要素は ``{"rpc": ApiAction, "args": {...}}``。引数はアカウント
# 固有の ``quest_defaults`` 値で上書きできる（キーが既に存在する場合のみ）。
# ※ 未登録のデイリー（10004 アリーナ戦闘、10006 エメラルド交換 等）は
#   クリア操作の API が確定した時点で追記する。
QUEST_OPERATIONS: dict[int, dict[str, Any]] = {
    # 10007: Soul Atrium 召喚は消費が大きいためデフォルト無効
    10007: {
        "enabled": False,
        "steps": [
            {"rpc": ApiAction.GACHA_OPEN, "args": {"ident": "heroGacha", "free": True, "pack": False}},
        ],
    },
    10024: {
        "enabled": False,
        "steps": [
            {"rpc": ApiAction.HERO_ARTIFACT_LEVEL_UP, "args": {"heroId": 61, "slotId": 1}},
        ],
    },
    10028: {
        "enabled": False,
        "steps": [
            # Elemental Tournament Shop でフラグメント 200 個購入 → レベルアップ
            # slot の reward/cost は実行時に shopGetAll の実在庫から動的解決される
            # （_resolve_shop_buy_reward）。この静的な cost/reward は在庫取得失敗時
            # のみのフォールバック（通常は到達しない）。指定 shop/slot が在庫に
            # 無い場合や購入済み（bought）の slot の場合は実行前に失敗報告
            # される。フラグメント ID は「タイタンアーティファクトの強化素材」として
            # 共通であり、購入後は quest_defaults で指定した titanId/slotId の
            # 対象に使う（docs/superpowers/titan-quests-ops.md 参照）。
            {"rpc": ApiAction.SHOP_BUY, "args": {"shopId": 13, "slot": 18, "cost": {"coin": {"18": 12}}, "reward": {"fragmentTitanArtifact": {"2001": 1}}, "amount": 200}},
            {"rpc": ApiAction.TITAN_ARTIFACT_LEVEL_UP, "args": {"titanId": 4012, "slotId": 1}},
        ],
    },
    10030: {
        "enabled": False,
        "steps": [
            {"rpc": ApiAction.HERO_SKIN_UPGRADE, "args": {"heroId": 59, "skinId": 313}},
        ],
    },
    10023: {
        "enabled": False,
        "steps": [
            {"rpc": ApiAction.HERO_TITAN_GIFT_LEVEL_UP, "args": {"heroId": 38}},
            {"rpc": ApiAction.HERO_TITAN_GIFT_LEVEL_UP, "args": {"heroId": 38}},
            {"rpc": ApiAction.HERO_TITAN_GIFT_DROP, "args": {"heroId": 38}},
        ],
    },
}

# quest_defaults の設定キー
QUEST_DEFAULTS_KEY = "quest_defaults"

# ギルドクエスト（「Obtain xxx Sparks of Power」等、ID がアカウント・日次で
# 動的な 2000xxxx/2001xxxx ファミリ）専用の実行制御設定キー。
# ・enabled      … true のときのみギルドクエスト達成用の操作（heroTitanGift
#                  LevelUp ×2 → Drop）を実行する。false / 未設定なら active
#                  （state=1）のギルドクエストは操作せずスキップする
#                  （claimable＝state=2 の報酬受領は enabled に関係なく常時行う）。
# ・heroId     … ギフト操作の対象ヒーロー（既定は QUEST_OPERATIONS[10023] と同一。
#                 優先度は quest_defaults[10023].heroId が先、未設定なら本設定）。
# ・last_recipe_at … 最後にギルドレシピを実行した Unix 秒。userGetInfo の
#                 nextDayTs から求めた今日のサイクル開始時刻（_guild_cycle_boundary）
#                 以上なら「今日は実行済み」とみなしてスキップする（1 日 1 回ガード）。
#                 ギルドクエストはギルド全体の累積ポイントで達成するため進捗には
#                 依存せず、毎日 1 セット（LevelUp ×2 → Drop）を確実に実行する。
#                 デイリー 10023（同一レシピ）の成功も last_recipe_at に記録される
#                 （Gift 資源の二重消費防止）。
# ・note       … 操作 RPC 名の連結メモ（可読性専用）
QUEST_GUILD_DEFAULTS_KEY = "quest_guild_defaults"

# ギルドクエスト達成レシピのテンプレート（10023 = heroTitanGift 3 連続操作）
GUILD_QUEST_RECIPE_ID = 10023

# quest_defaults 内で「操作引数ではない」キー（実行制御フラグ / 可読性メモ）
# ・enabled     … 実行可否フラグ（true のときのみ操作を実行）
# ・note        … 操作 RPC 名の連結メモ（DB JSON の人間可読性専用）
# ・candidates  … 失敗時のフォールバック候補（優先度順の dict リスト。ステップの
#                 args を候補 key/value で上書きして再実行する）
# ・last_recipe_at … ギルドレシピの 1 日 1 回実行ガード用の最終実行時刻
#                 （quest_guild_defaults。旧 recipe_runs 方式から移行）
# ・max_recipes / recipe_runs … 旧方式（サイクル内回数上限）の残骸キー。
#                 互換のため NON_ARG_KEYS に残すが新コードからは参照されない。
# これらは操作ステップの args としては使用されず、未知キー警告の対象外でもある。
NON_ARG_KEYS = ("enabled", "note", "candidates", "last_recipe_at", "max_recipes", "recipe_runs")


def classify_quest(qid: int) -> tuple[str, str]:
    """``(category, display_name)`` を返す。マスタ優先、未登録は ID ファミリ規則。"""
    master = QUEST_MASTER.get(qid)
    if master:
        return master["category"], master["name"]
    s = str(qid)
    for prefixes, category in _FAMILY_RULES:
        if s.startswith(prefixes):
            return category, f"{CATEGORY_LABELS[category]} (未命名)"
    return "one_time", "One-time Quest (未命名)"


def _to_int(value: Any) -> int | None:
    """int/str 混在の値（id, progress 等）を int に正規化する。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def is_guild_quest(qid: int) -> bool:
    """ギルドクエスト（2000xxxx/2001xxxx ファミリ）かどうか。

    ギルドクエスト（ゲーム側の名称は「Obtain xxx Sparks of Power」等）は
    ID がアカウント・日次で変わるため、QUEST_OPERATIONS のように固定 ID で
    は登録できず、ファミリ判定で捕捉する。category は ``classify_quest`` の
    _FAMILY_RULES（"2000"/"2001" → guild）に由来する。
    """
    return classify_quest(qid)[0] == "guild"


@dataclass
class Quest:
    id: int
    state: int = STATE_ACTIVE
    progress: int = 0
    reward: dict[str, Any] | None = None
    create_time: int = 0
    farm_count: int = 0
    order: int | None = None
    category: str = "unknown"
    name: str = ""
    target: int | None = None

    @property
    def is_claimable(self) -> bool:
        """報酬受取可能（条件達成済み・未受領）かどうか。"""
        return self.state == STATE_CLAIMABLE

    @property
    def is_done(self) -> bool:
        """報酬受領済みかどうか。"""
        return self.state == STATE_DONE


def parse_quests(raw: Any) -> list[Quest]:
    """``questGetAll`` のレスポンス配列を :class:`Quest` のリストに正規化する。"""
    if not isinstance(raw, list):
        logger.warning("questGetAll response is not a list: %r", type(raw))
        return []
    quests: list[Quest] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = _to_int(item.get("id"))
        if qid is None:
            continue
        category, name = classify_quest(qid)
        quests.append(
            Quest(
                id=qid,
                state=_to_int(item.get("state")) or STATE_ACTIVE,
                progress=_to_int(item.get("progress")) or 0,
                reward=item.get("reward") or {},
                create_time=_to_int(item.get("createTime")) or 0,
                farm_count=_to_int(item.get("farmCount")) or 0,
                order=_to_int(item.get("order")),
                category=category,
                name=name,
                target=QUEST_MASTER.get(qid, {}).get("target"),
            )
        )
    return quests


def format_reward(reward: dict[str, Any] | None) -> str:
    """報酬 dict を簡潔な文字列（``starmoney 50`` / ``consumable[56×1] + gold 6400``）にする。"""
    if not reward:
        return "-"
    parts = []
    for key, value in reward.items():
        if isinstance(value, dict):
            sub = ", ".join(f"{k}×{v}" for k, v in value.items())
            parts.append(f"{key}[{sub}]")
        else:
            parts.append(f"{key} {value}")
    return " + ".join(parts)


def format_create_time(ts: int) -> str:
    """エポック秒を表示タイムゾーン（HWGENIE_TZ、既定 UTC）の ISO に変換して返す。"""
    if not ts:
        return "-"
    iso = datetime.fromtimestamp(ts, timezone.utc).isoformat()
    return format_timestamp_for_display(iso)


def run_quest_status(
    client: HWClient,
    account_alias: str | None = None,
    show_all: bool = False,
    raw: bool = False,
    category: str | None = None,
) -> list[Quest]:
    """クエスト一覧を取得し、未完了（state!=3）のクエストを中心に表示する。

    受領済み（state=3）は questGetAll に通常現れないが、保険で除外する。
    条件達成済み・未受領（state=2）は「未完了」として 🎁 マークで表示する。

    Returns:
        取得・パース済みの全 :class:`Quest` リスト。
    """
    account = resolve_account(account_alias)
    res = client.quest_get_all()
    if res.status != ResponseStatus.SUCCESS:
        print(f"❌ Failed to fetch quests: {res.status.value} ({res.error_name or '-'})")
        return []

    raw_data = res.detail.get("response") if isinstance(res.detail, dict) else None
    if raw_data is None:
        print("❌ Unexpected questGetAll response format.")
        return []
    quests = parse_quests(raw_data)
    if not quests:
        print("ℹ️  No quests found.")
        return []

    if raw:
        print(json.dumps(raw_data, ensure_ascii=False, indent=1))
        return quests

    # カテゴリ・フィルタ適用
    if category:
        quests = [q for q in quests if q.category == category]
        if not quests:
            print(f"ℹ️  No quests in category '{category}'.")
            return []

    # state==3（受領済み）は questGetAll に通常現れないが、保険で除外
    visible = quests if show_all else [q for q in quests if not q.is_done]

    if show_all:
        done = sum(1 for q in quests if q.is_done)
        claimable = sum(1 for q in quests if q.is_claimable)
        active = len(quests) - done - claimable
        print(f"\n📋 Quest status for {account} "
              f"(total {len(quests)}, {claimable} claimable, {active} active, {done} done):")
    else:
        print(f"\n📋 Quest status for {account} "
              f"(total {len(quests)}, uncompleted {len(visible)}):")

    shown_any = False
    for cat in CATEGORIES:
        items = [q for q in visible if q.category == cat]
        if not items:
            continue
        shown_any = True
        claimable = sum(1 for q in items if q.is_claimable)
        label = CATEGORY_LABELS[cat]
        suffix = f" ({claimable} claimable)" if show_all and claimable else ""
        print(f"\n🔹 {label}{suffix}")
        for q in items:
            target = q.target if q.target is not None else "?"
            mark = "🎁" if q.is_claimable else ("⏳" if q.progress > 0 else "⬜")
            print(
                f"  {mark} {q.id!s:>8}  {q.name:<55} "
                f"{q.progress}/{target}  [{format_reward(q.reward)}]  ({format_create_time(q.create_time)})"
            )

    if not shown_any:
        print("ℹ️  No quests to show.")
    return quests


def get_quest_defaults(account: str) -> dict[int, dict[str, Any]]:
    """``account_configs`` の ``quest_defaults`` を読み込む（quest_id → 引数上書き値）。"""
    data = SessionManager.load(account)
    raw = data.get(QUEST_DEFAULTS_KEY) or {}
    return {int(k): dict(v) for k, v in raw.items()}


def set_quest_defaults(account: str, quest_id: int, key: str, value: Any) -> Any:
    """``quest_defaults`` に 1 パラメータだけ保存する（既存値は保持してマージ）。

    CLI（``--set-default``）から渡される文字列値は ``_parse_config_value`` で
    bool/int/float/JSON に解釈してから保存する。保存した値（解釈後）を返す。

    保存は ``update_config_merged`` によるロック付き read-modify-write で
    行う（並列実行時に他スレッドの書き込みを lost update で失わない）。
    """
    if isinstance(value, str):
        value = _parse_config_value(value)

    def _merge(existing: Any) -> dict[int, dict[str, Any]]:
        raw = existing if isinstance(existing, dict) else {}
        defaults = {int(k): dict(v) for k, v in raw.items()}
        defaults.setdefault(quest_id, {})[key] = value
        return defaults

    SessionManager.repo.update_config_merged(account, QUEST_DEFAULTS_KEY, _merge)
    return value


def get_quest_guild_defaults(account: str) -> dict[str, Any]:
    """``account_configs`` の ``quest_guild_defaults`` を読み込む（未設定は空 dict）。"""
    data = SessionManager.load(account)
    raw = data.get(QUEST_GUILD_DEFAULTS_KEY)
    return raw if isinstance(raw, dict) else {}


def set_quest_guild_defaults(account: str, key: str, value: Any) -> Any:
    """``quest_guild_defaults`` に 1 パラメータだけ保存する（既存値は保持してマージ）。

    保存は ``update_config_merged`` によるロック付き read-modify-write で
    行う（``last_recipe_at`` の更新が並列実行で旧値に戻るのを防ぐ）。
    """
    if isinstance(value, str):
        value = _parse_config_value(value)

    def _merge(existing: Any) -> dict[str, Any]:
        defaults = existing if isinstance(existing, dict) else {}
        defaults[key] = value
        return defaults

    SessionManager.repo.update_config_merged(account, QUEST_GUILD_DEFAULTS_KEY, _merge)
    return value


# ギルドレシピ（1 日 1 回ガード）で現在のリセットサイクル開始時刻（Unix 秒）を求める。
# userGetInfo の nextDayTs は「次のデイリーリセット境界」なので、そこから 24 時間を
# 引いた時刻が「今のサイクル（今日）の開始」になる。アカウントごとのタイムゾーン
# （timeZone / GMT オフセット）はサーバーが判定済みで、nextDayTs に反映されている。
def _guild_cycle_boundary(player: Any) -> int | None:
    """PlayerStatus の nextDayTs から現在のリセットサイクル開始時刻（Unix 秒）を返す。

    nextDayTs が未取得（0 など）なら None を返し、呼び出し側で「ガード無効
    （従来どおり実行）」として扱う。
    """
    next_day_ts = getattr(player, "next_day_ts", 0) or 0
    if not next_day_ts:
        return None
    return int(next_day_ts) - 86400


def _guild_ran_recipe_today(guild_defaults: dict[str, Any], boundary: int | None) -> bool:
    """今日のリセットサイクル内にギルドレシピを実行済みか（時刻ベース）。

    ``last_recipe_at``（最後にレシピを実行した Unix 秒）が現在のサイクル開始
    時刻（boundary）以上なら実行済み。boundary が None（nextDayTs 取得不可）
    の場合はガード無効＝未実行扱い（毎回実行）。
    """
    if boundary is None:
        return False
    last = guild_defaults.get("last_recipe_at")
    try:
        return last is not None and int(last) >= boundary
    except (TypeError, ValueError):
        return False


def _store_guild_recipe_run_today(account: str, last_ts: int) -> None:
    """ギルドレシピを実行した時刻（``last_recipe_at``）を保存する。

    ``set_quest_guild_defaults``（update_config_merged のロック付き
    read-modify-write）経由なので、並列実行時の lost update は起きない。
    """
    set_quest_guild_defaults(account, "last_recipe_at", last_ts)


# リソース不足等で「フォールバック候補（candidates）」を試す価値があるエラー名。
# 実測: 10024（heroArtifactLevelUp）の資源不足は "NotEnough"。スタミナ不足は
# 候補（heroId/slotId 変更）では解決しないため対象外。
FALLBACK_ERROR_NAMES = {ErrorName.NOT_ENOUGH.value}


def _guild_recipe_overrides(
    account_defaults: dict[int, dict[str, Any]], guild_defaults: dict[str, Any]
) -> dict[str, Any]:
    """ギルドレシピ実行用の引数オーバーライドを組み立てる。

    - heroId は ``quest_defaults[10023].heroId`` が設定済みならそれを優先し、
      未設定の場合のみ ``quest_guild_defaults.heroId`` を使う（二重管理の解消）。
    - 実行制御系キー（enabled / note / last_recipe_at / candidates）は
      オーバーライドに含めない（未知引数として警告されるのを防ぐ）。
    """
    overrides = {k: v for k, v in guild_defaults.items() if k not in NON_ARG_KEYS and k != "heroId"}
    hero_id = (account_defaults.get(GUILD_QUEST_RECIPE_ID) or {}).get("heroId")
    if hero_id is None:
        hero_id = guild_defaults.get("heroId")
    if hero_id is not None:
        overrides["heroId"] = hero_id
    return overrides


def _quest_candidates(account_defaults: dict[int, dict[str, Any]], quest_id: int) -> list[dict[str, Any]]:
    """``quest_defaults[quest_id].candidates``（優先度順のフォールバック候補）を返す。"""
    candidates = (account_defaults.get(quest_id) or {}).get("candidates")
    if not isinstance(candidates, list):
        return []
    return [c for c in candidates if isinstance(c, dict)]


def _filter_candidates(candidates: list[dict[str, Any]], known_keys: set[str]) -> list[dict[str, Any]]:
    """候補からステップ args に存在しないキーを除去する。

    実実行（``_run_quest_step``）と同じ規則を dry-run の計画表示にも適用し、
    「試される候補」の表示が実際の挙動と乖離しないようにする。
    """
    filtered = [{k: v for k, v in cand.items() if k in known_keys} for cand in candidates]
    return [cand for cand in filtered if cand]


def _guild_recipe_steps(
    account_defaults: dict[int, dict[str, Any]], guild_defaults: dict[str, Any]
) -> list[dict[str, Any]]:
    """ギルドレシピ（QUEST_OPERATIONS[GUILD_QUEST_RECIPE_ID]）のステップ列を解決する。

    dry-run の計画表示と実実行で同じ解決ロジックを使い回すための共通化。
    ``quest_defaults[10023].heroId`` が設定済みならそれを優先する
    （``_guild_recipe_overrides`` 参照）。
    """
    recipe = QUEST_OPERATIONS[GUILD_QUEST_RECIPE_ID]
    overrides = _guild_recipe_overrides(account_defaults, guild_defaults)
    return _resolve_operation_args(
        GUILD_QUEST_RECIPE_ID, recipe, {GUILD_QUEST_RECIPE_ID: overrides}
    )


def ensure_quest_guild_defaults(account: str) -> dict[str, Any]:
    """``quest_guild_defaults`` を初期化・補完する（既存値は保持）。

    ``QUEST_OPERATIONS[10023]``（heroTitanGift LevelUp ×2 → Drop）をギルド
    クエスト達成レシピとして使い、未設定のアカウントに ``enabled: false``、
    ``heroId``（レシピ既定値）・``note`` を投入する。
    初期状態は無効で、``--set-default guild enabled true`` で有効化する運用。

    補完は ``update_config_merged`` のロック付き read-modify-write で行う
    （並列実行時に ``last_recipe_at`` などの同時更新を失わない）。

    Returns:
        保存後の ``quest_guild_defaults``（dict）。
    """
    recipe = QUEST_OPERATIONS.get(GUILD_QUEST_RECIPE_ID)
    if not recipe:
        return get_quest_guild_defaults(account)
    first_args = recipe.get("steps", [{}])[0].get("args", {}) if recipe.get("steps") else {}
    note = " → ".join(_rpc_display(step["rpc"]) for step in recipe.get("steps", []))

    def _merge(existing: Any) -> dict[str, Any]:
        defaults = existing if isinstance(existing, dict) else {}
        if defaults.get("enabled") is None:
            defaults["enabled"] = False
        if "heroId" not in defaults and isinstance(first_args.get("heroId"), int):
            defaults["heroId"] = first_args["heroId"]
        if "note" not in defaults and note:
            defaults["note"] = note
        return defaults

    return SessionManager.repo.update_config_merged(account, QUEST_GUILD_DEFAULTS_KEY, _merge)


def ensure_quest_defaults(account: str) -> dict[int, dict[str, Any]]:
    """``quest_defaults`` を初期化・補完する（既存値は保持）。

    ``QUEST_OPERATIONS`` に登録されているクエストについて、``quest_defaults``
    に未登録のクエストを ``{"enabled": False}`` で追加し、さらに各ステップの
    ``args`` のデフォルト引数を（既に存在するキーは残したまま）補完する。
    デフォルト引数はコード側のレシピ（``QUEST_OPERATIONS``）からコピーされる
    ため、アカウント設定として固定され、レシピ変更の影響を受けない。
    **dict/list 型の引数（10028 の ``cost``/``reward`` 等）は構造データであり
    行編集で型崩れするため補完しない**（スカラー引数のみ固定値化する）。
    ``note`` には操作ステップの RPC 名を連結したメモ文字列（例: ``"shopBuy →
    titanArtifactLevelUp"``）が入り、DB JSON の人間可読性のためだけに使われる
    （実行・引数上書きには影響しない）。
    初期状態は全クエスト無効（enabled=false）で、``--set-default <id> enabled
    true`` で有効化してから実行する運用。既に全キーが揃っている（補完不要）
    場合は書き込みを行わない。**未登録の account を渡すと保存時に
    ``ValueError`` になるため、呼び出し側で事前にアカウント登録を確認する
    こと**（CLI は ``_ensure_session`` 等で登録済みを保証する）。

    補完は ``update_config_merged`` のロック付き read-modify-write で行う
    （並列実行時に ``enabled`` などの同時更新を失わない）。

    Returns:
        保存後の ``quest_defaults``（quest_id → 設定 dict）。
    """
    if not QUEST_OPERATIONS:
        return get_quest_defaults(account)

    def _merge(existing: Any) -> dict[int, dict[str, Any]]:
        raw = existing if isinstance(existing, dict) else {}
        defaults = {int(k): dict(v) for k, v in raw.items()}
        for qid, op in QUEST_OPERATIONS.items():
            conf = defaults.setdefault(qid, {})
            if conf.get("enabled") is None:
                conf["enabled"] = False
            note = " → ".join(_rpc_display(step["rpc"]) for step in op.get("steps", []))
            if "note" not in conf and note:
                conf["note"] = note
            for step in op.get("steps", []):
                for k, v in step.get("args", {}).items():
                    # dict/list 型（10028 の cost/reward 等）は構造データであり、
                    # 行編集（ウィザード/--set-default）で型崩れするため固定値化しない。
                    if k not in NON_ARG_KEYS and not isinstance(v, (dict, list)) and k not in conf:
                        conf[k] = v
        return defaults

    return SessionManager.repo.update_config_merged(account, QUEST_DEFAULTS_KEY, _merge)


def _parse_config_value(value: str) -> Any:
    """set-default の値文字列を bool/int/float/dict/list/str に解釈する。

    スカラー（bool/int/float）を最優先で解釈し、解釈できない場合は JSON として
    解釈を試みる（10028 の cost/reward のような dict/list 引数を文字列に化け
    させずに登録できるようにする）。どれにも該当しなければ文字列のまま返す。
    """
    if value == "true":
        return True
    if value == "false":
        return False
    for parse in (int, float):
        try:
            return parse(value)
        except ValueError:
            pass
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    # プリミティブ（int/float/str のみ）に戻るケースは上で処理済み。
    # dict/list 構造だけを JSON 解釈で復元する。
    if isinstance(parsed, (dict, list)):
        return parsed
    return value


def _rpc_display(rpc: Any) -> str:
    """ApiAction enum を RPC 名文字列（例: ``heroArtifactLevelUp``）に変換する。

    Python 3.11 以降 ``str(ApiAction.X)`` は ``ApiAction.HERO_ARTIFACT_LEVEL_UP``
    形式を返すため、表示・ログ用には明示的に ``.value`` を使う。
    """
    if isinstance(rpc, ApiAction):
        return rpc.value
    return str(rpc)


def _resolve_operation_args(
    quest_id: int, op: dict[str, Any], account_defaults: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    """アカウント固有設定を適用した steps（rpc+args）を返す。

    ``quest_defaults`` の上書きキーは、ステップの ``args`` に**既に存在する**
    キーに対してのみ適用される（新規キーの追加はしない）。マルチステップ
    （例: 10028）では各キーが該当する全ステップに適用される。どのステップの
    args にも存在しないキー（誤入力）は黙って無視せず警告ログを出し、
    気づけるようにする。
    """
    quest_overrides = (account_defaults or {}).get(quest_id) or {}
    steps = []
    for step in op.get("steps", []):
        args = dict(step.get("args", {}))
        for k, v in quest_overrides.items():
            if k in NON_ARG_KEYS:
                # enabled は実行制御フラグ、note は DB 可読性用メモであり操作引数ではない
                continue
            if k in args:
                args[k] = v
        steps.append({"rpc": step["rpc"], "args": args})

    known_keys = {k for st in steps for k in st["args"]}
    for k in quest_overrides:
        if k in NON_ARG_KEYS:
            continue
        if k not in known_keys:
            logger.warning(
                "quest_defaults[%d] key %r does not match any arg of quest %d's steps; ignored",
                quest_id,
                k,
                quest_id,
            )
    return steps


def _resolve_shop_buy_reward(
    client: HWClient,
    steps: list[dict[str, Any]],
    shop_cache: ShopInventory,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """shopBuy ステップの実在庫（shopGetAll）から reward/cost を動的解決する。

    タイタンアーティファクトショップ等は slot ごとにフラグメント商品が並び、
    期間で在庫が変わり得る。quest_defaults の ``slot`` は保持したまま、
    その slot の実際の商品（reward/cost）を実行直前に取得して上書きする。
    これにより「slot はユーザー選択、reward/cost はサーバー在庫に追従」と
    なる（reward を code 既定に固定しない）。

    フラグメントはタイタンアーティファクトの強化素材として共通であり
    （実測: fragment 2001 → titan 4012/4022、fragment 2003 → titan 4001
    がすべて成功）、titanArtifactLevelUp の対象（titanId/slotId）は
    quest_defaults でアカウントごとに指定する設計とする。

    - **在庫取得失敗**（通信等、認証以外）: 既定 reward をフォールバック
      として維持する（呼び出し側でエラー欄には出さない）。取得は
      ``shop_cache``（実行単位で共有）で最大 1 回に抑える。
    - **指定 shop が在庫に存在しない / 指定 slot が在庫に存在しない**:
      確実に失敗することが判明しているため、フォールバックせず
      ``(step, error)`` を problems に返す。呼び出し元はこのクエストの
      操作ステップを実行せず失敗報告する（固定 reward のまま送信して
      NotAvailable になるのを防ぐ）。
    - **指定 slot が購入済み（bought）**: 再購入はできないため同様に
      problems に返す（フォールバック・失敗の扱いは在庫不存在と統一）。

    Args:
        shop_cache: 実行単位で共有される shopGetAll のキャッシュ。
            （複数クエスト間で重複呼び出しを省くため run_quest_execute から渡す）

    Returns:
        ``(resolved_steps, problems)`` — problems は ``(step, message)`` 形式。
    """
    problems: list[tuple[str, str]] = []

    resolved: list[dict[str, Any]] = []
    for step in steps:
        if step["rpc"] != ApiAction.SHOP_BUY:
            resolved.append(step)
            continue
        args = dict(step["args"])
        shop_id = args.get("shopId")
        slot = args.get("slot")
        step_name = _rpc_display(step["rpc"])
        if shop_id is not None and slot is not None:
            shops = shop_cache.load(client)
            if shops is None:
                # 在庫取得失敗 → 既定 reward/cost でフォールバック
                resolved.append({"rpc": step["rpc"], "args": args})
                continue
            try:
                inventory = get_shop_slots(shops, shop_id)
            except ShopNotFoundError as exc:
                problems.append((step_name, str(exc)))
                continue
            slot_key = str(slot)
            if slot_key not in inventory:
                # 在庫は取得できたが指定 slot が存在しない → 実行しても失敗。
                problems.append(
                    (step_name, f"slot {slot} not in shop {shop_id} inventory (available: {sorted(inventory.keys())})")
                )
                continue
            item = inventory[slot_key]
            if is_bought(item):
                # 購入済み slot は再購入できない → 実行しても失敗。
                problems.append(
                    (step_name, f"slot {slot} in shop {shop_id} is already bought; choose another slot in quest_defaults")
                )
                continue
            if isinstance(item, dict):
                if "reward" in item:
                    args["reward"] = item["reward"]
                if "cost" in item:
                    args["cost"] = item["cost"]
        resolved.append({"rpc": step["rpc"], "args": args})
    return resolved, problems


def run_quest_execute(
    client: HWClient,
    account_alias: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    """未完了デイリー（state!=3 かつ QUEST_OPERATIONS 登録）を順に実行する。

    - ``dry_run=True`` の場合は操作は実行せず、実行予定の一覧を表示する。
      計画表示のため実在庫参照（shopGetAll の読み取りのみ）を行う場合が
      ある。操作（書き込み）は一切行わない。
    - ``confirm=False`` の場合（既定）、各ステップ実行前に y/n で確認する。
      ``confirm=True`` は自動実行（確認なし）。実際の操作は破壊的であるため
      CLI 上は ``--execute --yes`` 等で明示的に指示された場合のみ有効。
    - **対象は QUEST_OPERATIONS 登録済みのクエストのみ**。実行可否は
      ``quest_defaults[quest_id]["enabled"]`` でアカウントごとに制御し、
      enabled=true のものだけ操作ステップを実行する（未設定/初期状態は
      無効で何もしない）。未初期化の場合は ``ensure_quest_defaults`` で
      空設定（enabled=false）を自動投入する。
    - **shopBuy ステップは実在庫（shopGetAll）を読み、指定 slot の
      reward/cost を動的に解決してから実行する**。在庫の取得は実行単位で
      1 回にキャッシュされ（複数クエスト間で共有）、指定 shop / slot が
      在庫に存在しない場合や指定 slot が購入済み（bought）の場合は
      実行せず失敗報告する（固定 reward での NotAvailable 送信を防ぐ）。
      在庫取得失敗時（認証以外）は既定値をフォールバックする。
    - **報酬受取可能（state=2、または target 到達済み）のクエストは操作を
      実行せず、直接 ``questFarm`` で受領する**（既に条件達成済みなのに
      操作リソースを消費しないため）。
    - 失敗した項目は ``{account, quest_id, quest_name, step, error}`` として
      返り値と標準出力の両方に報告される。

    Returns:
        ``(succeeded, failed, skipped)`` — 成功/失敗の各報告リストと、無効
        （quest_defaults で enabled=false）のため対象外としたクエスト ID 一覧。
    """
    account = resolve_account(account_alias)
    res = client.quest_get_all()
    if res.status != ResponseStatus.SUCCESS:
        error = res.error_name or "-"
        print(f"❌ [{account}] Failed to fetch quests: {res.status.value} ({error})")
        return [], [{"account": account, "quest_id": None, "quest_name": "questGetAll", "step": "fetch", "error": error}], []

    raw_data = res.detail.get("response") if isinstance(res.detail, dict) else None
    if raw_data is None:
        print(f"❌ [{account}] Unexpected questGetAll response format.")
        return [], [], []
    quests = parse_quests(raw_data)
    account_defaults = ensure_quest_defaults(account)

    # 対象を 2 グループに分ける（QUEST_OPERATIONS 登録済みのみを対象とする）:
    #   claimable  = 報酬受取可能（state=2、または target 到達済みで state 遷移前）
    #                → 操作せず questFarm のみ
    #   targets    = 進行中（state=1 かつ target 未到達）で enabled=true
    #                → クリア操作を実行してから questFarm
    # ※ progress>=target も受領のみの対象にするのは、達成済みなのに操作
    #   リソース（10028 のフラグメント購入等）を再消費しないため。
    #   target 不明（None）のクエスト（10023 等）はこの判定に掛からない。
    # ※ バトルパス（26xx）等、QUEST_OPERATIONS 未登録の受領待ちクエストは
    #   execute の対象外（dry-run の表示ノイズも排除）。
    # ※ ギルドクエスト（2000xxxx/2001xxxx、"Obtain xxx Sparks of Power" 等、
    #   ID が日次・アカウントで動的）は QUEST_OPERATIONS と別扱い:
    #     - state=2（報酬受取可能）→ 無条件に claim 対象
    #     - state=1（進行中）→ quest_guild_defaults.enabled=true のとき
    #       heroTitanGift レシピ（10023 と同一）を実行して Sparks を稼ぐ。
    #       実行後 questGetAll を取り直し、達成（state=2）になったものを claim。
    claimable: list[Quest] = []
    failures: list[dict[str, Any]] = []
    skipped: list[int] = []
    targets: list[tuple[Quest, list[dict[str, Any]]]] = []
    guild_claimable: list[Quest] = []
    guild_active: list[Quest] = []
    shop_cache = ShopInventory()
    for q in quests:
        if q.is_done:
            continue
        if is_guild_quest(q.id):
            if q.is_claimable:
                guild_claimable.append(q)
            else:
                guild_active.append(q)
            continue
        op = QUEST_OPERATIONS.get(q.id)
        if op is None:
            continue
        if q.is_claimable or (q.target is not None and q.progress >= q.target):
            claimable.append(q)
            continue
        if not (account_defaults.get(q.id, {}).get("enabled")):
            skipped.append(q.id)
            continue
        steps = _resolve_operation_args(q.id, op, account_defaults)
        if not steps:
            continue
        steps, shop_problems = _resolve_shop_buy_reward(client, steps, shop_cache)
        if shop_problems:
            for step, message in shop_problems:
                failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": step, "error": message})
                print(f"❌ [{account}] {q.id} {q.name} cannot execute ({step}): {message}")
            continue
        targets.append((q, steps))

    guild_defaults = ensure_quest_guild_defaults(account)
    guild_enabled = bool(guild_defaults.get("enabled"))
    if guild_active and not guild_enabled:
        skipped.extend(q.id for q in guild_active)

    # ギルドレシピの 1 日 1 回ガード: userGetInfo の nextDayTs から今日のサイクル
    # 開始時刻を求め、last_recipe_at がこの境界以上なら「今日は実行済み」として
    # スキップ（時刻ベース。進捗には依存しない）。nextDayTs が取れない環境では
    # ガード無効＝毎回実行。
    guild_recipe_done_today = False
    guild_boundary: int | None = None
    if guild_active and guild_enabled:
        try:
            player = client.fetch_player_status()
            guild_boundary = _guild_cycle_boundary(player)
        except Exception:  # noqa: BLE001
            guild_boundary = None
        guild_recipe_done_today = _guild_ran_recipe_today(guild_defaults, guild_boundary)

    succeeded: list[dict[str, Any]] = []
    has_work = claimable or targets or guild_claimable or guild_active

    if dry_run:
        print(f"\n📋 [dry-run] Quest execution plan for {account}:")
        if not has_work:
            print("🔄 No executable quests.")
            return [], failures, skipped
        for q in claimable:
            print(f"\n🔹 {q.id} {q.name}: [claim already available]")
            print("    - questFarm (claim reward, no operation needed)")
        for q in guild_claimable:
            print(f"\n🔹 {q.id} {q.name}: [claim already available]")
            print("    - questFarm (claim reward, no operation needed)")
        for q, steps in targets:
            print(f"\n🔹 {q.id} {q.name}")
            for st in steps:
                print(f"    - {_rpc_display(st['rpc'])} {st['args']}")
            candidates = _filter_candidates(
                _quest_candidates(account_defaults, q.id),
                {k for st in steps for k in st["args"]},
            )
            if candidates:
                print(f"    (fallback candidates: {candidates})")
        if guild_active:
            daily_covers_recipe = any(q.id == GUILD_QUEST_RECIPE_ID for q, _ in targets)
            if not guild_enabled:
                print("ℹ️  Guild quests (Sparks of Power) found but quest_guild_defaults.enabled=false (skip; see Skipped list).")
            elif daily_covers_recipe:
                print("ℹ️  Guild quests (Sparks of Power): recipe covered by daily quest 10023 in this plan; skipping duplicate recipe (claims still run).")
            elif guild_recipe_done_today:
                last_ts = guild_defaults.get("last_recipe_at")
                at_label = format_create_time(int(last_ts)) if last_ts is not None else "today"
                print(f"ℹ️  Guild quests (Sparks of Power): recipe already run today ({at_label}); skipping recipe (claims still run).")
            else:
                print("\n🔹 Guild quests (Sparks of Power): run recipe to gain Sparks (not run yet today)")
                steps = _guild_recipe_steps(account_defaults, guild_defaults)
                for st in steps:
                    print(f"    - {_rpc_display(st['rpc'])} {st['args']}")
        print_skipped_quests(account, skipped)
        return [], failures, skipped

# 受領フェーズ（操作不要）
    _claim_quests(client, claimable + guild_claimable, account, succeeded, failures)

    # 実行フェーズ
    recipe_executed_in_daily = False
    for q, steps in targets:
        print(f"\n🔹 Executing {q.id} {q.name} ...")
        all_steps_ok = True
        for st in steps:
            resp = _run_quest_step(client, q, st, account, confirm, account_defaults, failures)
            if resp is None:
                all_steps_ok = False
                break

            # レスポンスに含まれる quests 配列から対象クエストの状態を確認
            if _quest_reached_claimable(resp, q.id):
                print(f"   ✅ {q.id} {q.name} completed (step: {_rpc_display(st['rpc'])}). Claiming reward...")
                claim_res = client.quest_farm(q.id)
                if claim_res.status == ResponseStatus.SUCCESS:
                    succeeded.append({"account": account, "quest_id": q.id, "quest_name": q.name})
                    print(f"   🎁 Reward claimed for {q.id} {q.name}")
                else:
                    failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": "questFarm", "error": claim_res.error_name or "-"})
                    print(f"❌ [{account}] {q.id} {q.name} reward claim failed: {claim_res.error_name}")
                break
            # 全ステップ成功したが、このレスポンス群には対象クエストが含まれなかった
        else:
            print(f"ℹ️  [{account}] {q.id} {q.name}: steps executed but claim not detected (check questGetAll).")

        # デイリー 10023（heroTitanGift レシピと同一）を成功させた場合は、
        # 「ギルドレシピ実行」を兼ねた扱いにしてギルドフェーズでの二重実行を
        # 防ぎつつ、今日の実行済み時刻（last_recipe_at）にも記録する
        # （Gift 資源の二重消費防止＝1 日 1 回の枠を消費）。
        if q.id == GUILD_QUEST_RECIPE_ID and all_steps_ok:
            recipe_executed_in_daily = True
            _store_guild_recipe_run_today(account, int(time.time()))

    # ギルドクエスト（Sparks of Power）フェーズ: active のギルドクエストが
    # ある場合、heroTitanGift レシピを 1 日 1 回実行し、実行後に questGetAll
    # を取り直して達成（state=2）になったギルドクエストをまとめて claim する。
    # （1 日 1 回ガード: nextDayTs から求めた今日のサイクル内で last_recipe_at
    #   が記録済みなら、次のリセットまでレシピはスキップする。claimable の
    #   ギルドクエストは上の受領フェーズで既に受領されている）
    _run_guild_quest_phase(
        client=client,
        guild_active=guild_active,
        guild_enabled=guild_enabled,
        guild_recipe_done_today=guild_recipe_done_today,
        recipe_executed_in_daily=recipe_executed_in_daily,
        account=account,
        account_defaults=account_defaults,
        guild_defaults=guild_defaults,
        confirm=confirm,
        succeeded=succeeded,
        failures=failures,
    )

    print_skipped_quests(account, skipped)
    print_quest_failures(account, failures)
    return succeeded, failures, skipped


def _claim_quests(
    client: HWClient,
    quests: list[Quest],
    account: str,
    succeeded: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    """操作不要な claimable クエストをまとめて questFarm で受領する。"""
    for q in quests:
        print(f"\n🔹 {q.id} {q.name}: already claimable. Claiming reward...")
        claim_res = client.quest_farm(q.id)
        if claim_res.status == ResponseStatus.SUCCESS:
            succeeded.append({"account": account, "quest_id": q.id, "quest_name": q.name})
            print(f"   🎁 Reward claimed for {q.id} {q.name}")
        else:
            failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": "questFarm", "error": claim_res.error_name or "-"})
            print(f"❌ [{account}] {q.id} {q.name} reward claim failed: {claim_res.error_name}")


def _run_guild_quest_phase(
    client: HWClient,
    guild_active: list[Quest],
    guild_enabled: bool,
    guild_recipe_done_today: bool,
    recipe_executed_in_daily: bool,
    account: str,
    account_defaults: dict[int, dict[str, Any]],
    guild_defaults: dict[str, Any],
    confirm: bool,
    succeeded: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> None:
    """ギルドクエスト（Sparks of Power）フェーズを実行する。

    active のギルドクエストに対して heroTitanGift レシピ（LevelUp ×2 →
    Drop）を**1 日 1 回**実行し、実行後に questGetAll を取り直して達成
    （state=2）になったギルドクエストを claim する（1 日 1 回ガード:
    時刻ベース。nextDayTs から求めた今日のサイクル開始を境界とする）。

    - ``guild_recipe_done_today``: 今日のサイクル内にレシピを実行済みか
      （``last_recipe_at`` >= 今日のサイクル開始時刻）。実行済みならスキップ。
    - ``recipe_executed_in_daily``: 同一実行内でデイリー 10023 が既に成功
      していた場合、レシピを重複実行しない（Gift 系資源の二重消費防止）。
      10023 の成功も ``last_recipe_at`` に記録済み。
    - 進捗（progress）には依存しない: ギルドクエストはギルド全体の累積
      ポイントで達成されるため、1 セット実行後に達成分を受領するだけで
      繰り返し実行はしない（達成は後日自然に進行する）。
    - 失敗時（資源不足等）は ``last_recipe_at`` を記録しないため、次の
      実行機会に再試行できる。
    """
    if not (guild_enabled and guild_active):
        return
    guild_ids = ", ".join(str(q.id) for q in guild_active)
    if recipe_executed_in_daily:
        print(f"\nℹ️  [{account}] Guild quests ({guild_ids}): recipe already executed via daily quest 10023 in this run; skipping duplicate recipe.")
        return
    if guild_recipe_done_today:
        last_ts = guild_defaults.get("last_recipe_at")
        at_label = format_create_time(int(last_ts)) if last_ts is not None else "today"
        print(f"\nℹ️  [{account}] Guild quests ({guild_ids}): recipe already run today ({at_label}); skipping recipe.")
        return

    steps = _guild_recipe_steps(account_defaults, guild_defaults)
    print(f"\n🔹 Guild quests ({guild_ids}): running recipe to gain Sparks of Power (today's run) ...")

    recipe_ok = True
    for st in steps:
        if not confirm:
            try:
                answer = input(f"   ⚠️  Run {_rpc_display(st['rpc'])} {st['args']}? [y/N] ")
            except EOFError:
                print("   ⛔ No interactive input available; re-run with --yes to proceed unattended.")
                failures.append({"account": account, "quest_id": None, "quest_name": "Guild quests", "step": _rpc_display(st["rpc"]), "error": "no interactive input (use --yes)"})
                recipe_ok = False
                break
            if answer.strip().lower() not in ("y", "yes"):
                print(f"   ⏭️  Skipped {_rpc_display(st['rpc'])} (user declined)")
                failures.append({"account": account, "quest_id": None, "quest_name": "Guild quests", "step": _rpc_display(st["rpc"]), "error": "skipped by user"})
                recipe_ok = False
                break

        try:
            resp = client.quest_operation(st["rpc"], st["args"])
        except Exception as exc:  # noqa: BLE001
            failures.append({"account": account, "quest_id": None, "quest_name": "Guild quests", "step": _rpc_display(st["rpc"]), "error": f"exception: {exc}"})
            print(f"❌ [{account}] Guild quests failed at {_rpc_display(st['rpc'])}: {exc}")
            recipe_ok = False
            break

        if resp.status != ResponseStatus.SUCCESS:
            error = resp.error_name or "-"
            failures.append({"account": account, "quest_id": None, "quest_name": "Guild quests", "step": _rpc_display(st["rpc"]), "error": error})
            print(f"❌ [{account}] Guild quests failed at {_rpc_display(st['rpc'])}: {error}")
            recipe_ok = False
            break

    if not recipe_ok:
        return
    _store_guild_recipe_run_today(account, int(time.time()))
    print("   ✅ Recipe executed. Re-fetching quests to claim reached stages...")
    res2 = client.quest_get_all()
    if res2.status != ResponseStatus.SUCCESS:
        error = res2.error_name or "-"
        failures.append({"account": account, "quest_id": None, "quest_name": "Guild quests", "step": "questGetAll", "error": error})
        print(f"❌ [{account}] Guild quests: re-fetch failed: {error}")
        return
    raw2 = res2.detail.get("response") if isinstance(res2.detail, dict) else None
    refreshed = parse_quests(raw2) if isinstance(raw2, list) else []
    reached = [q for q in refreshed if is_guild_quest(q.id) and q.is_claimable]
    if not reached:
        print("   ℹ️  No guild quest reached claimable state yet.")
    _claim_quests(client, reached, account, succeeded, failures)


def print_skipped_quests(account: str, skipped: list[int]) -> None:
    """enabled=false（quest_defaults / quest_guild_defaults）のため対象外としたクエストを 1 行にまとめる。"""
    if skipped:
        ids = ", ".join(str(q) for q in skipped)
        print(f"ℹ️  [{account}] Skipped (not enabled in quest_defaults / quest_guild_defaults): {ids}")


def _run_quest_step(
    client: HWClient,
    q: Quest,
    st: dict[str, Any],
    account: str,
    confirm: bool,
    account_defaults: dict[int, dict[str, Any]],
    failures: list[dict[str, Any]],
) -> HWResponse | None:
    """1 ステップを実行し、結果を failures に記録する。

    - ``confirm=False`` の場合（既定）、実行前に y/n で確認する。
      ``confirm=True`` は自動実行（確認なし）。
    - 失敗がフォールバック対象エラー（``FALLBACK_ERROR_NAMES``、リソース
      不足系）で、``quest_defaults[qid].candidates``（優先度順の dict
      リスト）が設定されていれば、候補を args にマージして再実行する。
      成功した候補で継続し、全候補失敗なら失敗報告する。

    Returns:
        成功時の HWResponse（呼び出し側で claim 判定に使う）。中断・全失敗
        なら None（failures への追記済み）。
    """
    candidates = _filter_candidates(
        _quest_candidates(account_defaults, q.id), set(st["args"])
    )
    args_list = [st["args"]] + [{**st["args"], **cand} for cand in candidates]
    last_error: str | None = None
    for idx, args in enumerate(args_list):
        is_fallback = idx > 0
        if not confirm:
            label = f" (fallback {idx})" if is_fallback else ""
            try:
                answer = input(f"   ⚠️  Run {_rpc_display(st['rpc'])} {args}{label}? [y/N] ")
            except EOFError:
                print("   ⛔ No interactive input available; re-run with --yes to proceed unattended.")
                failures.append(
                    {"account": account, "quest_id": q.id, "quest_name": q.name, "step": _rpc_display(st["rpc"]), "error": "no interactive input (use --yes)"}
                )
                return None
            if answer.strip().lower() not in ("y", "yes"):
                print(f"   ⏭️  Skipped {_rpc_display(st['rpc'])} (user declined)")
                failures.append(
                    {"account": account, "quest_id": q.id, "quest_name": q.name, "step": _rpc_display(st["rpc"]), "error": "skipped by user"}
                )
                return None
        try:
            resp = client.quest_operation(st["rpc"], args)
        except Exception as exc:  # noqa: BLE001
            last_error = f"exception: {exc}"
            if is_fallback:
                continue
            failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": _rpc_display(st["rpc"]), "error": last_error})
            print(f"❌ [{account}] {q.id} {q.name} failed at {_rpc_display(st['rpc'])}: {exc}")
            return None
        if resp.status == ResponseStatus.SUCCESS:
            if is_fallback:
                print(f"   ⚡️ [{account}] {q.id} {q.name} recovered with fallback args {args}")
            return resp
        last_error = resp.error_name or "-"
        if is_fallback:
            print(f"   ℹ️  [{account}] {q.id} {q.name} fallback {args} failed ({last_error}); trying next candidate...")
            continue
        if last_error in FALLBACK_ERROR_NAMES and len(args_list) > 1:
            print(f"   💡 [{account}] {q.id} {q.name} {last_error}; retrying with fallback candidates...")
            continue
        break
    failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": _rpc_display(st["rpc"]), "error": last_error or "-"})
    print(f"❌ [{account}] {q.id} {q.name} failed at {_rpc_display(st['rpc'])}: {last_error}")
    return None



def _quest_reached_claimable(resp: Any, quest_id: int) -> bool:
    """操作レスポンス内の ``quests`` 配列に対象クエストが state=2 で含まれているか。"""
    if hasattr(resp, "detail"):
        resp = resp.detail
    detail = resp if isinstance(resp, dict) else {}
    quests = detail.get("quests") if isinstance(detail, dict) else None
    if not isinstance(quests, list):
        return False
    for item in quests:
        if not isinstance(item, dict):
            continue
        # state は int/str 混在し得るため _to_int で正規化して比較する
        if _to_int(item.get("id")) == quest_id and _to_int(item.get("state")) == STATE_CLAIMABLE:
            return True
    return False


def print_quest_failures(account: str, failures: list[dict[str, Any]]) -> None:
    """失敗報告を「アカウント × クエスト」単位で標準出力に出す。"""
    if not failures:
        return
    print(f"\n❌ [{account}] {len(failures)} quest operation(s) failed:")
    for f in failures:
        qid = f.get("quest_id") or "-"
        print(f"   - account={f.get('account')} quest={qid} ({f.get('quest_name')}) step={f.get('step')}: {f.get('error')}")


# --- 対話的設定（quest_defaults 編集ウィザード） ---


def _prompt_input(prompt: str) -> str:
    """input() のラッパー。EOF（Ctrl-D / 閉じた stdin）では案内して終了する。

    TTY での Ctrl-D と非TTY の閉じたパイプはどちらも EOF として扱う。
    """
    try:
        return input(prompt)
    except EOFError:
        print("⛔ No interactive input available (EOF).", file=sys.stderr)
        raise SystemExit(1) from None


def _fmt_value(value: Any) -> str:
    """設定値の表示用文字列（bool は true/false、dict は JSON）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def edit_quest_defaults_interactive(account: str) -> None:
    """``quest_defaults`` を対話的に編集する（番号選択ウィザード）。

    - クエストを**番号＋名前＋現在の有効状態＋note**で一覧表示し、番号で選択する。
    - 選択したクエストの設定キー（``enabled`` と操作引数。``note`` は参照専用）を
      現在値付きで一覧表示し、番号で値を入力/切り替える。
    - ``q`` で終了、``b`` でクエスト一覧に戻る（enabled の true/false 選択中は
      ``b``/``q`` で選択をキャンセル）。
    - 保存は ``set_quest_defaults`` 経由（既存値は保持）。
    - 表示は rich の Table に一本化。TTY では選択のたびに画面をクリアして
      再描画し、非TTY（パイプ等）ではそのまま縦に描画される（スクロール表示）。
    - 入力は ``_prompt_input`` 経由で、非TTY（EOF）では案内して終了する。
    """
    defaults = ensure_quest_defaults(account)
    console = Console()
    # 全画面リフレッシュ（console.clear）は出力先が端末のときだけ行う。
    # stdin が TTY でも stdout がリダイレクト/パイプなら、クリア制御コードを
    # ファイル等に混入させない（EOF 検知は _prompt_input が担う）。
    refresh = sys.stdin.isatty() and sys.stdout.isatty()
    quest_ids = sorted(defaults)
    selected: int | None = None
    message = ""

    def _render(table: Table) -> None:
        """テーブルを描画する（refresh なら画面クリア＋メッセージ付き）。"""
        if refresh:
            console.clear()
        renderable: Any = table
        if message:
            renderable = Group(table, Text(message))
        console.print(renderable)

    while True:
        if selected is None:
            _render(_quest_list_table(account, quest_ids, defaults))
            choice = _prompt_input("Choice> ").strip().lower()
            if choice == "q":
                break
            idx = _parse_choice(choice, len(quest_ids))
            if idx is None:
                message = f"⚠️  Invalid choice: {choice!r}"
                continue
            message = ""
            selected = quest_ids[idx - 1]
            continue

        conf = defaults[selected]
        _render(_key_list_table(selected, conf))
        choice = _prompt_input("Choice> ").strip().lower()
        if choice == "b":
            selected = None
            message = ""
            continue
        if choice == "q":
            break
        keys = _editable_keys(conf)
        idx = _parse_choice(choice, len(keys))
        if idx is None:
            message = f"⚠️  Invalid choice: {choice!r}"
            continue
        key = keys[idx - 1]

        if key == "enabled":
            _render(_enabled_choice_table())
            v = _prompt_input("Choice> ").strip().lower()
            if v == "1":
                new_value = True
            elif v == "2":
                new_value = False
            elif v in ("b", "q"):
                message = ""
                continue
            else:
                message = f"⚠️  Invalid choice: {v!r} (1 or 2, b: cancel)"
                continue
        else:
            _render(_value_input_table(key, conf.get(key)))
            raw = _prompt_input("Value> ").strip()
            if not raw:
                continue
            if raw.lower() in ("b", "q"):
                # 他の選択メニューと同じ「キャンセル」セマンティクス（保存しない）
                message = "⚠️  Enter to keep current value; b/q: cancel (nothing saved)"
                continue
            new_value = _parse_config_value(raw)
            # dict/list 構造（JSON 入力）はウィザードの「行編集」では型を保証
            # できないため受け付けない。登録は --set-default <id> <key> '<json>'
            # で明示的に行う（_editable_keys の除外と対称）。
            if isinstance(new_value, (dict, list)):
                message = "⚠️  dict/list 値は --set-default で指定してください（ウィザードでは編集不可）"
                continue

        set_quest_defaults(account, selected, key, new_value)
        conf[key] = new_value
        message = f"✅ saved {key} = {_fmt_value(new_value)}"

    if refresh:
        console.clear()
    console.print("Bye.")


def _editable_keys(conf: dict[str, Any]) -> list[str]:
    """編集対象の設定キー（enabled ＋ スカラー操作引数。note・dict/list は除外）。

    dict/list 型の引数（10028 の cost/reward 等）は行編集すると型崩れするため、
    ウィザードの編集対象にしない（既存 DB に埋まっていた場合の保護も兼ねる）。
    """
    return ["enabled"] + [
        k for k in conf if k not in NON_ARG_KEYS and not isinstance(conf[k], (dict, list))
    ]


def _parse_choice(choice: str, count: int) -> int | None:
    """「1..N」の番号入力を index（1 始まり）に変換する（無効は None）。"""
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= count:
            return idx
    return None


def _quest_list_table(account: str, quest_ids: list[int], defaults: dict[int, dict[str, Any]]) -> Table:
    """クエスト一覧テーブル（note は参照表示）。"""
    table = Table(title=f"⚙️  quest_defaults for {account}", box=box.ROUNDED)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("ID", style="yellow")
    table.add_column("Quest", overflow="ellipsis")
    table.add_column("Operations (note)", overflow="ellipsis")
    table.add_column("Status")
    for i, qid in enumerate(quest_ids, 1):
        _, name = classify_quest(qid)
        conf = defaults[qid]
        enabled = bool(conf.get("enabled"))
        status = "✅ enabled" if enabled else "⏸️  disabled"
        table.add_row(str(i), str(qid), name, str(conf.get("note", "")), status)
    table.caption = "Select a quest by number; q: quit"
    return table


def _key_list_table(qid: int, conf: dict[str, Any]) -> Table:
    """設定キー一覧テーブル（note は編集対象外なので表示しない）。"""
    _, name = classify_quest(qid)
    table = Table(title=f"⚙️  Configure {qid} {name}", box=box.ROUNDED)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Key", style="yellow")
    table.add_column("Current", overflow="ellipsis")
    for i, k in enumerate(_editable_keys(conf), 1):
        table.add_row(str(i), k, _fmt_value(conf.get(k)))
    table.caption = "Select a key by number; b: back to quest list; q: quit"
    return table


def _enabled_choice_table() -> Table:
    """enabled の true/false 選択テーブル。"""
    table = Table(title="⚙️  Select enabled value", box=box.ROUNDED)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Value", style="yellow")
    table.add_row("1", "true")
    table.add_row("2", "false")
    table.caption = "b / q: cancel"
    return table


def _value_input_table(key: str, current: Any) -> Table:
    """値入力の案内テーブル。"""
    table = Table(title=f"⚙️  Enter value for {key}", box=box.ROUNDED)
    table.add_column("Field", style="yellow")
    table.add_column("Value", overflow="ellipsis")
    table.add_row("Current", _fmt_value(current))
    table.add_row("Hint", "Empty = keep (dict/list は --set-default で指定)")
    return table

