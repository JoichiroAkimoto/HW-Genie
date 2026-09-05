"""``hw_genie.commands.chat`` のテスト。

chatGetAll の実レスポンス（CHAT_API.md / docs/superpowers/chat.md）を模したデータで
パース・表示・要約を検証する。
"""

import json
from unittest.mock import MagicMock

from hw_genie.commands.chat import (
    _format_ctime,
    parse_chat_response,
    run_chat,
    summarize_chat,
)
from hw_genie.core.client import ApiAction, HWClient, ResponseStatus


MOCK_CHAT_RESPONSE = {
    "chat": [
        {
            "id": "144249176",
            "userId": "26754937",
            "messageType": "text",
            "ctime": "1775667274",
            "data": {"ids": [], "text": "メッセージ内容テスト"},
        },
        {
            "id": "144192792",
            "userId": "62081929",
            "messageType": "sticker",
            "ctime": "1775459356",
            "data": {"ids": [], "stickerId": 13},
        },
        {
            "id": "144250000",
            "userId": "26754937",
            "messageType": "text",
            "ctime": "1775668000",
            "data": {"ids": [], "text": "hello world hello"},
        },
        {
            "id": "144251000",
            "userId": "99999999",
            "messageType": "text",
            "ctime": "1775669000",
            "data": {"ids": [], "text": "こんにちは ギルド戦 頑張ろう"},
        },
    ],
    "users": {
        "26754937": {"id": "26754937", "name": "Alice", "level": "130", "clanRole": "4", "avatarId": "1617"},
        "62081929": {"id": "62081929", "name": "Bob", "level": "125", "clanRole": "4", "avatarId": "1618"},
    },
}


def _make_client(response: dict | None = None, status: ResponseStatus = ResponseStatus.SUCCESS, error_name: str | None = None) -> HWClient:
    client = HWClient(headers={"x-auth-token": "test"})
    res = MagicMock()
    res.status = status
    res.error_name = error_name
    res.detail = {"response": response if response is not None else MOCK_CHAT_RESPONSE}
    client.chat_get_all = MagicMock(return_value=res)
    return client


# --- _format_ctime ---


def test_format_ctime_valid(monkeypatch):
    monkeypatch.setenv("HWGENIE_TZ", "UTC")
    # 1775667274 -> UTC: check starts with year
    out = _format_ctime("1775667274")
    assert "2026" in out or "1775667274" not in out  # formatted
    assert out != "-"
    assert _format_ctime(0) == "-"
    assert _format_ctime(None) == "-"


# --- parse_chat_response ---


def test_parse_chat_response_normalizes():
    messages = parse_chat_response(MOCK_CHAT_RESPONSE)
    assert len(messages) == 4
    # ソートで ctime 昇順
    assert messages[0].id == "144192792"  # oldest ctime 1775459356
    assert messages[-1].id == "144251000"
    # ユーザー名解決
    by_id = {m.id: m for m in messages}
    assert by_id["144249176"].name == "Alice"
    assert by_id["144249176"].level == 130
    assert by_id["144192792"].message_type == "sticker"
    assert by_id["144192792"].sticker_id == 13
    assert by_id["144192792"].display_text == "sticker:13"
    assert by_id["144249176"].text == "メッセージ内容テスト"
    assert by_id["144249176"].display_text == "メッセージ内容テスト"
    # 不在ユーザは fallback
    assert "99999999" in by_id["144251000"].name
    assert "left?" in by_id["144251000"].name


def test_parse_chat_response_empty_and_invalid():
    assert parse_chat_response({}) == []
    assert parse_chat_response({"chat": "not-a-list"}) == []
    assert parse_chat_response(None) == []  # type: ignore
    # users なしでも動く
    msgs = parse_chat_response({"chat": [{"id": "1", "userId": "1", "ctime": "1000", "messageType": "text", "data": {"text": "hi"}}]})
    assert len(msgs) == 1
    assert msgs[0].name == "1 (left?)"


def test_parse_chat_response_sort_and_sticker_text():
    resp = {
        "chat": [
            {"id": "2", "userId": "1", "ctime": "2000", "messageType": "text", "data": {"text": "second"}},
            {"id": "1", "userId": "1", "ctime": "1000", "messageType": "text", "data": {"text": "first"}},
        ],
        "users": {"1": {"name": "T", "level": "10"}},
    }
    msgs = parse_chat_response(resp)
    assert msgs[0].id == "1"
    assert msgs[1].id == "2"


# --- summarize_chat ---


