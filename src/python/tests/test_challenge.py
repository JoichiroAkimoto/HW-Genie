"""``hw_genie.commands.challenge`` のテスト。

Live で観測した chatGetAll / chatSendChallenge / chatAcceptChallenge の
レスポンス形状（messageId は personal スレッドの challenge メッセージの ``id``、
``replay.data.challengeId`` が消費済みマーカー）を模したデータで検証する。
API 呼び出しはすべてモックし、実在アカウント名は使わない。
"""

import json
import logging
import sys
from unittest.mock import MagicMock

import pytest

from hw_genie.commands.challenge import (
    CHALLENGE_DEFAULTS_KEY,
    ChallengeBattleResult,
    ChallengeError,
    ChallengeMessage,
    ChallengeRunResult,
    clear_challenge_defaults,
    fetch_player_id,
    load_challenge_defaults,
    parse_accept_result,
    parse_challenge_messages,
    parse_send_result,
    pending_challenges,
    resolve_challenge_peer,
    resolve_challenge_target,
    run_challenge_accept,
    run_challenge_battle,
    run_challenge_list,
    run_challenge_self_battle,
    run_challenge_send,
    select_challenge_target_team,
    select_challenge_team,
    sent_pending_challenges,
    set_challenge_defaults,
    validate_heroes,
    validate_user_id,
)
from hw_genie.core.client import ApiAction, HWClient, ResponseStatus
from hw_genie.core.session_manager import SessionManager

PEER_ID = "11111111"
SENDER_ID = "22222222"


def _res(detail: dict | None = None, status: ResponseStatus = ResponseStatus.SUCCESS, error_name: str | None = None) -> MagicMock:
    res = MagicMock()
    res.status = status
    res.error_name = error_name
    res.detail = detail if detail is not None else {"response": {}}
    return res


def _challenge_message(
    message_id: int | str,
    user_id: str,
    ctime: int,
    *,
    challenge_type: str = "titan",
    power: int = 1000,
) -> dict:
    return {
        "id": message_id,
        "userId": user_id,
        "messageType": "challenge",
        "ctime": ctime,
        "data": {
            "ids": [],
            "power": power,
            "type": challenge_type,
            "manual": False,
            "maxUpgrade": True,
            "heroes": {},
            "messageType": "challenge",
        },
    }


def _replay_message(message_id: int, challenge_id: int | str, user_id: str, ctime: int) -> dict:
    return {
        "id": message_id,
        "userId": user_id,
        "messageType": "replay",
        "ctime": ctime,
        "data": {
            "ids": [],
            "replayId": str(ctime),
            "enemyId": int(user_id) if user_id.isdigit() else 0,
            "replayType": "challenge_titan",
            "challengeId": challenge_id,
            "messageType": "replay",
        },
    }


def _battle_detail(*, win: bool = True, stars: int = 2, battle_id: str = "1794728303314860997") -> dict:
    return {
        "response": {
            "battle": {
                "userId": PEER_ID,
                "typeId": int(SENDER_ID),
                "attackers": {},
                "defenders": {},
                "effects": {"battleConfig": "titan_pvp"},
                "reward": [],
                "startTime": 1794728303,
                "seed": 885764156,
                "type": "challenge_titan",
                "id": battle_id,
                "progress": [],
                "endTime": 1794728303,
                "result": {"win": win, "stars": stars, "serverVersion": 299},
            }
        }
    }


def _make_client(
    *,
    thread: dict | None = None,
    teams: dict | None = None,
    send_detail: dict | None = None,
    accept_detail: dict | None = None,
    player_id: str = "12345678",
) -> HWClient:
    client = HWClient(headers={"x-auth-token": "test"})
    client.chat_get_all = MagicMock(
        return_value=_res({"response": thread if thread is not None else {"chat": [], "users": {}, "clans": []}})
    )
    client.team_get_all = MagicMock(
        return_value=_res(
            {
                "response": teams
                if teams is not None
                else {"challenge_titan": [4000, 4001, 4002], "challenge": [11, 22]}
            }
        )
    )
    client.chat_send_challenge = MagicMock(
        return_value=_res({"response": send_detail if send_detail is not None else {"result": True}})
    )
    client.chat_accept_challenge = MagicMock(return_value=_res(accept_detail if accept_detail is not None else _battle_detail()))
    client.user_get_info = MagicMock(
        return_value=_res({"response": {"id": player_id, "name": "Alice"}})
    )
    return client


# --- ApiAction ---


def test_api_action_has_challenge_methods():
    assert ApiAction.CHAT_SEND_CHALLENGE == "chatSendChallenge"
    assert ApiAction.CHAT_ACCEPT_CHALLENGE == "chatAcceptChallenge"


