import pytest
import os
import json
from hw_genie.core.auth import save_session

TEST_SESSION_FILE = "session.json"

@pytest.fixture(autouse=True)
def cleanup():
    if os.path.exists(TEST_SESSION_FILE):
        os.remove(TEST_SESSION_FILE)
    yield
    if os.path.exists(TEST_SESSION_FILE):
        os.remove(TEST_SESSION_FILE)

def test_save_session_preserves_mission_id():
    # 既存の session.json を作成
    initial_mission_id = 123
    initial_data = {
        "status": "success",
        "last_updated": "2026-04-09T00:00:00",
        "last_item_raid_mission_id": initial_mission_id
    }
    with open(TEST_SESSION_FILE, "w") as f:
        json.dump(initial_data, f)
    
    # 新しい情報を保存（mission_id は含まない）
    new_data = {
        "status": "success",
        "last_updated": "2026-04-09T01:00:00"
    }
    
    # ここで save_session を呼ぶ。
    # 修正前なら上書きされてしまい last_item_raid_mission_id は消える。
    save_session(new_data, "default")
    
    # 結果を確認
    with open(TEST_SESSION_FILE, "r") as f:
        saved_data = json.load(f)
    
    assert saved_data.get("last_item_raid_mission_id") == initial_mission_id

