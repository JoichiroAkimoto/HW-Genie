"""Guild / personal chat (chatGetAll / chatGetTalks) fetch, table display and summary.

Fetch latest 50 messages (default) via ``chatGetAll`` (``chatType=clan``
and other types) and display as table + statistical summary. For personal
chat, both modes are supported: omit ``--user-id`` to list recent
conversations via ``chatGetTalks``, or pass it to fetch one thread via
``chatGetAll``.

- ``hw-genie chat``: show guild chat table + summary
- ``--type``: chatType selection (clan / training / xgvg / server / personal)
- ``--user-id``: peer userId for ``--type personal`` (optional; omit it to list
  recent talks via ``chatGetTalks``)
- ``--count``: number of messages / talks (clamped to 1-200)
- ``--raw``: raw response JSON, ``--json``: parsed JSON
"""

import json
import logging
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from rich import box
from rich.console import Console
from rich.table import Table

from hw_genie.core.client import HWClient, ResponseStatus, _safe_int, resolve_account
from hw_genie.core.utils import display_timezone_name, format_timestamp_for_display

logger = logging.getLogger(__name__)

CHAT_TYPES = ("clan", "training", "xgvg", "server", "personal")

HEADER_DATETIME = "Time"
HEADER_SENDER = "Sender"
HEADER_MESSAGE = "Message"

# Rich Console の再利用（出力の一貫性とパフォーマンス）
_console = Console()


class ChatResult(list):  # type: ignore[type-arg]
    """``list[ChatMessage]`` のサブクラスで成功/失敗フラグを保持。

    既存テストの ``assert messages == []`` 等との互換性のため ``list`` を継承し、
    ``success`` 属性で API 成功/失敗を区別する。空チャット（成功）と API 失敗を
    ``[]`` だけで区別できない問題を解消する。
    """

    def __init__(self, iterable: Any = (), *, success: bool = True) -> None:
        super().__init__(iterable if iterable is not None else ())
        self.success: bool = success


def _format_ctime(ctime: Any) -> str:
    """epoch 秒（str/int）を表示タイムゾーンの ``YYYY-MM-DD HH:MM`` に変換。"""
    ts = _safe_int(ctime, 0)
    if not ts:
        return "-"
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    formatted = format_timestamp_for_display(iso)
    # format_timestamp_for_display は ``YYYY-MM-DD HH:MM:SS`` を返すので秒を落とすかそのまま表示
    # チャットでは分までで十分なので秒を削る（ただし秒まで欲しい場合はそのまま使う）
    # ここでは読みやすさ優先で ``YYYY-MM-DD HH:MM`` にする
    if len(formatted) >= 16:
        # "2026-04-06 07:02:00" -> "2026-04-06 07:02"
        return formatted[:16]
    return formatted


def _format_ctime_full(ctime: Any) -> str:
    """期間表示用のフルフォーマット（秒まで）。"""
    ts = _safe_int(ctime, 0)
    if not ts:
        return "-"
    iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return format_timestamp_for_display(iso)


@dataclass
class ChatMessage:
    id: str
    user_id: str
    name: str
    level: int
    ctime: int
    message_type: str
    text: str | None = None
    sticker_id: int | None = None

    @property
    def display_text(self) -> str:
        if self.message_type == "sticker":
            return f"sticker:{self.sticker_id}" if self.sticker_id is not None else "sticker"
        return self.text or ""

    @property
    def display_time(self) -> str:
        return _format_ctime(self.ctime)