# --- client payloads ---


def test_client_chat_send_challenge_payload_has_no_retry():
    client = HWClient(headers={"x-auth-token": "test"})
    client.call = MagicMock(return_value=_res())
    client.chat_send_challenge(user_id=11111111, heroes=[4000, 4001], challenge_type="titan")
    call = client.call
    payload = call.call_args.args[0]
    assert payload["calls"][0]["name"] == "chatSendChallenge"
    assert payload["calls"][0]["args"] == {
        "chatType": "personal",
        "heroes": [4000, 4001],
        "favor": {},
        "text": "",
        "type": "titan",
        "manual": False,
        "maxUpgrade": True,
        "userId": "11111111",
    }
    assert payload["calls"][0]["context"] == {"actionTs": 0}
    assert call.call_args.kwargs["max_retries"] == 0


def test_client_chat_accept_challenge_payload_has_no_retry():
    client = HWClient(headers={"x-auth-token": "test"})
    client.call = MagicMock(return_value=_res())
    client.chat_accept_challenge(user_id="22222222", message_id=9, heroes=[4000])
    call = client.call
    payload = call.call_args.args[0]
    assert payload["calls"][0]["name"] == "chatAcceptChallenge"
    assert payload["calls"][0]["args"] == {
        "messageId": 9,
        "heroes": [4000],
        "favor": {},
        "maxUpgrade": True,
        "userId": "22222222",
    }
    assert call.call_args.kwargs["max_retries"] == 0


def test_client_team_get_all_and_user_get_info_methods():
    client = HWClient(headers={"x-auth-token": "test"})
    client.call = MagicMock(return_value=_res())
    client.team_get_all()
    assert client.call.call_args.args[0]["calls"][0]["name"] == "teamGetAll"
    client.user_get_info()
    assert client.call.call_args.args[0]["calls"][0]["name"] == "userGetInfo"


# --- validation ---


def test_validate_user_id_accepts_numeric_and_rejects_invalid():
    assert validate_user_id("12345678") == "12345678"
    assert validate_user_id(12345678) == "12345678"
    for bad in ("", "  ", "abc", "0", "-1", None, True):
        with pytest.raises(ChallengeError):
            validate_user_id(bad)


def test_validate_heroes_allows_partial_teams_and_rejects_bad_input():
    assert validate_heroes([4000]) == (4000,)
    assert validate_heroes(["4000", 4001]) == (4000, 4001)
    with pytest.raises(ChallengeError):
        validate_heroes([])
    with pytest.raises(ChallengeError):
        validate_heroes([4000, 4000])
    with pytest.raises(ChallengeError):
        validate_heroes([4000, 4001, 4002, 4003, 4004, 4005])
    with pytest.raises(ChallengeError):
        validate_heroes([0])
    with pytest.raises(ChallengeError):
        validate_heroes([True])


# --- parsing ---


def test_parse_challenge_messages_marks_consumed_via_replay_challenge_id():
    response = {
        "chat": [
            _challenge_message(1, SENDER_ID, 1000),
            _replay_message(2, 1, "12345678", 1001),
            _challenge_message("3", SENDER_ID, 1002),
            _replay_message(4, "3", "12345678", 1003),
            _challenge_message(5, "12345678", 1004),
        ]
    }
    messages = parse_challenge_messages(response)
    assert [m.message_id for m in messages] == [1, 3, 5]
    assert messages[0].pending is False
    assert messages[1].pending is False  # "3" (str) matches replay challengeId 3 (int)
    assert messages[2].pending is True
    assert messages[2].sender_id == "12345678"


def test_parse_challenge_messages_skips_challenge_without_sender():
    response = {
        "chat": [
            _challenge_message(1, "", 1000),
            _challenge_message(2, SENDER_ID, 1001),
        ]
    }
    messages = parse_challenge_messages(response)
    assert [m.message_id for m in messages] == [2]
    assert messages[0].sender_id == SENDER_ID


def test_parse_challenge_messages_float_challenge_id_consumes_matching_message():
    response = {
        "chat": [
            _challenge_message(1, SENDER_ID, 1000),
            _replay_message(2, 1.0, "12345678", 1001),
        ]
    }
    messages = parse_challenge_messages(response)
    assert [m.message_id for m in messages] == [1]
    assert messages[0].pending is False


def test_parse_challenge_messages_tolerates_bad_shapes():
    assert parse_challenge_messages(None) == []
    assert parse_challenge_messages({"chat": "nope"}) == []
    assert parse_challenge_messages({"chat": [None, {"messageType": "text"}]}) == []


