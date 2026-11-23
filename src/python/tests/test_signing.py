"""Hero Wars リクエスト署名（core/signing.py）のテスト。

署名式は live のクライアントリクエスト（完全一致で 200 JSON が返ったもの）で
検証した。下のベクタは合成の認証情報を使うため、実セッショントークンは含まない。
"""

import pytest
import requests
from requests.exceptions import HTTPError

from hw_genie.core.signing import (
    compute_signature,
    ensure_json_content_type,
    serialize_body,
    set_header,
    sign_headers,
)

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
#: X-Env* を含まない場合の既知ベクタ（env 部分は空）。
BASELINE = "e02a6eb816ee0f12d2ff3d2d6759385b"


def _headers(**overrides):
    headers = {
        "x-request-id": "3",
        "x-auth-token": TOKEN,
        "x-auth-session-id": SESSION_ID,
    }
    headers.update(overrides)
    return headers


def test_known_vector_without_env_headers():
    """X-Env* ヘッダーが無いとき env 部分は空（live でも受理される）。"""
    assert compute_signature(_headers(), BODY) == BASELINE


def test_known_vector_with_env_headers():
    """X-Env* は ``<NAME>=<value>`` を NAME 昇順で連結して末尾に付く。"""
    headers = _headers(
        **{
            "X-Env-Library-Version": "1",
            "X-Env-Unique-Session-Id": "1",
            "X-Env-Unique-Session-Uuid": "test-uuid",
        }
    )
    assert compute_signature(headers, BODY) == EXPECTED


def test_signature_depends_on_request_id_and_body():
    """request-id と body のどちらが変わっても署名は変わる。"""
    base = compute_signature(_headers(), BODY)
    assert compute_signature(_headers(**{"x-request-id": "4"}), BODY) != base
    assert compute_signature(_headers(), BODY.replace("11698", "11699", 1)) != base


def test_header_names_are_case_insensitive():
    """ヘッダー名の大小文字が違っても同じ署名になる。"""
    headers = {
        "X-Request-Id": "3",
        "X-Auth-Token": TOKEN,
        "X-Auth-Session-Id": SESSION_ID,
    }
    assert compute_signature(headers, BODY) == BASELINE


def test_header_lookup_uses_last_match_like_requests():
    """重複キーがある場合は requests のマージ（後勝ち）と同じ最後の値を署名対象にする。"""
    headers = _headers()
    headers["X-Request-Id"] = "9"
    assert compute_signature(headers, BODY) == compute_signature(
        _headers(**{"x-request-id": "9"}), BODY
    )


def test_set_header_removes_case_variants_and_signs_the_sent_value():
    """保存ヘッダー相当の大小混在 dict でも、送信値と同じ request-id で署名される。"""
    # 保存済みヘッダー相当（requests は後勝ちで上書きするため、そのままだと送信値は "6"）。
    headers = {
        "X-Request-Id": "5",
        "x-auth-token": TOKEN,
        "x-auth-session-id": SESSION_ID,
    }
    set_header(headers, "x-request-id", "6")

    # 大小違いの重複が残らないこと。
    assert [key for key in headers if key.lower() == "x-request-id"] == ["x-request-id"]

    # 署名対象は実際に送られる値（6）を反映していること。
    expect = compute_signature(
        {"x-request-id": "6", "x-auth-token": TOKEN, "x-auth-session-id": SESSION_ID}, BODY
    )
    assert compute_signature(headers, BODY) == expect

    # requests が実際に送るヘッダーでも同じ値になっていること。
    prepared = requests.Request("POST", "https://example.com/", headers=headers).prepare()
    assert prepared.headers["x-request-id"] == "6"
    assert compute_signature(
        {
            "x-request-id": prepared.headers["x-request-id"],
            "x-auth-token": prepared.headers["x-auth-token"],
            "x-auth-session-id": prepared.headers["x-auth-session-id"],
        },
        BODY,
    ) == expect


def test_ensure_json_content_type_is_case_insensitive():
    """Content-Type が大小文字違いで既にあれば追加しない。"""
    headers = {"content-type": "text/plain"}
    ensure_json_content_type(headers)
    assert headers == {"content-type": "text/plain"}

    headers = {}
    ensure_json_content_type(headers)
    assert headers["Content-Type"] == "application/json; charset=UTF-8"


def test_sign_headers_sets_lowercase_header():
    """sign_headers は小文字の x-auth-signature を設定する。"""
    headers = _headers()
    sign_headers(headers, BODY)
    assert headers["x-auth-signature"] == BASELINE


