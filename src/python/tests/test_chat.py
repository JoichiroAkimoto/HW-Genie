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


MOCK_TALKS_RESPONSE = {
    "talks": [
        {"userId": "111", "lastMessage": "older msg", "lastMessageType": "text", "utime": "1000", "seen": "0", "read": "0"},
        {"userId": "222", "lastMessage": "", "lastMessageType": "challenge", "utime": "3000", "seen": "0", "read": "0"},
        {"userId": "333", "lastMessage": "newest msg", "lastMessageType": "text", "utime": "2000", "seen": "0", "read": "0"},
    ]
}

MOCK_TALK_USERS = [
    {"id": "111", "name": "Alice"},
    {"id": "222", "name": "Bob"},
    {"id": "333", "name": "Carol"},
]


def _make_client(
    response: dict | None = None,
    status: ResponseStatus = ResponseStatus.SUCCESS,
    error_name: str | None = None,
    talks: dict | None = None,
    talk_users: list | None = None,
    names_status: ResponseStatus = ResponseStatus.SUCCESS,
) -> HWClient:
    client = HWClient(headers={"x-auth-token": "test"})
    res = MagicMock()
    res.status = status
    res.error_name = error_name
    res.detail = {"response": response if response is not None else MOCK_CHAT_RESPONSE}
    client.chat_get_all = MagicMock(return_value=res)

    talks_res = MagicMock()
    talks_res.status = status
    talks_res.error_name = error_name
    talks_res.detail = {"response": talks if talks is not None else MOCK_TALKS_RESPONSE}
    client.chat_get_talks = MagicMock(return_value=talks_res)

    names_res = MagicMock()
    names_res.status = names_status
    names_res.error_name = None
    names_res.detail = {"response": {"users": talk_users if talk_users is not None else MOCK_TALK_USERS}}
    client.user_get_info_by_ids = MagicMock(return_value=names_res)
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


def _register_session(alias: str) -> None:
    from hw_genie.core.session_manager import SessionManager

    SessionManager.repo.save_data(
        alias,
        {"headers": {"x-auth-token": "t"}, "player": {"id": "c1", "name": alias}},
    )


# --- run_chat: personal (PM) ---

def test_parse_talks_response_sorts_newest_first():
    from hw_genie.commands.chat import parse_talks_response

    talks = parse_talks_response(MOCK_TALKS_RESPONSE)
    assert [t.user_id for t in talks] == ["222", "333", "111"]
    assert talks[0].message_type == "challenge"
    assert talks[0].display_text == "(challenge)"
    assert talks[1].display_text == "newest msg"
    assert parse_talks_response({}) == []
    assert parse_talks_response({"talks": "nope"}) == []


def test_run_chat_personal_lists_talks_with_names(capsys, monkeypatch):
    monkeypatch.setenv("HWGENIE_TZ", "UTC")
    _register_session("TestUser6")
    client = _make_client()
    result = run_chat(client, account_alias="TestUser6", chat_type="personal", count=2)
    assert result.success
    client.chat_get_talks.assert_called_once_with()
    client.chat_get_all.assert_not_called()
    # count で最新2件に絞られる（utime 降順: 222, 333）
    assert [t.user_id for t in result] == ["222", "333"]
    client.user_get_info_by_ids.assert_called_once_with(["222", "333"])
    out = capsys.readouterr().out
    assert "Personal talks" in out
    assert "Bob (222)" in out
    assert "Carol (333)" in out
    assert "Alice (111)" not in out


def test_run_chat_personal_talks_json(capsys):
    _register_session("TestUser20")
    client = _make_client()
    run_chat(client, account_alias="TestUser20", chat_type="personal", json_output=True)
    payload = json.loads(capsys.readouterr().out)
    assert [row["user_id"] for row in payload] == ["222", "333", "111"]
    assert payload[0]["name"] == "Bob"
    assert payload[0]["message_type"] == "challenge"


def test_run_chat_personal_talks_names_failure_falls_back_to_id(capsys):
    _register_session("TestUser21")
    client = _make_client(names_status=ResponseStatus.ERROR)
    result = run_chat(client, account_alias="TestUser21", chat_type="personal")
    assert result.success
    assert result[0].name is None
    assert "222" in capsys.readouterr().out