def test_pending_challenges_filters_sender_and_pending():
    messages = [
        ChallengeMessage(1, SENDER_ID, "titan", 1, 1000, pending=True),
        ChallengeMessage(2, "12345678", "titan", 1, 1001, pending=True),
        ChallengeMessage(3, SENDER_ID, "hero", 1, 1002, pending=False),
    ]
    assert [m.message_id for m in pending_challenges(messages)] == [1, 2]
    assert [m.message_id for m in pending_challenges(messages, SENDER_ID)] == [1]
    assert [m.message_id for m in sent_pending_challenges(messages, SENDER_ID)] == [2]
    assert [m.message_id for m in sent_pending_challenges(messages, 99999999)] == [1, 2]


def _invoke_main(
    argv: list[str], monkeypatch, *, expect_exit: bool = True
) -> pytest.ExceptionInfo | None:
    """Run main() and restore the root log level it changes globally."""
    from hw_genie.main import main

    previous = logging.getLogger().level
    monkeypatch.setattr(sys, "argv", argv)
    try:
        if not expect_exit:
            main()
            return None
        with pytest.raises(SystemExit) as exc:
            main()
        return exc
    finally:
        logging.getLogger().setLevel(previous)


def test_cli_challenge_battle_targets_are_mutually_exclusive(monkeypatch):
    both = _invoke_main(
        ["hw-genie", "challenge", "battle", "--user-id", "11111111", "--accept-account", "Bob"],
        monkeypatch,
    )
    assert both.value.code == 2


def test_cli_challenge_battle_allows_default_target_without_flags(monkeypatch, capsys):
    # Neither flag is an argparse error now: the command falls back to
    # challenge_defaults.user_id (here the test DB has no accounts, so the
    # account-resolution stage exits 1 instead of the argparse exit 2).
    missing = _invoke_main(["hw-genie", "challenge", "battle"], monkeypatch)
    assert missing.value.code == 1
    assert "No accounts found" in capsys.readouterr().err


def test_cli_challenge_battle_blank_accept_account_is_rejected(monkeypatch, capsys):
    result = _invoke_main(
        ["hw-genie", "challenge", "battle", "-a", "Alice", "--accept-account", "   "],
        monkeypatch,
    )
    assert result.value.code == 2
    assert "must not be blank" in capsys.readouterr().err


def test_cli_challenge_config_sets_defaults(monkeypatch):
    _register_alice()
    _invoke_main(
        [
            "hw-genie",
            "challenge",
            "config",
            "-a",
            "Alice",
            "--user-id",
            PEER_ID,
            "--type",
            "hero",
        ],
        monkeypatch,
        expect_exit=False,
    )
    assert load_challenge_defaults("Alice") == {"user_id": PEER_ID, "type": "hero"}


def test_cli_challenge_config_clear_rejects_other_flags(monkeypatch, capsys):
    result = _invoke_main(
        [
            "hw-genie",
            "challenge",
            "config",
            "-a",
            "Alice",
            "--clear",
            "--user-id",
            PEER_ID,
        ],
        monkeypatch,
    )
    assert result.value.code == 2
    assert "--clear cannot be combined" in capsys.readouterr().err


def test_cli_multi_challenge_requires_yes_before_any_api_call(monkeypatch, capsys):
    result = _invoke_main(["hw-genie", "multi", "challenge", "Alice"], monkeypatch)
    assert result.value.code == 2
    assert "Add --yes" in capsys.readouterr().err


def test_cli_multi_challenge_rejects_invalid_user_id_before_workers(monkeypatch, capsys):
    result = _invoke_main(
        ["hw-genie", "multi", "challenge", "--user-id", "abc", "--yes"], monkeypatch
    )
    assert result.value.code == 2
    assert "Invalid userId" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("challenge_type", "field", "expected"),
    [
        ("titan", "challenge_titan", (4000, 4001, 4002)),
        ("hero", "challenge", (11, 22)),
    ],
)
def test_select_challenge_team_uses_saved_team(challenge_type, field, expected):
    detail = {"response": {"challenge_titan": [4000, 4001, 4002], "challenge": [11, 22]}}
    assert select_challenge_team(detail, challenge_type) == expected


def test_select_challenge_team_errors_when_missing_or_unknown():
    with pytest.raises(ChallengeError, match="no 'challenge' team"):
        select_challenge_team({"response": {"challenge_titan": [4000]}}, "hero")
    with pytest.raises(ChallengeError, match="Unknown challenge type"):
        select_challenge_team({"response": {}}, "beast")
    with pytest.raises(ChallengeError):
        select_challenge_team("nope", "titan")