def parse_chat_response(response: dict[str, Any]) -> list[ChatMessage]:
    """``chatGetAll`` の ``response``（``{chat: [...], users: {...}}``）をパースする。"""
    if not isinstance(response, dict):
        logger.warning("chatGetAll response is not a dict: %r", type(response))
        return []
    chat_list = response.get("chat")
    users = response.get("users") if isinstance(response.get("users"), dict) else {}
    if not isinstance(chat_list, list):
        logger.warning("chatGetAll response.chat is not a list: %r", type(chat_list))
        return []
    messages: list[ChatMessage] = []
    for item in chat_list:
        if not isinstance(item, dict):
            continue
        # None バグ対策: str(None) == "None" を避け、None は空文字にフォールバック
        msg_id = str(item.get("id") or "")
        user_id = str(item.get("userId") or "")
        ctime = _safe_int(item.get("ctime"), 0)
        message_type = str(item.get("messageType") or "text")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        text = data.get("text") if isinstance(data.get("text"), str) else None
        sticker_id = _safe_int(data.get("stickerId")) if data.get("stickerId") is not None else None
        user_info = users.get(user_id) if isinstance(users, dict) else None
        if isinstance(user_info, dict):
            name = str(user_info.get("name", user_id))
            level = _safe_int(user_info.get("level"), 0)
        else:
            name = "Unknown" if not user_id else f"{user_id} (left?)"
            level = 0
        messages.append(
            ChatMessage(
                id=msg_id,
                user_id=user_id,
                name=name,
                level=level,
                ctime=ctime,
                message_type=message_type,
                text=text,
                sticker_id=sticker_id if message_type == "sticker" else None,
            )
        )
    # 古い順にソート（ctime 昇順、欠損は 0 として先頭）
    messages.sort(key=lambda m: m.ctime)
    return messages


@dataclass
class ChatTalk:
    """``chatGetTalks`` が返す個人チャットの会話1件（相手ごとの最新メッセージ）。"""

    user_id: str
    utime: int
    message_type: str
    last_message: str
    seen: int = 0
    read: int = 0
    name: str | None = None

    @property
    def display_time(self) -> str:
        return _format_ctime(self.utime)

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.user_id})" if self.name else self.user_id

    @property
    def display_text(self) -> str:
        return self.last_message or f"({self.message_type})"


def parse_talks_response(response: dict[str, Any]) -> list[ChatTalk]:
    """``chatGetTalks`` の ``response``（``{talks: [...]}``）をパースする。"""
    if not isinstance(response, dict):
        logger.warning("chatGetTalks response is not a dict: %r", type(response))
        return []
    talks = response.get("talks")
    if not isinstance(talks, list):
        logger.warning("chatGetTalks response.talks is not a list: %r", type(talks))
        return []
    parsed: list[ChatTalk] = []
    for item in talks:
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("userId") or "")
        if not user_id:
            continue
        parsed.append(
            ChatTalk(
                user_id=user_id,
                utime=_safe_int(item.get("utime"), 0),
                message_type=str(item.get("lastMessageType") or "text"),
                last_message=str(item.get("lastMessage") or ""),
                seen=_safe_int(item.get("seen"), 0),
                read=_safe_int(item.get("read"), 0),
            )
        )
    # サーバーは utime 降順で返すが、欠損や順序変更に備えて明示的に並べる
    parsed.sort(key=lambda t: t.utime, reverse=True)
    return parsed


def fetch_talk_names(client: HWClient, talks: list[ChatTalk]) -> list[ChatTalk]:
    """``userGetInfoByIds`` で会話相手の名前を一括解決する（失敗時は ID 表示のまま）。"""
    if not talks:
        return talks
    res = client.user_get_info_by_ids([t.user_id for t in talks])
    if res.status != ResponseStatus.SUCCESS:
        logger.warning("userGetInfoByIds failed: %s (%s)", res.status, res.error_name or "-")
        return talks
    response = res.detail.get("response") if isinstance(res.detail, dict) else None
    users = response.get("users") if isinstance(response, dict) else None
    if not isinstance(users, list):
        return talks
    names = {
        str(u.get("id")): str(u.get("name")).strip()
        for u in users
        if isinstance(u, dict) and u.get("id") and u.get("name")
    }
    return [replace(t, name=names.get(t.user_id)) for t in talks]