def test_run_chat_personal_talks_empty(capsys):
    _register_session("TestUser22")
    client = _make_client(talks={"talks": []})
    result = run_chat(client, account_alias="TestUser22", chat_type="personal")
    assert result == []
    assert result.success
    assert "No personal talks found" in capsys.readouterr().out


def test_run_chat_personal_talks_rejects_last_id(capsys):
    _register_session("TestUser23")
    client = _make_client()
    result = run_chat(client, account_alias="TestUser23", chat_type="personal", last_id="1")
    assert not result.success
    assert "--last-id" in capsys.readouterr().err
    client.chat_get_talks.assert_not_called()


def test_run_chat_personal_talks_failure(capsys):
    _register_session("TestUser24")
    client = _make_client(status=ResponseStatus.ERROR, error_name="InvalidSession")
    result = run_chat(client, account_alias="TestUser24", chat_type="personal")
    assert not result.success
    assert "Failed to fetch personal talks" in capsys.readouterr().err


def test_run_chat_personal_talks_raw_skips_name_lookup(capsys):
    _register_session("TestUser30")
    client = _make_client()
    result = run_chat(client, account_alias="TestUser30", chat_type="personal", count=1, raw=True)
    assert result.success
    payload = json.loads(capsys.readouterr().out)
    assert "talks" in payload
    # raw はデバッグ用でスライスしない
    assert len(payload["talks"]) == 3
    assert len(result) == 3
    client.user_get_info_by_ids.assert_not_called()


def test_run_chat_personal_talks_unexpected_format(capsys):
    _register_session("TestUser31")
    client = _make_client(talks={})
    result = run_chat(client, account_alias="TestUser31", chat_type="personal")
    assert not result.success
    assert "Unexpected chatGetTalks response format" in capsys.readouterr().err


def test_run_chat_personal_talks_json_empty(capsys):
    _register_session("TestUser32")
    client = _make_client(talks={"talks": []})
    result = run_chat(client, account_alias="TestUser32", chat_type="personal", json_output=True)
    assert result.success
    assert json.loads(capsys.readouterr().out) == []


def test_run_chat_personal_talks_count_clamped(capsys):
    # utime 降順（10000 が最新）。user_id は 1000..1204。
    talks = [
        {
            "userId": str(1000 + i),
            "lastMessage": f"msg{i}",
            "lastMessageType": "text",
            "utime": str(10000 - i),
        }
        for i in range(205)
    ]
    _register_session("TestUser33")
    client = _make_client(talks={"talks": talks}, talk_users=[])

    result = run_chat(client, account_alias="TestUser33", chat_type="personal", count=500)
    assert result.success
    assert len(result) == 200
    expected_ids = [str(1000 + i) for i in range(200)]
    assert [t.user_id for t in result] == expected_ids
    client.user_get_info_by_ids.assert_called_once_with(expected_ids)

    # count=0 は 1 にクランプされ、最新の1件だけになる
    client.user_get_info_by_ids.reset_mock()
    result = run_chat(client, account_alias="TestUser33", chat_type="personal", count=0)
    assert len(result) == 1
    assert result[0].user_id == "1000"
    client.user_get_info_by_ids.assert_called_once_with(["1000"])


def test_run_chat_personal_rejects_empty_user_id(capsys):
    _register_session("TestUser25")
    client = _make_client()
    result = run_chat(client, account_alias="TestUser25", chat_type="personal", user_id="  ")
    assert not result.success
    assert "must not be empty" in capsys.readouterr().err
    client.chat_get_talks.assert_not_called()


def test_run_chat_user_id_rejected_for_non_personal(capsys):
    _register_session("TestUser7")
    client = _make_client()
    result = run_chat(client, account_alias="TestUser7", chat_type="clan", user_id="123")
    assert not result.success
    assert "only valid with --type personal" in capsys.readouterr().err
    client.chat_get_all.assert_not_called()


def test_run_chat_personal_rejects_non_numeric_user_id(capsys):
    _register_session("TestUser8")
    client = _make_client()
    result = run_chat(client, account_alias="TestUser8", chat_type="personal", user_id="abc")
    assert not result.success
    assert "numeric userId" in capsys.readouterr().err
    client.chat_get_all.assert_not_called()


