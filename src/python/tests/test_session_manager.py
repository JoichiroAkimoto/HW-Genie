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
