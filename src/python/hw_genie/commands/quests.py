"""クエスト（特にデイリー）の取得・表示。

Hero Wars の ``questGetAll`` レスポンスにはクエスト名・カテゴリ・目標値が
含まれないため、:data:`QUEST_MASTER`（ゲーム UI との照合で確定した
ID → 名称/カテゴリ/目標値）を正引きテーブルとして使う。
未確定の ID は ID ファミリの規則でカテゴリだけ推定する。
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from hw_genie.core.client import HWClient, ResponseStatus, resolve_account
from hw_genie.core.utils import format_timestamp_for_display

logger = logging.getLogger(__name__)

# --- クエスト状態 ---
# 1 = 進行中 (active/progressing), 2 = 完了 (finished), 3 = その他 (farm 応答等)
STATE_ACTIVE = 1
STATE_FINISHED = 2

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
        "name": "Perform 1 summon in the Soul Atrium / Use emerald exchange (順序要確認)",
        "target": 1,
    },
    10007: {
        "category": "daily",
        "name": "Use emerald exchange / Perform 1 summon in the Soul Atrium (順序要確認)",
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
    10033: {
        "category": "unknown",
        "name": "要確認（dungeonActivity 報酬、Daily タブ非表示）",
        "target": None,
    },
}

# ID ファミリごとのカテゴリ推定規則（マスタ未登録 ID 向け）
_FAMILY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("2000", "2001"), "guild"),
    (("110",), "weekly"),
    (("232",), "main"),
    (("398",), "event"),
    (("26", "27"), "battlepass"),
)


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
    def is_completed(self) -> bool:
        """完了済み（state=2）かどうか。"""
        return self.state == STATE_FINISHED


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
    """クエスト一覧を取得し、未完了（state!=2）のデイリーを中心に表示する。

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

    visible = quests if show_all else [q for q in quests if not q.is_completed]

    if show_all:
        completed = sum(1 for q in quests if q.is_completed)
        uncompleted = len(quests) - completed
        print(f"\n📋 Quest status for {account} "
              f"(total {len(quests)}, {completed} completed, {uncompleted} uncompleted):")
    else:
        print(f"\n📋 Quest status for {account} "
              f"(total {len(quests)}, uncompleted {len(visible)}):")

    shown_any = False
    for cat in CATEGORIES:
        items = [q for q in visible if q.category == cat]
        if not items:
            continue
        shown_any = True
        done = sum(1 for q in items if q.is_completed)
        label = CATEGORY_LABELS[cat]
        suffix = f" ({done} completed)" if show_all and done else ""
        print(f"\n🔹 {label}{suffix}")
        for q in items:
            target = q.target if q.target is not None else "?"
            mark = "✅" if q.is_completed else ("⏳" if q.progress > 0 else "⬜")
            print(
                f"  {mark} {q.id!s:>8}  {q.name:<55} "
                f"{q.progress}/{target}  [{format_reward(q.reward)}]  ({format_create_time(q.create_time)})"
            )

    if not shown_any:
        print("ℹ️  No quests to show.")
    return quests
