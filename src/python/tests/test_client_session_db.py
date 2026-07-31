from hw_genie.core.client import load_session_headers
from hw_genie.core.session_manager import SessionManager


def test_load_session_headers_from_db_default():
    """DBの 'default' アカウントからヘッダーを読み込めることを検証"""
    account = "default"
    headers = {"x-auth-token": "your-default-token"}
    data = {"headers": headers, "player": {"id": "default_id", "name": "Default"}}

    SessionManager.save(account, data)

    assert load_session_headers()["x-auth-token"] == "your-default-token"
    assert load_session_headers("default")["x-auth-token"] == "your-default-token"


def test_load_session_headers_from_db_account():
    """DBの特定アカウントからヘッダーを読み込めることを検証"""
    account = "Alice"
    headers = {"x-auth-token": "your-alice-token"}
    data = {"headers": headers, "player": {"id": "alice_id", "name": "Alice"}}

    SessionManager.save(account, data)

    assert load_session_headers("Alice")["x-auth-token"] == "your-alice-token"


def test_load_session_headers_nonexistent():
    """DBにデータがない場合、None が返ることを検証"""
    # 存在しないアカウントを指定
    assert load_session_headers("nonexistent_user_12345") is None
