import pytest
import os
import json
from hw_genie.core.session_manager import SessionManager

SESSION_FILE = "session.json"

@pytest.fixture(autouse=True)
def cleanup():
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
    yield
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)

def test_load_nonexistent_file():
    assert SessionManager.load() == {}

def test_save_and_load():
    data = {"last_item_raid_mission_id": 123}
    SessionManager.save(data)
    assert SessionManager.load() == data

def test_get_set_mission_id():
    SessionManager.set_last_mission_id(456)
    assert SessionManager.get_last_mission_id() == 456
    assert SessionManager.load()["last_item_raid_mission_id"] == 456