def test_summarize_chat_basic():
    messages = parse_chat_response(MOCK_CHAT_RESPONSE)
    summary = summarize_chat(messages)
    assert summary["count"] == 4
    assert summary["participants"] == 3  # Alice, Bob, 99999999
    assert summary["text_count"] == 3
    assert summary["sticker_count"] == 1
    # top speakers
    assert summary["top_speakers"][0][0] == "Alice"
    assert summary["top_speakers"][0][1] == 2
    assert "Latest" in summary["summary_text"]
    assert "Participants" in summary["summary_text"]
    assert summary.get("keywords") is None or "keywords" not in summary


def test_summarize_chat_empty():
    summary = summarize_chat([])
    assert summary["count"] == 0
    assert summary["summary_text"] == "No messages."


def test_summarize_chat_all_stickers():
    resp = {
        "chat": [
            {"id": "1", "userId": "1", "ctime": "1000", "messageType": "sticker", "data": {"stickerId": 1}},
            {"id": "2", "userId": "1", "ctime": "2000", "messageType": "sticker", "data": {"stickerId": 2}},
        ],
        "users": {"1": {"name": "T", "level": "1"}},
    }
    msgs = parse_chat_response(resp)
    summary = summarize_chat(msgs)
    assert summary["sticker_count"] == 2
    assert summary["text_count"] == 0
    assert "All messages are stickers" in summary["summary_text"]


# --- run_chat ---


def test_run_chat_success_table_and_summary(capsys, monkeypatch):
    from hw_genie.core.session_manager import SessionManager

    monkeypatch.setenv("HWGENIE_TZ", "UTC")
    SessionManager.repo.save_data(
        "TestUser",
        {"headers": {"x-auth-token": "t"}, "player": {"id": "c1", "name": "TestUser"}},
    )
    client = _make_client()
    # inject client.chat_get_all mock already set
    # run_chat は内部で resolve_account するので SessionManager にデータが必要
    messages = run_chat(client, account_alias="TestUser", chat_type="clan", count=50)
    out = capsys.readouterr().out
    assert len(messages) == 4
    assert "Time" in out
    assert "Sender" in out
    assert "Message" in out
    assert "Summary" in out
    assert "Chat" in out
    # メッセージ内容が表示される
    assert "Alice" in out
    assert "メッセージ内容テスト" in out


def test_run_chat_raw(capsys):
    from hw_genie.core.session_manager import SessionManager

    SessionManager.repo.save_data(
        "TestUser2",
        {"headers": {"x-auth-token": "t"}, "player": {"id": "c1", "name": "TestUser2"}},
    )
    client = _make_client()
    run_chat(client, account_alias="TestUser2", raw=True)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "chat" in payload
    assert "users" in payload


def test_run_chat_json(capsys):
    from hw_genie.core.session_manager import SessionManager

    SessionManager.repo.save_data(
        "TestUser3",
        {"headers": {"x-auth-token": "t"}, "player": {"id": "c1", "name": "TestUser3"}},
    )
    client = _make_client()
    run_chat(client, account_alias="TestUser3", json_output=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["name"] == "Bob"  # sorted oldest first


def test_run_chat_failure(capsys):
    from hw_genie.core.session_manager import SessionManager

    SessionManager.repo.save_data(
        "TestUser4",
        {"headers": {"x-auth-token": "t"}, "player": {"id": "c1", "name": "TestUser4"}},
    )
    client = _make_client(status=ResponseStatus.ERROR, error_name="InvalidSession")
    messages = run_chat(client, account_alias="TestUser4")
    assert messages == []


def test_run_chat_empty(capsys):
    from hw_genie.core.session_manager import SessionManager

    SessionManager.repo.save_data(
        "TestUser5",
        {"headers": {"x-auth-token": "t"}, "player": {"id": "c1", "name": "TestUser5"}},
    )
    client = _make_client(response={"chat": [], "users": {}})
    messages = run_chat(client, account_alias="TestUser5")
    assert messages == []
    out = capsys.readouterr().out
    assert "No chat history found" in out


def test_client_chat_get_all_payload():
    client = HWClient(headers={"x-auth-token": "tok", "x-request-id": "5"})
    # セッションをモックして call を検証
    called = {}

    def fake_call(payload):
        called["payload"] = payload
        res = MagicMock()
        res.status = ResponseStatus.SUCCESS
        res.detail = {"response": {"chat": [], "users": {}}}
        return res

    client.call = fake_call  # type: ignore
    client.chat_get_all(chat_type="clan", count=10)
    assert called["payload"]["calls"][0]["name"] == ApiAction.CHAT_GET_ALL
    assert called["payload"]["calls"][0]["args"]["chatType"] == "clan"
    assert called["payload"]["calls"][0]["args"]["count"] == 10

    client.chat_get_all(chat_type="server", count=5, last_id="123")
    assert called["payload"]["calls"][0]["args"]["lastId"] == "123"
    assert called["payload"]["calls"][0]["args"]["chatType"] == "server"
