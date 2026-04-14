import os
import json
from hw_genie.core.session_manager import SessionManager

def test_load_nonexistent_account():
    # 存在しないアカウントは空dictを返す
    assert SessionManager.load("nonexistent") == {}

def test_save_and_load():
    data = {"some_key": "some_value"}
    SessionManager.repo.save_data("test_account", data)
    assert SessionManager.load("test_account") == data

def test_get_set_mission_id():
    SessionManager.set_last_mission_id(456, account="default")
    assert SessionManager.get_last_mission_id(account="default") == 456

def test_migration_from_json(tmp_path, monkeypatch):
    """jsonファイルが存在する場合、初回アクセス時にDBへ自動移行されることを検証"""
    session_data = {"headers": {"test": "header"}, "player": {"name": "MigratedUser"}}
    # アカウント名 'migrated' のファイルパスを作成
    json_file = tmp_path / "session.migrated.json"
    with open(json_file, "w") as f:
        json.dump(session_data, f)
    
    # core.auth.get_session_path を tmp_path を見るように差し替え
    monkeypatch.setattr("hw_genie.core.auth.get_session_path", lambda acc: str(tmp_path / f"session.{acc}.json"))
    
    # 初回ロード: JSONから読み込まれ、DBに保存されるはず
    loaded_data = SessionManager.load("migrated")
    assert loaded_data["player"]["name"] == "MigratedUser"
    
    # ファイルを削除しても、DBから読み込めるはず
    os.remove(json_file)
    db_data = SessionManager.load("migrated")
    assert db_data == loaded_data
