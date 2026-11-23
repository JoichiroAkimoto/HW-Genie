"""Tests for the Hero Wars request signature (core/signing.py).

The formula was validated against a live client request (exact request
returned 200 JSON). The vector below uses synthetic credentials so no real
session token is committed.
"""

import json

from hw_genie.core.signing import compute_signature, serialize_body, sign_headers

TOKEN = "ps-FAKE-TOKEN-FOR-TESTS-0"
SESSION_ID = "testsession0001"
BODY = (
    '{"calls":[{"name":"clanInvites_getUserInbox","args":{},"context":{"actionTs":11698},'
    '"ident":"group_0_body"},{"name":"socialQuestPost","args":{"value":false},'
    '"context":{"actionTs":11698},"ident":"group_1_body"},'
    '{"name":"socialQuestGroupJoin","args":{"value":false},"context":{"actionTs":11698},'
    '"ident":"group_1_socialQuestGroupJoin"},{"name":"socialQuestPost","args":{"value":false},'
    '"context":{"actionTs":11698},"ident":"group_2_body"},'
    '{"name":"socialQuestGroupJoin","args":{"value":false},"context":{"actionTs":11698},'
    '"ident":"group_2_socialQuestGroupJoin"},{"name":"saleShowcase_rewardInfo",'
    '"args":{"offerId":3941001748},"context":{"actionTs":11711},"ident":"group_3_body"},'
    '{"name":"clanRaidSubscription_getInfo","args":{},"context":{"actionTs":11716},'
    '"ident":"group_4_body"},{"name":"billingGetAll","args":{"currency":"JPY"},'
    '"context":{"actionTs":11731},"ident":"group_5_body"},'
    '{"name":"invasion_getInfo","args":{},"context":{"actionTs":11854},'
    '"ident":"group_6_body"}]}'
)
EXPECTED = "2fa7b5a8d851190a712d1cd36ec5ac5e"


def _headers(**overrides):
    headers = {
        "x-request-id": "3",
        "x-auth-token": TOKEN,
        "x-auth-session-id": SESSION_ID,
    }
    headers.update(overrides)
    return headers


def test_known_vector_without_env_headers():
    """Env part is empty when no X-Env* header is present (still accepted live)."""
    assert compute_signature(_headers(), BODY) == "e02a6eb816ee0f12d2ff3d2d6759385b"


def test_known_vector_with_env_headers():
    """X-Env* headers are appended as ``<NAME>=<value>`` sorted by NAME."""
    headers = _headers(
        **{
            "X-Env-Library-Version": "1",
            "X-Env-Unique-Session-Id": "1",
            "X-Env-Unique-Session-Uuid": "test-uuid",
        }
    )
    assert compute_signature(headers, BODY) == EXPECTED


def test_signature_depends_on_request_id_and_body():
    base = compute_signature(_headers(), BODY)
    assert compute_signature(_headers(**{"x-request-id": "4"}), BODY) != base
    assert compute_signature(_headers(), BODY.replace("11698", "11699", 1)) != base


def test_header_names_are_case_insensitive():
    headers = {
        "X-Request-Id": "3",
        "X-Auth-Token": TOKEN,
        "X-Auth-Session-Id": SESSION_ID,
    }
    assert compute_signature(headers, BODY) == "e02a6eb816ee0f12d2ff3d2d6759385b"


def test_sign_headers_sets_lowercase_header():
    headers = _headers()
    sign_headers(headers, BODY)
    assert headers["x-auth-signature"] == "e02a6eb816ee0f12d2ff3d2d6759385b"


def test_serialize_body_is_compact_and_keeps_unicode():
    payload = {"calls": [{"name": "chatSend", "args": {"text": "こんにちは"}}]}
    body = serialize_body(payload)
    assert body == json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    assert "こんにちは" in body


def test_hwclient_signs_the_body_it_posts():
    from unittest.mock import MagicMock

    from hw_genie.core.client import HWClient

    session = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "results": [{"ident": "x", "result": {"response": {"ok": True}}}]
    }
    session.post.return_value = response
    client = HWClient(
        {"x-auth-token": TOKEN, "x-auth-session-id": SESSION_ID, "x-request-id": "0"},
        session=session,
    )

    client.call({"calls": [{"name": "test", "args": {}, "ident": "x"}]})

    kwargs = session.post.call_args.kwargs
    body = kwargs["data"].decode("utf-8")
    headers = kwargs["headers"]
    assert headers["x-request-id"] == "1"
    assert headers["x-auth-signature"] == compute_signature(headers, body)


def test_get_user_info_signs_the_body_it_posts():
    from unittest.mock import MagicMock, patch

    from hw_genie.core.auth import get_user_info

    response = MagicMock(status_code=200)
    response.json.return_value = {"results": []}
    with patch("hw_genie.core.auth.requests.post", return_value=response) as post:
        get_user_info({"x-auth-token": TOKEN, "x-auth-session-id": SESSION_ID})

    kwargs = post.call_args.kwargs
    body = kwargs["data"].decode("utf-8")
    headers = kwargs["headers"]
    assert headers["x-auth-signature"] == compute_signature(headers, body)
