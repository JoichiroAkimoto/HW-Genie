"""クエスト（特にデイリー）の取得・表示。

Hero Wars の ``questGetAll`` レスポンスにはクエスト名・カテゴリ・目標値が
含まれないため、:data:`QUEST_MASTER`（ゲーム UI との照合で確定した
ID → 名称/カテゴリ/目標値）を正引きテーブルとして使う。
未確定の ID は ID ファミリの規則でカテゴリだけ推定する。
"""

import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from hw_genie.core.client import ApiAction, HWClient, ResponseStatus, resolve_account
from hw_genie.core.session_manager import SessionManager
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
            {"rpc": ApiAction.SHOP_BUY, "args": {"shopId": 13, "slot": 24, "cost": {"coin": {"18": 12}}, "reward": {"fragmentTitanArtifact": {"2005": 1}}, "amount": 200}},
            {"rpc": ApiAction.TITAN_ARTIFACT_LEVEL_UP, "args": {"titanId": 4022, "slotId": 0}},
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

# quest_defaults 内で「操作引数ではない」キー（実行制御フラグ / 可読性メモ）
# ・enabled … 実行可否フラグ（true のときのみ操作を実行）
# ・note   … 操作 RPC 名の連結メモ（DB JSON の人間可読性専用）
# これらは操作ステップの args としては使用されず、未知キー警告の対象外でもある。
NON_ARG_KEYS = ("enabled", "note")


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

    CLI（``--set-default``）から渡される文字列値は ``_parse_float_value`` で
    bool/int/float/JSON に解釈してから保存する。保存した値（解釈後）を返す。
    """
    if isinstance(value, str):
        value = _parse_float_value(value)
    defaults = get_quest_defaults(account)
    defaults.setdefault(quest_id, {})[key] = value
    SessionManager.repo.update_config(account, {QUEST_DEFAULTS_KEY: defaults})
    return value


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

    Returns:
        保存後の ``quest_defaults``（quest_id → 設定 dict）。
    """
    if not QUEST_OPERATIONS:
        return get_quest_defaults(account)
    defaults = get_quest_defaults(account)
    changed = False
    for qid, op in QUEST_OPERATIONS.items():
        conf = defaults.setdefault(qid, {})
        if conf.get("enabled") is None:
            conf["enabled"] = False
            changed = True
        note = " → ".join(_rpc_display(step["rpc"]) for step in op.get("steps", []))
        if "note" not in conf and note:
            conf["note"] = note
            changed = True
        for step in op.get("steps", []):
            for k, v in step.get("args", {}).items():
                # dict/list 型（10028 の cost/reward 等）は構造データであり、
                # 行編集（ウィザード/--set-default）で型崩れするため固定値化しない。
                if k not in NON_ARG_KEYS and not isinstance(v, (dict, list)) and k not in conf:
                    conf[k] = v
                    changed = True
    if changed:
        SessionManager.repo.update_config(account, {QUEST_DEFAULTS_KEY: defaults})
    return defaults


def _parse_float_value(value: str) -> Any:
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