def test_run_chat_personal_passes_user_id(capsys, monkeypatch):
    monkeypatch.setenv("HWGENIE_TZ", "UTC")
    _register_session("TestUser9")
    client = _make_client()
    result = run_chat(client, account_alias="TestUser9", chat_type="personal", user_id=" 30520858 ")
    assert result.success
    client.chat_get_all.assert_called_once_with(
        chat_type="personal", count=50, last_id=None, user_id="30520858"
    )
    assert "personal:30520858" in capsys.readouterr().out


def test_run_chat_personal_empty_mentions_unread_behavior(capsys):
    _register_session("TestUser10")
    client = _make_client(response={"chat": [], "users": {}})
    result = run_chat(client, account_alias="TestUser10", chat_type="personal", user_id="30520858")
    assert result == []
    assert result.success
    captured = capsys.readouterr()
    assert "personal: 30520858" in captured.out
    # unread の注意書きは stdout をパース可能に保つため stderr に出す
    assert "unread" in captured.err
    assert "unread" not in captured.out


def test_run_chat_personal_empty_json_prints_empty_list(capsys):
    _register_session("TestUser11")
    client = _make_client(response={"chat": [], "users": {}})
    result = run_chat(
        client,
        account_alias="TestUser11",
        chat_type="personal",
        user_id="30520858",
        json_output=True,
    )
    assert result.success
    captured = capsys.readouterr()
    # stdout は必ずパース可能な JSON（空リスト）
    assert json.loads(captured.out) == []
    # 人間向けメッセージは JSON モードでは出さない
    assert "No chat history found" not in captured.out


def test_run_chat_non_personal_rejects_whitespace_user_id(capsys):
    _register_session("TestUser12")
    client = _make_client()
    result = run_chat(client, account_alias="TestUser12", chat_type="clan", user_id="  ")
    assert not result.success
    assert "only valid with --type personal" in capsys.readouterr().err
    client.chat_get_all.assert_not_called()


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
    assert "userId" not in called["payload"]["calls"][0]["args"]

    client.chat_get_all(chat_type="server", count=5, last_id="123")
    assert called["payload"]["calls"][0]["args"]["lastId"] == "123"
    assert called["payload"]["calls"][0]["args"]["chatType"] == "server"

    # 空文字の user_id は payload に userId を載せない
    client.chat_get_all(chat_type="clan", user_id="")
    assert "userId" not in called["payload"]["calls"][0]["args"]

    client.chat_get_all(chat_type="personal", count=50, user_id="30520858")
    args = called["payload"]["calls"][0]["args"]
    assert args["chatType"] == "personal"
    assert args["userId"] == "30520858"


def test_client_talk_methods_payload():
    client = HWClient(headers={"x-auth-token": "tok", "x-request-id": "5"})
    called = {}

    def fake_call(payload):
        called["payload"] = payload
        res = MagicMock()
        res.status = ResponseStatus.SUCCESS
        res.detail = {"response": {"talks": [], "users": []}}
        return res

    client.call = fake_call  # type: ignore
    client.chat_get_talks()
    call = called["payload"]["calls"][0]
    assert call["name"] == ApiAction.CHAT_GET_TALKS
    assert call["args"] == {}

    client.user_get_info_by_ids(["111", 222])
    call = called["payload"]["calls"][0]
    assert call["name"] == ApiAction.USER_GET_INFO_BY_IDS
    assert call["args"] == {"ids": ["111", "222"]}


def test_chat_parser_accepts_personal_user_id(monkeypatch):
    import sys

    import hw_genie.main as main_mod

    captured: dict = {}
    monkeypatch.setattr(
        sys,
        "argv",
        ["hw-genie", "chat", "-a", "Alice", "--type", "personal", "--user-id", "30520858"],
    )
    monkeypatch.setattr(main_mod, "cmd_chat", lambda args: captured.update(vars(args)))
    monkeypatch.setattr("hw_genie.core.database.init_db", lambda: None)
    monkeypatch.setattr("hw_genie.core.database.install_token_masking_filter", lambda: None)
    monkeypatch.setattr(main_mod, "setup_logging", lambda debug=False: None)
    main_mod.main()
    assert captured["type"] == "personal"
    assert captured["user_id"] == "30520858"
