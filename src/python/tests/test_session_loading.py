import pytest
import json
from hw_genie.core.client import load_session_headers

@pytest.fixture
def temp_session_files(tmp_path):
    """一時ディレクトリにテスト用セッションファイルを配置する"""
    # カレントディレクトリをシミュレートするため tmp_path を使用
    # load_session_headers は相対パス "session.json" を見るため、
    # テスト実行時に os.chdir で移動するか、モックしてパスを差し替える必要がある。
    # ここでは、load_session_headers が参照する相対パスを mock するか、
    # 単純にファイルを配置して os.chdir する。
    
    default_file = tmp_path / "session.json"
    default_file.write_text(json.dumps({"headers": {"x-auth-token": "default-token"}}))
    
    joe_file = tmp_path / "session.Joe.json"
    joe_file.write_text(json.dumps({"headers": {"x-auth-token": "joe-token"}}))
    
    return tmp_path

def test_load_session_headers_default(temp_session_files, monkeypatch):
    """アカウント指定なし（または 'default'）の場合に session.json が読み込まれること"""
    monkeypatch.chdir(temp_session_files)
    
    # 指定なし
    assert load_session_headers()["x-auth-token"] == "default-token"
    # 'default' 指定
    assert load_session_headers("default")["x-auth-token"] == "default-token"
    # None 指定
    assert load_session_headers(None)["x-auth-token"] == "default-token"

def test_load_session_headers_account(temp_session_files, monkeypatch):
    """アカウント指定がある場合に session.{account}.json が優先的に読み込まれること"""
    monkeypatch.chdir(temp_session_files)
    
    assert load_session_headers("Joe")["x-auth-token"] == "joe-token"

def test_load_session_headers_fallback(temp_session_files, monkeypatch):
    """指定したアカウントファイルがない場合に session.json にフォールバックすること"""
    monkeypatch.chdir(temp_session_files)
    
    assert load_session_headers("Unknown")["x-auth-token"] == "default-token"

def test_load_session_headers_path_traversal(temp_session_files, monkeypatch):
    """パス・トラバーサル攻撃が防止され、安全なファイル名として処理されること"""
    monkeypatch.chdir(temp_session_files)
    
    # ../.. と指定しても、os.path.basename により "passwd" となり、
    # session.passwd.json を探しに行くはず（存在しないのでフォールバックして session.json になる）
    assert load_session_headers("../../etc/passwd")["x-auth-token"] == "default-token"