def run_quest_execute(
    client: HWClient,
    account_alias: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """未完了デイリー（state!=3 かつ QUEST_OPERATIONS 登録）を順に実行する。

    - ``dry_run=True`` の場合は操作は実行せず、実行予定の一覧を表示する。
    - ``confirm=False`` の場合（既定）、各ステップ実行前に y/n で確認する。
      ``confirm=True`` は自動実行（確認なし）。実際の操作は破壊的であるため
      CLI 上は ``--execute --yes`` 等で明示的に指示された場合のみ有効。
    - **対象は QUEST_OPERATIONS 登録済みのクエストのみ**。実行可否は
      ``quest_defaults[quest_id]["enabled"]`` でアカウントごとに制御し、
      enabled=true のものだけ操作ステップを実行する（未設定/初期状態は
      無効で何もしない）。未初期化の場合は ``ensure_quest_defaults`` で
      空設定（enabled=false）を自動投入する。
    - **報酬受取可能（state=2、または target 到達済み）のクエストは操作を
      実行せず、直接 ``questFarm`` で受領する**（既に条件達成済みなのに
      操作リソースを消費しないため）。
    - 失敗した項目は ``{account, quest_id, quest_name, step, error}`` として
      返り値と標準出力の両方に報告される。

    Returns:
        ``(succeeded, failed)`` — 成功/失敗の各報告リスト。
    """
    account = resolve_account(account_alias)
    res = client.quest_get_all()
    if res.status != ResponseStatus.SUCCESS:
        error = res.error_name or "-"
        print(f"❌ [{account}] Failed to fetch quests: {res.status.value} ({error})")
        return [], [{"account": account, "quest_id": None, "quest_name": "questGetAll", "step": "fetch", "error": error}]

    raw_data = res.detail.get("response") if isinstance(res.detail, dict) else None
    if raw_data is None:
        print(f"❌ [{account}] Unexpected questGetAll response format.")
        return [], []
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
    # ※ バトルパス（26xx）やギルド（2000x）等、QUEST_OPERATIONS 未登録の
    #   受領待ちクエストは execute の対象外（dry-run の表示ノイズも排除）。
    claimable: list[Quest] = []
    targets: list[tuple[Quest, list[dict[str, Any]]]] = []
    for q in quests:
        if q.is_done:
            continue
        op = QUEST_OPERATIONS.get(q.id)
        if op is None:
            continue
        if q.is_claimable or (q.target is not None and q.progress >= q.target):
            claimable.append(q)
            continue
        if not (account_defaults.get(q.id, {}).get("enabled")):
            print(f"ℹ️  [{account}] Skip {q.id} ({q.name}): not enabled in quest_defaults")
            continue
        steps = _resolve_operation_args(q.id, op, account_defaults)
        if not steps:
            continue
        targets.append((q, steps))

    succeeded: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    if dry_run:
        print(f"\n📋 [dry-run] Quest execution plan for {account}:")
        if not claimable and not targets:
            print("🔄 No executable quests.")
            return [], failures
        for q in claimable:
            print(f"\n🔹 {q.id} {q.name}: [claim already available]")
            print("    - questFarm (claim reward, no operation needed)")
        for q, steps in targets:
            print(f"\n🔹 {q.id} {q.name}")
            for st in steps:
                print(f"    - {_rpc_display(st['rpc'])} {st['args']}")
        return [], failures

    # 受領フェーズ（操作不要）
    for q in claimable:
        print(f"\n🔹 {q.id} {q.name}: already claimable. Claiming reward...")
        claim_res = client.quest_farm(q.id)
        if claim_res.status == ResponseStatus.SUCCESS:
            succeeded.append({"account": account, "quest_id": q.id, "quest_name": q.name})
            print(f"   🎁 Reward claimed for {q.id} {q.name}")
        else:
            failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": "questFarm", "error": claim_res.error_name or "-"})
            print(f"❌ [{account}] {q.id} {q.name} reward claim failed: {claim_res.error_name}")

    # 実行フェーズ
    for q, steps in targets:
        print(f"\n🔹 Executing {q.id} {q.name} ...")
        for st in steps:
            if not confirm:
                try:
                    answer = input(f"   ⚠️  Run {_rpc_display(st['rpc'])} {st['args']}? [y/N] ")
                except EOFError:
                    print("   ⛔ No interactive input available; re-run with --yes to proceed unattended.")
                    failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": _rpc_display(st["rpc"]), "error": "no interactive input (use --yes)"})
                    break
                if answer.strip().lower() not in ("y", "yes"):
                    print(f"   ⏭️  Skipped {_rpc_display(st['rpc'])} (user declined)")
                    failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": _rpc_display(st["rpc"]), "error": "skipped by user"})
                    break

            try:
                resp = client.quest_operation(st["rpc"], st["args"])
            except Exception as exc:  # noqa: BLE001
                failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": _rpc_display(st["rpc"]), "error": f"exception: {exc}"})
                print(f"❌ [{account}] {q.id} {q.name} failed at {_rpc_display(st['rpc'])}: {exc}")
                break

            if resp.status != ResponseStatus.SUCCESS:
                error = resp.error_name or "-"
                failures.append({"account": account, "quest_id": q.id, "quest_name": q.name, "step": _rpc_display(st["rpc"]), "error": error})
                print(f"❌ [{account}] {q.id} {q.name} failed at {_rpc_display(st['rpc'])}: {error}")
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
        else:
            # 全ステップ成功したが、このレスポンス群には対象クエストが含まれなかった
            print(f"ℹ️  [{account}] {q.id} {q.name}: steps executed but claim not detected (check questGetAll).")

    print_quest_failures(account, failures)
    return succeeded, failures


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
            new_value = _parse_float_value(raw)
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

