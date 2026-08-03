import pytest
from hw_genie.core.client import load_session_headers
from hw_genie.core.session_manager import SessionManager

def test_load_session_headers_no_arg_single_account():
    """アカウント指定なしで、DBに単一アカウントのみの場合は自動選択されること"""
    data = {"headers": {"x-auth-token": "your-db-token"}, "player": {"id": "default_id", "name": "Default"}}
    SessionManager.repo.save_data("Default", data)

    headers = load_session_headers()
    assert headers["x-auth-token"] == "your-db-token"

def test_load_session_headers_no_arg_multiple_accounts_raises():
    """アカウント指定なしで複数アカウントが登録されている場合はエラーになること"""
    from hw_genie.core.client import AccountAmbiguityError

    SessionManager.repo.save_data("Alice", {"headers": {"x-auth-token": "alice-token"}, "player": {"id": "a1", "name": "Alice"}})
    SessionManager.repo.save_data("Bob", {"headers": {"x-auth-token": "bob-token"}, "player": {"id": "b1", "name": "Bob"}})

    with pytest.raises(AccountAmbiguityError):
        load_session_headers()

def test_load_session_headers_unknown_account_returns_none():
    """指定したアカウントが DB にない場合は None を返すこと"""
    headers = load_session_headers("Unknown")
    assert headers is None

def test_load_session_headers_from_db():
    """DBにデータがある場合、読み込めること"""
    data = {"headers": {"x-auth-token": "your-db-token"}, "player": {"id": "dbuser_id", "name": "dbuser"}}
    SessionManager.repo.save_data("dbuser", data)

    headers = load_session_headers("dbuser")
    assert headers["x-auth-token"] == "your-db-token"

def test_list_accounts_registration_order():
    """list_accounts は登録順（id 順）を返し、アルファベット順にソートしない。"""
    for alias in ("zulu", "alpha", "mike"):
        SessionManager.repo.save_data(alias, {"headers": {"x-auth-token": "t"}, "player": {"id": f"{alias}_id", "name": alias}})
    assert SessionManager.list_accounts() == ["zulu", "alpha", "mike"]


def test_resolve_account_no_accounts_raises_not_found():
    """DB空の場合は AccountNotFoundError になる。"""
    from hw_genie.core.client import resolve_account, AccountNotFoundError

    with pytest.raises(AccountNotFoundError):
        resolve_account(None)
