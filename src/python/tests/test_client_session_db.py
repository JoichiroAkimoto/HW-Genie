from hw_genie.core.client import load_session_headers
from hw_genie.core.session_manager import SessionManager


def test_load_session_headers_from_db_default():
    """DBの 'default' アカウントからヘッダーを読み込めることを検証"""
    account = "default"
    headers = {"x-auth-token": "default-db-token"}
    data = {"headers": headers}

    SessionManager.save(account, data)

    assert load_session_headers()["x-auth-token"] == "default-db-token"
    assert load_session_headers("default")["x-auth-token"] == "default-db-token"


def test_load_session_headers_from_db_account():
    """DBの特定アカウントからヘッダーを読み込めることを検証"""
    account = "Joe"
    headers = {"x-auth-token": "joe-db-token"}
    data = {"headers": headers}

    SessionManager.save(account, data)

    assert load_session_headers("Joe")["x-auth-token"] == "joe-db-token"


def test_load_session_headers_nonexistent():
    """DBにデータがない場合、None が返ることを検証"""
    # 存在しないアカウントを指定
    assert load_session_headers("nonexistent_user_12345") is None