def test_parse_send_result_truthy_and_rejected():
    parse_send_result({"response": {"result": True}})
    with pytest.raises(ChallengeError, match="reason=notFound"):
        parse_send_result({"response": {"result": False, "reason": "notFound"}})
    with pytest.raises(ChallengeError):
        parse_send_result({"response": "nope"})


def test_parse_accept_result_extracts_outcome_and_replay_url():
    result = parse_accept_result(_battle_detail(win=True, stars=2))
    assert result.win is True
    assert result.stars == 2
    assert result.battle_id == "1794728303314860997"
    assert result.battle_type == "challenge_titan"
    assert result.replay_url == "https://www.hero-wars.com/?replay_id=1794728303314860997"


def test_parse_accept_result_loss_and_malformed():
    result = parse_accept_result(_battle_detail(win=False, stars=0))
    assert result.win is False
    assert result.stars == 0
    with pytest.raises(ChallengeError, match="no battle"):
        parse_accept_result({"response": {}})


def test_parse_accept_result_rejected_includes_reason():
    with pytest.raises(ChallengeError, match="reason=NotAvailable"):
        parse_accept_result({"response": {"result": False, "reason": "NotAvailable"}})


def test_fetch_player_id_reads_user_get_info():
    client = _make_client(player_id="99999999")
    assert fetch_player_id(client) == "99999999"
    client.user_get_info = MagicMock(return_value=_res({"response": {}}))
    with pytest.raises(ChallengeError, match="did not return a player id"):
        fetch_player_id(client)


# --- send ---


def test_run_challenge_send_uses_single_target_unit_and_calls_api():
    client = _make_client()
    result = run_challenge_send(client, "Alice", PEER_ID)
    assert result.peer_id == PEER_ID
    assert result.heroes == (4000,)
    client.chat_send_challenge.assert_called_once_with(
        user_id=PEER_ID, heroes=[4000], challenge_type="titan"
    )


def test_select_challenge_target_team_defaults_to_first_saved_unit():
    detail = {"response": {"challenge_titan": [4000, 4001, 4002], "challenge": [11, 22]}}
    assert select_challenge_target_team(detail, "titan") == (4000,)
    assert select_challenge_target_team(detail, "hero") == (11,)


def test_run_challenge_send_hero_override_and_dry_run():
    client = _make_client()
    result = run_challenge_send(client, "Alice", PEER_ID, "hero", [11], dry_run=True)
    assert result.heroes == (11,)
    client.chat_send_challenge.assert_not_called()
    client.team_get_all.assert_not_called()


def test_run_challenge_send_rejected_or_api_error():
    rejected = _make_client(send_detail={"result": False, "reason": "notFound"})
    with pytest.raises(ChallengeError, match="notFound"):
        run_challenge_send(rejected, "Alice", PEER_ID)

    failed = _make_client()
    failed.chat_send_challenge = MagicMock(
        return_value=_res(status=ResponseStatus.ERROR, error_name="NotAvailable")
    )
    with pytest.raises(ChallengeError, match="NotAvailable"):
        run_challenge_send(failed, "Alice", PEER_ID)


# --- accept ---


def _thread_with_challenges(*messages: dict) -> dict:
    return {"chat": list(messages), "users": {"11111111": {"id": "11111111", "name": "Bob"}}, "clans": []}


def test_run_challenge_accept_picks_newest_pending_and_parses_result():
    client = _make_client(
        thread=_thread_with_challenges(
            _challenge_message(1, PEER_ID, 1000),
            _replay_message(2, 1, "12345678", 1001),
            _challenge_message(3, PEER_ID, 1002),
        )
    )
    result = run_challenge_accept(client, "Alice", PEER_ID)
    assert result is not None and result.win is True
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=3, heroes=[4000, 4001, 4002]
    )


def test_run_challenge_accept_explicit_message_id_uses_message_type_for_team():
    client = _make_client(
        thread=_thread_with_challenges(
            _challenge_message(7, PEER_ID, 1000, challenge_type="hero"),
        )
    )
    result = run_challenge_accept(client, "Alice", PEER_ID, message_id=7)
    assert result is not None
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=7, heroes=[11, 22]
    )


def test_run_challenge_accept_explicit_consumed_message_warns_but_accepts(capsys):
    client = _make_client(
        thread=_thread_with_challenges(
            _challenge_message(1, PEER_ID, 1000),
            _replay_message(2, 1, "12345678", 1001),
        )
    )
    run_challenge_accept(client, "Alice", PEER_ID, message_id=1)
    assert "already exists for this messageId" in capsys.readouterr().out


