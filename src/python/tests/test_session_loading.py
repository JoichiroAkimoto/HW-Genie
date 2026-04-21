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

def test_load_session_headers_default(temp_session_files):
    """アカウント指定なしの場合に、自動移行経由で session.json が読み込まれること"""
    data = {"headers": {"x-auth-token": "default-token"}, "player": {"id": "default_id", "name": "Default"}}
    with open(temp_session_files / "session.json", "w") as f:
        json.dump(data, f)
    
    headers = load_session_headers()
    assert headers["x-auth-token"] == "default-token"

def test_load_session_headers_account(temp_session_files):
    """アカウント指定がある場合に、自動移行経由で session.{account}.json が読み込まれること"""
    data = {"headers": {"x-auth-token": "joe-token"}, "player": {"id": "joe_id", "name": "Joe"}}
    with open(temp_session_files / "session.Joe.json", "w") as f:
        json.dump(data, f)
    
    headers = load_session_headers("Joe")
    assert headers["x-auth-token"] == "joe-token"

def test_load_session_headers_fallback(temp_session_files):
    """指定したアカウントファイルがなく、DBにもない場合に None を返すこと (以前のフォールバック動作は現在サポート外)"""
    headers = load_session_headers("Unknown")
    assert headers is None

def test_load_session_headers_from_db(temp_session_files):
    """DBにデータがある場合、ファイルがなくても読み込めること"""
    data = {"headers": {"x-auth-token": "db-token"}, "player": {"id": "dbuser_id", "name": "dbuser"}}
    SessionManager.repo.save_data("dbuser", data)
    
    headers = load_session_headers("dbuser")
    assert headers["x-auth-token"] == "db-token"
