import pytest
import os
import json
from hw_genie.core.session_manager import SessionManager

# セッションファイルのデフォルトパスをテスト用に一時的に設定
TEST_SESSION_FILE = "session.json"

@pytest.fixture(autouse=True)
def cleanup():
    if os.path.exists(TEST_SESSION_FILE):
        os.remove(TEST_SESSION_FILE)
    # キャッシュをクリア
    SessionManager._cached_data = None
    SessionManager._loaded_path = None
    yield
    if os.path.exists(TEST_SESSION_FILE):
        os.remove(TEST_SESSION_FILE)

def test_load_nonexistent_file():
    assert SessionManager.load() == {}

def test_save_and_load():
    data = {"some_key": "some_value"}
    SessionManager._cached_data = data
    SessionManager._loaded_path = TEST_SESSION_FILE
    SessionManager.save()
    
    # キャッシュをクリアして読み直す
    SessionManager._cached_data = None
    assert SessionManager.load() == data

def test_get_set_mission_id():
    SessionManager.set_last_mission_id(456)
    assert SessionManager.get_last_mission_id() == 456
    # 物理ファイルを確認
    with open(TEST_SESSION_FILE, "r") as f:
        assert json.load(f)["last_item_raid_mission_id"] == 456