def test_run_challenge_accept_explicit_sent_challenge_battles_as_sender():
    # userId 12345678 == the client's own player id: outgoing "Sent" challenge.
    client = _make_client(
        thread=_thread_with_challenges(
            _challenge_message(5, "12345678", 1000),
        )
    )
    result = run_challenge_accept(client, "Alice", PEER_ID, message_id=5)
    assert result is not None
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=5, heroes=[4000, 4001, 4002]
    )


def test_run_challenge_accept_sent_flag_picks_sent_pending():
    client = _make_client(
        thread=_thread_with_challenges(
            _challenge_message(1, PEER_ID, 1000),
            _challenge_message(2, "12345678", 1001),
        )
    )
    run_challenge_accept(client, "Alice", PEER_ID, sent=True)
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=2, heroes=[4000, 4001, 4002]
    )


def test_run_challenge_accept_same_ctime_picks_numerically_newest():
    client = _make_client(
        thread=_thread_with_challenges(
            _challenge_message(10, PEER_ID, 1000),
            _challenge_message(9, PEER_ID, 1000),
        )
    )
    result = run_challenge_accept(client, "Alice", PEER_ID)
    assert result is not None
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=10, heroes=[4000, 4001, 4002]
    )


def test_run_challenge_accept_rejected_result_surfaces_reason():
    client = _make_client(
        thread=_thread_with_challenges(_challenge_message(3, PEER_ID, 1002)),
        accept_detail={"response": {"result": False, "reason": "NotAvailable"}},
    )
    with pytest.raises(ChallengeError, match="reason=NotAvailable"):
        run_challenge_accept(client, "Alice", PEER_ID)


def test_run_challenge_accept_no_pending_errors_and_dry_run_skips_call():
    no_pending = _make_client(
        thread=_thread_with_challenges(
            _challenge_message(1, PEER_ID, 1000),
            _replay_message(2, 1, "12345678", 1001),
        )
    )
    with pytest.raises(ChallengeError, match="No pending challenge"):
        run_challenge_accept(no_pending, "Alice", PEER_ID)

    dry = _make_client(thread=_thread_with_challenges(_challenge_message(3, PEER_ID, 1002)))
    assert run_challenge_accept(dry, "Alice", PEER_ID, dry_run=True) is None
    dry.chat_accept_challenge.assert_not_called()


def test_run_challenge_accept_unknown_explicit_message_id_errors():
    client = _make_client(thread=_thread_with_challenges(_challenge_message(3, PEER_ID, 1002)))
    with pytest.raises(ChallengeError, match="was not found"):
        run_challenge_accept(client, "Alice", PEER_ID, message_id=99)


# --- list ---


def test_run_challenge_list_json_and_pending_hint(capsys):
    client = _make_client(
        thread=_thread_with_challenges(
            _challenge_message(1, PEER_ID, 1000),
            _replay_message(2, 1, "12345678", 1001),
            _challenge_message(3, PEER_ID, 1002),
        )
    )
    challenges = run_challenge_list(client, "Alice", PEER_ID, json_output=True)
    output = json.loads(capsys.readouterr().out)
    assert [item["message_id"] for item in output] == [1, 3]
    assert [item["pending"] for item in output] == [False, True]
    assert [c.message_id for c in challenges] == [1, 3]

    run_challenge_list(client, "Alice", PEER_ID)
    assert "Newest pending messageId from 11111111: 3" in capsys.readouterr().out


def test_run_challenge_list_empty(capsys):
    client = _make_client(thread={"chat": []})
    assert run_challenge_list(client, "Alice", PEER_ID) == []
    assert "No challenge messages found" in capsys.readouterr().out


def test_run_challenge_list_clamps_count_to_api_max():
    client = _make_client(thread={"chat": []})
    run_challenge_list(client, "Alice", PEER_ID, count=500)
    assert client.chat_get_all.call_args.kwargs["count"] == 200


# --- battle ---


def test_run_challenge_battle_sends_then_accepts_discovered_message(monkeypatch):
    sender = _make_client(player_id=SENDER_ID)
    accept = _make_client(player_id=PEER_ID)
    empty = _res({"response": {"chat": []}})
    after_send = _res(
        {"response": _thread_with_challenges(_challenge_message(9, SENDER_ID, 2000))}
    )
    accept.chat_get_all = MagicMock(side_effect=[empty, after_send, after_send])

    result = run_challenge_battle(
        sender,
        "Alice",
        accept,
        "Bob",
        wait_seconds=1,
        poll_interval=0.1,
        sleep_fn=lambda _: None,
    )
    assert result is not None and result.win is True
    sender.chat_send_challenge.assert_called_once_with(
        user_id=PEER_ID, heroes=[4000], challenge_type="titan"
    )
    accept.chat_accept_challenge.assert_called_once_with(
        user_id=SENDER_ID, message_id=9, heroes=[4000, 4001, 4002]
    )


