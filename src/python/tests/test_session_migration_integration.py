import os
import json
from hw_genie.core.client import load_session_headers
from hw_genie.core.session_manager import SessionManager


def test_load_session_headers_triggers_migration(tmp_path, monkeypatch):
    """JSONファイルが存在する場合、load_session_headers が DB への移行をトリガーすることを検証"""
    # 1. JSONファイルを配置
    account = "migrator"
    session_data = {"headers": {"x-auth-token": "migration-token"}}
    json_file = tmp_path / f"session.{account}.json"
    json_file.write_text(json.dumps(session_data))

    # 2. get_session_path が tmp_path を見るようにモック
    monkeypatch.setattr("hw_genie.core.auth.get_session_path", lambda acc: str(tmp_path / f"session.{acc}.json"))

    # 3. 読み込み実行 (このとき DB は空であるはず)
    headers = load_session_headers(account)

    # 4. 正しく読み込めたか
    assert headers["x-auth-token"] == "migration-token"

    # 5. DB に保存されたか確認
    db_data = SessionManager.load(account)
    assert db_data["headers"]["x-auth-token"] == "migration-token"

    # 6. JSONを削除しても読めるか
    os.remove(json_file)
    assert load_session_headers(account)["x-auth-token"] == "migration-token"
