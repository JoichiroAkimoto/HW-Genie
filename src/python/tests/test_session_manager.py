import os
import json
from hw_genie.core.session_manager import SessionManager


def test_load_nonexistent_account():
    # 存在しないアカウントは空dictを返す
    assert SessionManager.load("nonexistent") == {}


def test_save_and_load():
    data = {"some_key": "some_value"}
    SessionManager.save("test_account", data)
    assert SessionManager.load("test_account") == data


def test_prevent_account_duplication():
    """同じ player_id を持つアカウントを異なる alias で保存したとき、重複せず更新されることを検証"""
    from hw_genie.core.database import SessionLocal, Account

    # 1回目の保存: alias="user1", player_id="id_123"
    data1 = {"player": {"id": "id_123", "name": "User One"}}
    SessionManager.save("user1", data1)

    # 2回目の保存: alias="user1_alt", player_id="id_123"
    data2 = {"player": {"id": "id_123", "name": "User One Updated"}}
    SessionManager.save("user1_alt", data2)

    # DBを確認してレコードが1つであることを検証
    with SessionLocal() as db:
        count = db.query(Account).filter(Account.player_id == "id_123").count()
        assert count == 1
        rec = db.query(Account).filter(Account.player_id == "id_123").first()
        assert rec.alias == "user1_alt"

    # alias "user1_alt" でロードできることを検証
    loaded_alt = SessionManager.load("user1_alt")
    assert loaded_alt["player"]["name"] == "User One Updated"

    # 旧 alias "user1" ではロードできないことを検証
    assert SessionManager.load("user1") == {}


def test_save_overwrites_existing():
    """同一アカウントで保存した場合、データが正しく更新されることを検証"""
    account = "tdd_overwrite_user"
    data1 = {"val": 1}
    data2 = {"val": 2}

    SessionManager.save(account, data1)
    SessionManager.save(account, data2)

    loaded = SessionManager.load(account)
    assert loaded == data2


def test_account_isolation():
    """異なるアカウント間でデータが混在しないことを検証"""
    user1 = "user_1"
    user2 = "user_2"
    data1 = {"name": "User One"}
    data2 = {"name": "User Two"}

    SessionManager.save(user1, data1)
    SessionManager.save(user2, data2)

    assert SessionManager.load(user1) == data1
    assert SessionManager.load(user2) == data2


def test_get_set_mission_id():
    SessionManager.set_last_mission_id(456, account="default")
    assert SessionManager.get_last_mission_id(account="default") == 456


def test_last_mission_id_persistence():
    """last_mission_id が正しく保存され、ロードされることを検証"""
    account = "mission_test_user"
    mission_id = 789

    SessionManager.set_last_mission_id(mission_id, account=account)

    # ロードして確認
    loaded_data = SessionManager.load(account)
    assert loaded_data.get("last_item_raid_mission_id") == mission_id


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