def test_run_challenge_battle_dry_run_does_not_send_or_accept():
    sender = _make_client(player_id=SENDER_ID)
    accept = _make_client(player_id=PEER_ID)
    assert (
        run_challenge_battle(sender, "Alice", accept, "Bob", dry_run=True)
        is None
    )
    sender.chat_send_challenge.assert_not_called()
    accept.chat_accept_challenge.assert_not_called()


def test_run_challenge_battle_rejects_same_account():
    client = _make_client()
    with pytest.raises(ChallengeError, match="must differ"):
        run_challenge_battle(client, "Alice", client, "alice")


def test_run_challenge_battle_times_out_when_challenge_never_appears():
    sender = _make_client(player_id=SENDER_ID)
    accept = _make_client(player_id=PEER_ID)
    accept.chat_get_all = MagicMock(return_value=_res({"response": {"chat": []}}))
    with pytest.raises(ChallengeError, match="no new pending challenge"):
        run_challenge_battle(
            sender,
            "Alice",
            accept,
            "Bob",
            wait_seconds=0,
            poll_interval=0.1,
            sleep_fn=lambda _: None,
        )


def test_run_challenge_battle_ignores_challenges_that_existed_before_send():
    sender = _make_client(player_id=SENDER_ID)
    accept = _make_client(player_id=PEER_ID)
    baseline = _res({"response": _thread_with_challenges(_challenge_message(1, SENDER_ID, 1000))})
    still_baseline = _res({"response": _thread_with_challenges(_challenge_message(1, SENDER_ID, 1000))})
    accept.chat_get_all = MagicMock(side_effect=[baseline, still_baseline])
    with pytest.raises(ChallengeError, match="no new pending challenge"):
        run_challenge_battle(
            sender,
            "Alice",
            accept,
            "Bob",
            wait_seconds=0,
            poll_interval=0.1,
            sleep_fn=lambda _: None,
        )


# --- sender-side battle (doc-style) ---


def test_run_challenge_accept_sent_flag_errors_when_only_received_pending():
    client = _make_client(
        thread=_thread_with_challenges(_challenge_message(1, PEER_ID, 1000))
    )
    with pytest.raises(ChallengeError, match="No pending challenge sent to"):
        run_challenge_accept(client, "Alice", PEER_ID, sent=True)


def test_run_challenge_accept_sent_with_explicit_received_id_errors():
    client = _make_client(
        thread=_thread_with_challenges(_challenge_message(1, PEER_ID, 1000))
    )
    with pytest.raises(ChallengeError, match="was received from"):
        run_challenge_accept(client, "Alice", PEER_ID, message_id=1, sent=True)
    client.chat_accept_challenge.assert_not_called()


def test_run_challenge_list_hints_sent_pending(capsys):
    client = _make_client(
        thread=_thread_with_challenges(_challenge_message(5, "12345678", 1000))
    )
    run_challenge_list(client, "Alice", PEER_ID)
    out = capsys.readouterr().out
    assert "Newest pending challenge you sent: 5" in out
    assert "challenge accept --sent" in out


def test_run_challenge_self_battle_sends_one_unit_and_battles_on_same_account():
    client = _make_client(player_id="12345678")
    empty = _res({"response": {"chat": []}})
    after_send = _res(
        {"response": _thread_with_challenges(_challenge_message(9, "12345678", 2000))}
    )
    client.chat_get_all = MagicMock(side_effect=[empty, after_send, after_send])

    result = run_challenge_self_battle(
        client,
        "Alice",
        PEER_ID,
        wait_seconds=1,
        poll_interval=0.1,
        sleep_fn=lambda _: None,
    )
    assert result is not None and result.win is True
    client.chat_send_challenge.assert_called_once_with(
        user_id=PEER_ID, heroes=[4000], challenge_type="titan"
    )
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=9, heroes=[4000, 4001, 4002]
    )


def test_run_challenge_self_battle_dry_run_does_not_write():
    client = _make_client()
    assert (
        run_challenge_self_battle(client, "Alice", PEER_ID, dry_run=True)
        is None
    )
    client.chat_send_challenge.assert_not_called()
    client.chat_accept_challenge.assert_not_called()
    client.team_get_all.assert_called_once()