def summarize_chat(messages: list[ChatMessage]) -> dict[str, Any]:
    """Generate statistical summary and natural language summary."""
    if not messages:
        return {
            "count": 0,
            "period": "-",
            "participants": 0,
            "top_speakers": [],
            "text_count": 0,
            "sticker_count": 0,
            "peak_hour": None,
            "largest_gap_hours": None,
            "summary_text": "No messages.",
        }
    # 未ソートでも duration が負にならないようにソートしてから計算
    messages = sorted(messages, key=lambda m: m.ctime)
    count = len(messages)
    oldest = messages[0]
    newest = messages[-1]
    period = f"{_format_ctime_full(oldest.ctime)} 〜 {_format_ctime_full(newest.ctime)}"
    # 期間日数
    duration_days = max(1, (newest.ctime - oldest.ctime) // 86400 + 1) if oldest.ctime and newest.ctime else 1
    per_day = round(count / duration_days, 1) if duration_days else count

    # 発言者別
    speaker_counter = Counter(m.name for m in messages)
    top_speakers = speaker_counter.most_common(3)
    participants = len(speaker_counter)

    text_count = sum(1 for m in messages if m.message_type == "text")
    sticker_count = sum(1 for m in messages if m.message_type == "sticker")

    # 時間帯別: 表示TZ を考慮した hour で集計（ZoneInfo はループ外でキャッシュ）
    tz = None
    tz_env = os.environ.get("HWGENIE_TZ")
    if tz_env:
        try:
            tz = ZoneInfo(tz_env)
        except Exception:
            tz = None
    hour_counter = Counter()
    for m in messages:
        if not m.ctime:
            continue
        try:
            dt = datetime.fromtimestamp(m.ctime, tz=timezone.utc)
            if tz is not None:
                dt = dt.astimezone(tz)
            hour_counter[dt.hour] += 1
        except Exception:
            continue
    peak_hour = hour_counter.most_common(1)[0][0] if hour_counter else None

    # 最大ギャップ
    largest_gap_hours = None
    if count > 1:
        gaps = [messages[i + 1].ctime - messages[i].ctime for i in range(count - 1) if messages[i].ctime and messages[i + 1].ctime]
        if gaps:
            largest_gap_hours = round(max(gaps) / 3600, 1)

    tz_label = display_timezone_name()
    summary_lines: list[str] = []
    summary_lines.append(
        f"Latest {count} messages: {period} ({tz_label}, ~{duration_days} days, avg {per_day}/day)."
    )
    if top_speakers:
        speakers_text = ", ".join(f"{name} ({cnt})" for name, cnt in top_speakers)
        summary_lines.append(f"Participants: {participants}, top speakers: {speakers_text}.")
    if sticker_count and text_count:
        ratio = round(sticker_count / count * 100)
        summary_lines.append(f"Types: text {text_count}, sticker {sticker_count} ({ratio}% stickers).")
    elif sticker_count:
        summary_lines.append("All messages are stickers.")
    if peak_hour is not None:
        summary_lines.append(f"Peak hour: {peak_hour}:00.")
    if largest_gap_hours is not None and largest_gap_hours >= 24:
        summary_lines.append(f"Longest silence: ~{largest_gap_hours} hours.")
    summary_text = " ".join(summary_lines)

    return {
        "count": count,
        "period": period,
        "duration_days": duration_days,
        "per_day": per_day,
        "participants": participants,
        "top_speakers": top_speakers,
        "text_count": text_count,
        "sticker_count": sticker_count,
        "peak_hour": peak_hour,
        "largest_gap_hours": largest_gap_hours,
        "summary_text": summary_text,
    }


def print_chat_table(messages: list[ChatMessage], account: str, chat_type: str, user_id: str | None = None) -> None:
    """Display chat as table."""
    tz_label = display_timezone_name()
    chat_label = f"{chat_type}:{user_id}" if chat_type == "personal" and user_id else chat_type
    table = Table(
        title=f"Chat ({chat_label}) - {account}  latest {len(messages)}  [{tz_label}]",
        box=box.ROUNDED,
        show_lines=False,
        header_style="bold cyan",
    )
    table.add_column(HEADER_DATETIME, style="dim", no_wrap=True, justify="left")
    table.add_column(HEADER_SENDER, style="bold", no_wrap=False, overflow="fold")
    table.add_column(HEADER_MESSAGE, no_wrap=False, overflow="fold", ratio=1)

    for m in messages:
        # 長文は 200 文字で切り詰め（表幅を考慮し崩れ防止）。rich の fold で折り返すが、
        # 極端に長い 1 メッセージで表全体が縦に伸びるのを防ぐため。
        text = m.display_text
        if len(text) > 200:
            text = text[:197] + "..."
        table.add_row(m.display_time, m.name, text)

    _console.print(table)


def print_talks_table(talks: list[ChatTalk], account: str) -> None:
    """Display personal talk list (newest first)."""
    tz_label = display_timezone_name()
    table = Table(
        title=f"Personal talks - {account}  latest {len(talks)}  [{tz_label}]",
        box=box.ROUNDED,
        show_lines=False,
        header_style="bold cyan",
    )
    table.add_column(HEADER_DATETIME, style="dim", no_wrap=True, justify="left")
    table.add_column("Partner", style="bold", no_wrap=False, overflow="fold")
    table.add_column("Type", style="dim", no_wrap=True)
    table.add_column(HEADER_MESSAGE, no_wrap=False, overflow="fold", ratio=1)

    for t in talks:
        text = t.display_text
        if len(text) > 200:
            text = text[:197] + "..."
        table.add_row(t.display_time, t.display_name, t.message_type, text)

    _console.print(table)


def print_chat_summary(summary: dict[str, Any]) -> None:
    """Print statistical summary."""
    print("\n📊 --- Summary ---")
    print(f"  Period: {summary.get('period', '-')}")
    print(f"  Count: {summary.get('count', 0)} (text {summary.get('text_count', 0)} / sticker {summary.get('sticker_count', 0)})")
    print(f"  Participants: {summary.get('participants', 0)}")
    top = summary.get("top_speakers") or []
    if top:
        print("  Top speakers:")
        for name, cnt in top:
            print(f"    - {name}: {cnt}")
    if summary.get("peak_hour") is not None:
        print(f"  Peak hour: {summary['peak_hour']}:00")
    if summary.get("largest_gap_hours") is not None:
        print(f"  Longest gap: ~{summary['largest_gap_hours']}h")
    print("\n📝 Summary:")
    print(f"  {summary.get('summary_text', '-')}")


def _run_personal_talks(
    client: HWClient,
    account: str,
    count_int: int,
    *,
    raw: bool,
    json_output: bool,
    last_id: str | None,
) -> ChatResult:
    """個人チャットの会話一覧（chatGetTalks）を表示する。"""
    if last_id is not None:
        print(
            "❌ --last-id is not supported for the personal talk list (use --user-id to fetch a thread).",
            file=sys.stderr,
        )
        return ChatResult([], success=False)

    res = client.chat_get_talks()
    if res.status != ResponseStatus.SUCCESS:
        print(f"❌ Failed to fetch personal talks: {res.status.value} ({res.error_name or '-'})", file=sys.stderr)
        return ChatResult([], success=False)

    raw_data = res.detail.get("response") if isinstance(res.detail, dict) else None
    if raw_data is None or not isinstance(raw_data, dict) or "talks" not in raw_data:
        print("❌ Unexpected chatGetTalks response format.", file=sys.stderr)
        logger.warning("Unexpected chatGetTalks detail format: %r", res.detail)
        return ChatResult([], success=False)

    if raw:
        print(json.dumps(raw_data, ensure_ascii=False, indent=1))
        return ChatResult(parse_talks_response(raw_data), success=True)

    talks = parse_talks_response(raw_data)
    # サーバーは utime 降順で返すので、最新 count 件は先頭のスライスになる
    if len(talks) > count_int:
        talks = talks[:count_int]
    talks = fetch_talk_names(client, talks)

    if json_output:
        print(json.dumps([asdict(t) for t in talks], ensure_ascii=False, indent=1))
        return ChatResult(talks, success=True)

    if not talks:
        print(f"ℹ️  [{account}] No personal talks found.")
        return ChatResult([], success=True)

    print_talks_table(talks, account)
    return ChatResult(talks, success=True)


def run_chat(
    client: HWClient,
    account_alias: str | None = None,
    chat_type: str = "clan",
    count: int = 50,
    raw: bool = False,
    json_output: bool = False,
    last_id: str | None = None,
    user_id: str | None = None,
) -> ChatResult:
    """Fetch chat and display as table + summary.

    Args:
        client: authenticated HWClient
        account_alias: account alias for display
        chat_type: chatType to fetch
        count: number of messages / talks (clamped to 1-200)
        raw: print raw response JSON
        json_output: print parsed messages as JSON
        last_id: pagination (fetch before this ID; not supported for the
            personal talk list)
        user_id: peer userId for ``chat_type="personal"``. Omit it to list recent
            personal talks (``chatGetTalks``, newest first).

    Returns:
        Parsed ChatMessage list (or ChatTalk list for the personal talk list mode),
        wrapped in ``ChatResult`` with its ``success`` flag.
    """
    if raw and json_output:
        print("❌ --raw and --json cannot be used together.", file=sys.stderr)
        return ChatResult([], success=False)

    account = resolve_account(account_alias)
    if chat_type not in CHAT_TYPES:
        print(f"❌ Unknown chatType: {chat_type} (choices: {', '.join(CHAT_TYPES)})", file=sys.stderr)
        return ChatResult([], success=False)

    # 指定有無は strip 前の値で判定する（空白のみの指定も「指定あり」として扱う）。
    user_id_supplied = user_id is not None
    user_id = str(user_id).strip() if user_id_supplied else None
    if chat_type == "personal":
        # userId 省略時は会話一覧（chatGetTalks）、指定時は個別スレッドを取得する。
        if user_id_supplied and not user_id:
            print("❌ --user-id must not be empty.", file=sys.stderr)
            return ChatResult([], success=False)
        # str.isdigit は非 ASCII 数字も通すため isascii と併用する。
        if user_id and not (user_id.isascii() and user_id.isdigit()):
            print(f"❌ Invalid --user-id: {user_id!r} (numeric userId required).", file=sys.stderr)
            return ChatResult([], success=False)
    else:
        # 非 personal では空白のみの指定もエラーにする（payload に空 userId を送らない）。
        if user_id_supplied:
            print("❌ --user-id is only valid with --type personal.", file=sys.stderr)
            return ChatResult([], success=False)
        user_id = None

    try:
        count_int = int(count) if count is not None else 50
    except (TypeError, ValueError):
        print(f"❌ Invalid count: {count!r}", file=sys.stderr)
        return ChatResult([], success=False)
    # 会話一覧のスライスと userGetInfoByIds の送信件数の両方に効くよう、ここで 1-200 にクランプする
    # （chat_get_all / ドキュメントが約束する範囲と揃える）。
    count_int = max(1, min(200, count_int))

    if chat_type == "personal" and user_id is None:
        return _run_personal_talks(
            client, account, count_int, raw=raw, json_output=json_output, last_id=last_id
        )

    res = client.chat_get_all(chat_type=chat_type, count=count_int, last_id=last_id, user_id=user_id)
    if res.status != ResponseStatus.SUCCESS:
        print(f"❌ Failed to fetch chat: {res.status.value} ({res.error_name or '-'})", file=sys.stderr)
        return ChatResult([], success=False)

    raw_data = res.detail.get("response") if isinstance(res.detail, dict) else None
    if raw_data is None or not isinstance(raw_data, dict) or "chat" not in raw_data:
        print("❌ Unexpected chatGetAll response format.", file=sys.stderr)
        logger.warning("Unexpected chatGetAll detail format: %r", res.detail)
        return ChatResult([], success=False)

    if raw:
        # raw はサーバーレスポンスをそのまま表示（count はサーバー側で無視される場合があるため raw は全件表示）
        print(json.dumps(raw_data, ensure_ascii=False, indent=1))
        # 戻り値はパース済みだが、count でのスライシングは行わない（raw はデバッグ用）
        return ChatResult(parse_chat_response(raw_data), success=True)

    messages = parse_chat_response(raw_data)
    # サーバーが count を無視して常に 50 件返す場合があるため、クライアント側でスライシングして count を尊重する
    # messages は ctime 昇順にソート済みなので、最新 count 件は末尾のスライスになる
    if count_int is not None and len(messages) > count_int:
        messages = messages[-count_int:]
    if json_output:
        # JSON モードは空結果でも必ず ``[]`` を出す（stdout を常にパース可能に保つ）。
        data = [asdict(m) for m in messages]
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return ChatResult(messages, success=True)

    if not messages:
        chat_label = f"personal: {user_id}" if chat_type == "personal" else chat_type
        print(f"ℹ️  [{account}] No chat history found ({chat_label}).")
        if chat_type == "personal":
            print(
                "   Note: the server may return only unread PMs for a conversation; "
                "messages already read in-game can come back empty.",
                file=sys.stderr,
            )
        return ChatResult([], success=True)

    # 通常表示: 表＋要約
    print_chat_table(messages, account, chat_type, user_id)
    summary = summarize_chat(messages)
    print_chat_summary(summary)
    return ChatResult(messages, success=True)
