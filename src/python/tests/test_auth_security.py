import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.exc import SQLAlchemyError
from hw_genie.core.client import HWClient, HWAuthError
from hw_genie.core.session_manager import SessionManager
from hw_genie.core.auth import extract_headers_from_curl


def test_db_connection_error_handling():
    """DB接続エラーが発生した場合の挙動を確認する"""
    with patch("hw_genie.core.repository.get_session_local") as mock_get_session_local:
        # SessionLocal() が呼ばれたときに例外を投げるように設定
        mock_get_session_local.return_value.return_value.__enter__.side_effect = SQLAlchemyError("DB connection failed")

        # SessionManager.load が例外を適切に伝播するか、あるいはハンドリングするかを確認
        # 現在の実装ではハンドリングしていないため、SQLAlchemyError が発生することを期待
        with pytest.raises(SQLAlchemyError):
            SessionManager.load("default")


def test_db_corruption_handling():
    """DBデータが破損している（想定外の型が返ってくる）場合の挙動を確認する"""
    with patch("hw_genie.core.repository.SessionRepository.get_data") as mock_get_data:
        # 辞書を期待しているところで None や文字列が返ってきた場合
        mock_get_data.return_value = "not a dict"

        # SessionManager.load("default").get("headers") で AttributeError が発生することを期待
        with pytest.raises(AttributeError):
            SessionManager.load("default").get("headers")


def test_malformed_headers_injection():
    """不正な形式のヘッダーが注入された場合の HWClient の挙動を確認する"""
    # 極端に大きな値や、特殊文字を含むヘッダー
    malformed_headers = {"x-auth-token": "A" * 10000, "x-auth-id": "'; DROP TABLE sessions; --", "invalid-key": "value"}

    client = HWClient(malformed_headers)

    with patch("requests.Session.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        with pytest.raises(HWAuthError):
            client.call({"calls": []})


def test_empty_headers_handling():
    """ヘッダーが空の場合の挙動を確認する"""
    client = HWClient({})

    with patch("requests.Session.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        with pytest.raises(HWAuthError):
            client.call({"calls": []})


def test_extract_headers_from_curl_robustness():
    """curlコマンドからのヘッダー抽出の堅牢性をテストする"""
    # 1. 空の入力
    assert extract_headers_from_curl("") == {}

    # 2. x-auth- を含まないcurl
    assert extract_headers_from_curl("curl 'https://google.com'") == {}

    # 3. 不正な形式の-H
    assert extract_headers_from_curl("curl -H 'invalidheader'") == {}

    # 4. 複数のx-auth-ヘッダー
    curl = "curl -H 'x-auth-token: token1' -H 'x-auth-id: id1'"
    expected = {"x-auth-token": "token1", "x-auth-id": "id1"}
    assert extract_headers_from_curl(curl) == expected

    # 5. クォートが混在しているケース
    curl_mixed = "curl -H \"x-auth-token: token2\" -H 'x-auth-id: id2'"
    expected_mixed = {"x-auth-token": "token2", "x-auth-id": "id2"}
    assert extract_headers_from_curl(curl_mixed) == expected_mixed


def test_extract_headers_from_curl_edge_cases():
    """curl抽出のエッジケースをテストする"""
    # 値が空のケース
    curl_empty_val = "curl -H 'x-auth-token:'"
    # 現実装では key: value に分割するため、split(":", 1) で value は "" になる
    assert extract_headers_from_curl(curl_empty_val) == {"x-auth-token": ""}

    # 大文字小文字の混在
    curl_case = "curl -H 'X-Auth-Token: TokenCase'"
    assert extract_headers_from_curl(curl_case) == {"x-auth-token": "TokenCase"}