def test_run_challenge_self_battle_prefers_sent_challenge_over_received():
    client = _make_client(player_id="12345678")
    empty = _res({"response": {"chat": []}})
    after_send = _res(
        {
            "response": _thread_with_challenges(
                _challenge_message(1, PEER_ID, 2000),
                _challenge_message(2, "12345678", 2001),
            )
        }
    )
    client.chat_get_all = MagicMock(side_effect=[empty, after_send, after_send])

    result = run_challenge_self_battle(
        client,
        "Alice",
        PEER_ID,
        wait_seconds=1,
        poll_interval=0.1,
        sleep_fn=lambda _: None,
    )
    assert result is not None
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=2, heroes=[4000, 4001, 4002]
    )


def test_run_challenge_self_battle_ignores_new_received_challenge_and_times_out():
    client = _make_client(player_id="12345678")
    empty = _res({"response": {"chat": []}})
    received = _res(
        {"response": _thread_with_challenges(_challenge_message(5, PEER_ID, 2000))}
    )
    client.chat_get_all = MagicMock(side_effect=[empty, received])
    with pytest.raises(ChallengeError, match="no new pending challenge appeared"):
        run_challenge_self_battle(
            client,
            "Alice",
            PEER_ID,
            wait_seconds=0,
            poll_interval=0.1,
            sleep_fn=lambda _: None,
        )
    client.chat_accept_challenge.assert_not_called()


def test_run_challenge_self_battle_team_overrides_are_directional():
    client = _make_client(player_id="12345678")
    empty = _res({"response": {"chat": []}})
    after_send = _res(
        {"response": _thread_with_challenges(_challenge_message(9, "12345678", 2000))}
    )
    client.chat_get_all = MagicMock(side_effect=[empty, after_send, after_send])

    result = run_challenge_self_battle(
        client,
        "Alice",
        PEER_ID,
        heroes=[4013],
        accept_heroes=[4000, 4001],
        wait_seconds=1,
        poll_interval=0.1,
        sleep_fn=lambda _: None,
    )
    assert result is not None
    client.chat_send_challenge.assert_called_once_with(
        user_id=PEER_ID, heroes=[4013], challenge_type="titan"
    )
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=9, heroes=[4000, 4001]
    )
    client.team_get_all.assert_not_called()


def test_run_challenge_self_battle_ignores_baseline_and_times_out():
    client = _make_client(player_id="12345678")
    same = _res(
        {"response": _thread_with_challenges(_challenge_message(1, "12345678", 1000))}
    )
    client.chat_get_all = MagicMock(side_effect=[same, same])
    with pytest.raises(ChallengeError, match="no new pending challenge appeared"):
        run_challenge_self_battle(
            client,
            "Alice",
            PEER_ID,
            wait_seconds=0,
            poll_interval=0.1,
            sleep_fn=lambda _: None,
        )


def test_run_challenge_self_battle_validates_accept_team_before_send():
    client = _make_client()
    with pytest.raises(ChallengeError, match="Invalid unit id"):
        run_challenge_self_battle(client, "Alice", PEER_ID, accept_heroes=[0])
    client.chat_send_challenge.assert_not_called()


def test_run_challenge_battle_validates_accept_team_before_send():
    sender = _make_client(player_id=SENDER_ID)
    accept = _make_client(player_id=PEER_ID)
    with pytest.raises(ChallengeError, match="Invalid unit id"):
        run_challenge_battle(sender, "Alice", accept, "Bob", accept_heroes=[0])
    sender.chat_send_challenge.assert_not_called()


# --- per-account defaults / multi ---


def _register_alice() -> None:
    SessionManager.save(
        "Alice",
        {"headers": {"x-auth-token": "test"}, "player": {"id": "a1", "name": "Alice"}},
    )


def test_challenge_defaults_roundtrip_and_clear():
    _register_alice()
    defaults = set_challenge_defaults("Alice", user_id=PEER_ID, challenge_type="hero")
    assert defaults == {"user_id": PEER_ID, "type": "hero"}
    assert load_challenge_defaults("Alice") == defaults
    assert SessionManager.load("Alice")[CHALLENGE_DEFAULTS_KEY] == defaults
    assert resolve_challenge_target("Alice") == (PEER_ID, "hero")

    clear_challenge_defaults("Alice")
    assert load_challenge_defaults("Alice") == {}
    with pytest.raises(ChallengeError, match="No challenge target"):
        resolve_challenge_target("Alice")


