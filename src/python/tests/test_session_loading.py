import pytest
import json
from hw_genie.core.client import load_session_headers
from hw_genie.core.session_manager import SessionManager

@pytest.fixture
def temp_session_files(tmp_path, monkeypatch):
    """
    テスト用のセッションファイル環境を構築する。
    SessionManager.load が migration ロジックで使う get_session_path を tmp_path に向ける。
    """
    monkeypatch.setattr("hw_genie.core.auth.get_session_path", lambda acc: str(tmp_path / ("session.json" if acc == "default" else f"session.{acc}.json")))
    return tmp_path

def test_load_session_headers_no_arg_single_account(temp_session_files):
    """アカウント指定なしで、DBに単一アカウントのみの場合は自動選択されること"""
    data = {"headers": {"x-auth-token": "your-db-token"}, "player": {"id": "default_id", "name": "Default"}}
    SessionManager.repo.save_data("Default", data)

    headers = load_session_headers()
    assert headers["x-auth-token"] == "your-db-token"

def test_load_session_headers_no_arg_multiple_accounts_raises(temp_session_files):
    """アカウント指定なしで複数アカウントが登録されている場合はエラーになること"""
    from hw_genie.core.client import AccountAmbiguityError

    SessionManager.repo.save_data("Alice", {"headers": {"x-auth-token": "alice-token"}, "player": {"id": "a1", "name": "Alice"}})
    SessionManager.repo.save_data("Bob", {"headers": {"x-auth-token": "bob-token"}, "player": {"id": "b1", "name": "Bob"}})

    import pytest

    with pytest.raises(AccountAmbiguityError):
        load_session_headers()

def test_load_session_headers_account(temp_session_files):
    """アカウント指定がある場合に、自動移行経由で session.{account}.json が読み込まれること"""
    data = {"headers": {"x-auth-token": "your-alice-token"}, "player": {"id": "alice_id", "name": "Alice"}}
    with open(temp_session_files / "session.Alice.json", "w") as f:
        json.dump(data, f)
    
    headers = load_session_headers("Alice")
    assert headers["x-auth-token"] == "your-alice-token"

def test_load_session_headers_fallback(temp_session_files):
    """指定したアカウントファイルがなく、DBにもない場合に None を返すこと (以前のフォールバック動作は現在サポート外)"""
    headers = load_session_headers("Unknown")
    assert headers is None

def test_load_session_headers_from_db(temp_session_files):
    """DBにデータがある場合、ファイルがなくても読み込めること"""
    data = {"headers": {"x-auth-token": "your-db-token"}, "player": {"id": "dbuser_id", "name": "dbuser"}}
    SessionManager.repo.save_data("dbuser", data)
    
    headers = load_session_headers("dbuser")
    assert headers["x-auth-token"] == "your-db-token"

def test_resolve_account_migrates_legacy_session_json(tmp_path, monkeypatch):
    """DB空 + session.json 存在時、-a なしの resolve が実名で移行保存して解決する。"""
    from unittest.mock import patch
    from hw_genie.core.client import resolve_account

    session_data = {
        "headers": {"x-auth-token": "legacy-token"},
        "player": {"id": "legacy_id", "name": "LegacyPlayer"},
    }
    legacy_path = tmp_path / "session.json"
    with open(legacy_path, "w") as f:
        json.dump(session_data, f)

    with patch("hw_genie.core.auth.get_session_path", return_value=str(legacy_path)):
        resolved = resolve_account(None)

    # 実名で解決され、DB には実名で保存されている（default エイリアスは作られない）
    assert resolved == "LegacyPlayer"
    assert SessionManager.list_accounts() == ["LegacyPlayer"]
    headers = load_session_headers("LegacyPlayer")
    assert headers["x-auth-token"] == "legacy-token"

def test_resolve_account_no_accounts_raises_not_found(tmp_path, monkeypatch):
    """DB空 + session.json なしの場合は AccountNotFoundError になる。"""
    from unittest.mock import patch
    from hw_genie.core.client import resolve_account, AccountNotFoundError

    with patch("hw_genie.core.auth.get_session_path", return_value=str(tmp_path / "session.json")):
        with pytest.raises(AccountNotFoundError):
            resolve_account(None)