def test_serialize_body_is_compact_and_keeps_unicode():
    """compact な JSON（空白なし・非 ASCII をそのまま）で直列化される。"""
    payload = {"calls": [{"name": "chatSend", "args": {"text": "こんにちは"}}]}
    # 実装と同じ式で再計算せず、literal で固定する（自己成就的なテストを避ける）。
    assert (
        serialize_body(payload)
        == '{"calls":[{"name":"chatSend","args":{"text":"こんにちは"}}]}'
    )


def test_serialize_body_rejects_nan():
    """NaN / Infinity は厳密な JSON ではないため拒否する。"""
    with pytest.raises(ValueError):
        serialize_body({"value": float("nan")})


def test_hwclient_signs_the_body_it_posts():
    """HWClient は送信する body とヘッダーから計算した署名を付ける。"""
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


def test_hwclient_retry_resigns_with_new_request_id_and_body():
    """HTTP 5xx のリトライでは request-id と body（actionTs）を作り直して再署名する。"""
    from unittest.mock import MagicMock, patch

    from hw_genie.core.client import HWClient

    session = MagicMock()
    failed = MagicMock(status_code=500)
    failed.text = "boom"
    failed.raise_for_status.side_effect = HTTPError(response=failed)
    ok = MagicMock(status_code=200)
    ok.raise_for_status.return_value = None
    ok.json.return_value = {"results": [{"ident": "x", "result": {"response": {}}}]}
    session.post.side_effect = [failed, ok]

    client = HWClient(
        {"x-auth-token": TOKEN, "x-auth-session-id": SESSION_ID, "x-request-id": "0"},
        session=session,
    )

    payload = {"calls": [{"name": "test", "args": {}, "context": {"actionTs": 0}, "ident": "x"}]}
    with patch("hw_genie.core.client.time.sleep"), patch(
        "hw_genie.core.client.time.time", side_effect=[1000, 2000]
    ):
        res = client.call(payload)

    assert res.is_success
    assert session.post.call_count == 2
    first = session.post.call_args_list[0].kwargs
    second = session.post.call_args_list[1].kwargs

    # actionTs が更新されるので body は変わる。
    assert first["data"] != second["data"]
    # request-id はリトライ毎に増える。
    assert first["headers"]["x-request-id"] == "1"
    assert second["headers"]["x-request-id"] == "2"
    # どちらも送信 body に対して正しく署名されている。
    for call in (first, second):
        body = call["data"].decode("utf-8")
        assert call["headers"]["x-auth-signature"] == compute_signature(
            call["headers"], body
        )


def test_hwclient_classifies_invalid_signature_plain_text_as_auth_error():
    """200 + text/plain の "Invalid signature" は認証エラーとして扱う。"""
    from unittest.mock import MagicMock

    from hw_genie.core.client import HWAuthError, HWClient

    session = MagicMock()
    response = MagicMock(status_code=200)
    response.raise_for_status.return_value = None
    response.text = "Invalid signature"
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response

    client = HWClient(
        {"x-auth-token": TOKEN, "x-auth-session-id": SESSION_ID, "x-request-id": "0"},
        session=session,
    )

    with pytest.raises(HWAuthError):
        client.call({"calls": [{"name": "test", "args": {}, "ident": "x"}]})


def test_get_user_info_signs_the_body_it_posts():
    """get_user_info も送信する body とヘッダーから計算した署名を付ける。"""
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


def test_get_user_info_keeps_input_headers_and_drops_signature_from_result():
    """入力 dict を変更せず、成功時の戻り値 headers に署名を含めない。"""
    from unittest.mock import MagicMock, patch

    from hw_genie.core.auth import get_user_info

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "results": [
            {
                "ident": "body",
                "result": {"response": {"id": "1", "name": "Alice", "level": 10}},
            },
            {"ident": "arena", "result": {"response": {}}},
        ]
    }
    headers = {"x-auth-token": TOKEN, "x-auth-session-id": SESSION_ID}
    with patch("hw_genie.core.auth.requests.post", return_value=response):
        info = get_user_info(headers)

    assert info["status"] == "success"
    # 呼び出し元の dict は変更されない（request-id 追加・署名付与なし）。
    assert headers == {"x-auth-token": TOKEN, "x-auth-session-id": SESSION_ID}
    # 戻り値の headers は再計算前提のため署名を含めない。
    assert "x-auth-signature" not in {key.lower() for key in info["headers"]}
    assert info["headers"]["x-request-id"] == "101"