def test_challenge_defaults_validation_and_cli_precedence():
    _register_alice()
    set_challenge_defaults("Alice", user_id=PEER_ID, challenge_type="hero")
    assert resolve_challenge_target("Alice", "22222222", "titan") == ("22222222", "titan")
    assert resolve_challenge_peer("Alice") == PEER_ID
    with pytest.raises(ChallengeError, match="Invalid userId"):
        resolve_challenge_peer("Alice", "")
    with pytest.raises(ChallengeError, match="Unknown challenge type"):
        set_challenge_defaults("Alice", challenge_type="beast")
    with pytest.raises(ChallengeError, match="Unknown challenge type"):
        resolve_challenge_target("Alice", challenge_type="beast")
    with pytest.raises(ChallengeError, match="Invalid userId"):
        set_challenge_defaults("Alice", user_id="abc")


def test_run_challenge_send_uses_stored_defaults():
    _register_alice()
    set_challenge_defaults("Alice", user_id=PEER_ID, challenge_type="hero")
    client = _make_client()
    result = run_challenge_send(client, "Alice")
    assert result.peer_id == PEER_ID
    assert result.challenge_type == "hero"
    assert result.heroes == (11,)
    client.chat_send_challenge.assert_called_once_with(
        user_id=PEER_ID, heroes=[11], challenge_type="hero"
    )


def test_run_challenge_send_without_target_errors_before_api():
    _register_alice()
    client = _make_client()
    with pytest.raises(ChallengeError, match="No challenge target"):
        run_challenge_send(client, "Alice")
    client.chat_send_challenge.assert_not_called()


def test_run_challenge_self_battle_uses_stored_defaults():
    _register_alice()
    set_challenge_defaults("Alice", user_id=PEER_ID, challenge_type="titan")
    client = _make_client(player_id="a1")
    empty = _res({"response": {"chat": []}})
    after_send = _res(
        {"response": _thread_with_challenges(_challenge_message(9, "a1", 2000))}
    )
    client.chat_get_all = MagicMock(side_effect=[empty, after_send, after_send])

    result = run_challenge_self_battle(
        client,
        "Alice",
        wait_seconds=1,
        poll_interval=0.1,
        sleep_fn=lambda _: None,
    )
    assert result is not None
    client.chat_send_challenge.assert_called_once_with(
        user_id=PEER_ID, heroes=[4000], challenge_type="titan"
    )
    client.chat_accept_challenge.assert_called_once_with(
        user_id=PEER_ID, message_id=9, heroes=[4000, 4001, 4002]
    )


def test_challenge_routine_uses_account_defaults():
    from hw_genie.runner import challenge_routine

    _register_alice()
    set_challenge_defaults("Alice", user_id=PEER_ID, challenge_type="titan")
    client = _make_client(player_id="a1")
    empty = _res({"response": {"chat": []}})
    after_send = _res(
        {"response": _thread_with_challenges(_challenge_message(9, "a1", 2000))}
    )
    client.chat_get_all = MagicMock(side_effect=[empty, after_send, after_send])

    result = challenge_routine(wait_seconds=1)(client, "Alice")
    assert isinstance(result, ChallengeRunResult)
    assert result.peer_id == PEER_ID
    assert result.dry_run is False
    assert result.battle is not None and result.battle.win is True


def test_challenge_routine_shared_override_wins_over_defaults():
    from hw_genie.runner import challenge_routine

    _register_alice()
    set_challenge_defaults("Alice", user_id=PEER_ID, challenge_type="titan")
    client = _make_client(player_id="a1")
    empty = _res({"response": {"chat": []}})
    after_send = _res(
        {"response": _thread_with_challenges(_challenge_message(9, "a1", 2000))}
    )
    client.chat_get_all = MagicMock(side_effect=[empty, after_send, after_send])

    result = challenge_routine(user_id="22222222", challenge_type="hero", wait_seconds=1)(
        client, "Alice"
    )
    assert result.peer_id == "22222222"
    client.chat_send_challenge.assert_called_once_with(
        user_id="22222222", heroes=[11], challenge_type="hero"
    )


def test_summarize_challenge_counts_failures_and_shows_results(capsys):
    from hw_genie.runner import summarize_challenge

    good = ChallengeRunResult(
        peer_id=PEER_ID,
        challenge_type="titan",
        dry_run=False,
        battle=ChallengeBattleResult(True, 3, "battle-id", "challenge_titan", []),
    )
    planned = ChallengeRunResult(PEER_ID, "titan", True, None)
    failed = summarize_challenge(
        [
            ("Alice", (good, None)),
            ("Bob", (planned, None)),
            ("Carol", (None, ChallengeError("boom"))),
        ],
        dry_run=False,
    )
    assert failed == 1
    out = capsys.readouterr().out
    assert "Multi challenge summary" in out
    assert "WIN" in out and "planned" in out and "boom" in out
