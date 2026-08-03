from hw_genie.core.session_manager import SessionManager


def test_load_nonexistent_account():
    # 存在しないアカウントは空dictを返す
    assert SessionManager.load("nonexistent") == {}


def test_save_and_load():
    player_with_id = {"id": "test_id", "name": "Test User"}
    player_without_id = {"name": "Test User"}
    data = {"player": player_with_id, "some_key": "some_value"}
    expected = {"player": player_without_id, "some_key": "some_value"}
    SessionManager.save("test_account", data)
    assert SessionManager.load("test_account") == expected


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
    player_with_id = {"id": "overwrite_id", "name": "Overwrite User"}
    player_without_id = {"name": "Overwrite User"}
    data1 = {"player": player_with_id, "val": 1}
    data2 = {"player": player_with_id, "val": 2}
    expected2 = {"player": player_without_id, "val": 2}

    SessionManager.save(account, data1)
    SessionManager.save(account, data2)

    loaded = SessionManager.load(account)
    assert loaded == expected2


def test_account_isolation():
    """異なるアカウント間でデータが混在しないことを検証"""
    user1 = "user_1"
    user2 = "user_2"
    data1 = {"player": {"id": "id_1", "name": "User One"}, "name": "User One"}
    data2 = {"player": {"id": "id_2", "name": "User Two"}, "name": "User Two"}
    expected1 = {"player": {"name": "User One"}, "name": "User One"}
    expected2 = {"player": {"name": "User Two"}, "name": "User Two"}

    SessionManager.save(user1, data1)
    SessionManager.save(user2, data2)

    assert SessionManager.load(user1) == expected1
    assert SessionManager.load(user2) == expected2


def test_get_set_mission_id():
    account = "default"
    SessionManager.save(account, {"player": {"id": "def_id", "name": "Default"}})
    SessionManager.set_last_mission_id(456, account=account)
    assert SessionManager.get_last_mission_id(account=account) == 456


def test_last_mission_id_persistence():
    """last_mission_id が正しく保存され、ロードされることを検証"""
    account = "mission_test_user"
    SessionManager.save(account, {"player": {"id": "miss_id", "name": "Mission User"}})
    mission_id = 789

    SessionManager.set_last_mission_id(mission_id, account=account)

    # ロードして確認
    loaded_data = SessionManager.load(account)
    assert loaded_data.get("last_item_raid_mission_id") == mission_id


def test_save_without_mission_key_preserves_stored_mission_id():
    """mission_id を含まないデータの保存で、既存の last_item_raid_mission_id が消えないことを検証"""
    account = "preserve_mission_user"
    SessionManager.save(account, {"player": {"id": "pm_id", "name": "Preserve"}})
    SessionManager.set_last_mission_id(123, account=account)

    SessionManager.save(account, {"player": {"id": "pm_id", "name": "Preserve"}, "status": "success"})

    loaded_data = SessionManager.load(account)
    assert loaded_data.get("last_item_raid_mission_id") == 123
    assert loaded_data.get("status") == "success"


def test_set_last_mission_id_skips_write_when_unchanged(monkeypatch):
    """現在値と同値の set_last_mission_id は update_config を呼ばない"""
    from hw_genie.core.repository import SessionRepository

    account = "skip_write_user"
    SessionManager.save(account, {"player": {"id": "sw_id", "name": "Skip"}})

    calls = []
    real_update_config = SessionRepository.update_config

    def spy_update_config(self, a, d):
        calls.append((a, d))
        return real_update_config(self, a, d)

    monkeypatch.setattr(SessionRepository, "update_config", spy_update_config)

    # 初回: 未設定 (None) なので書き込み
    SessionManager.set_last_mission_id(456, account=account)
    assert len(calls) == 1

    # 同値: 書き込みスキップ
    SessionManager.set_last_mission_id(456, account=account)
    assert len(calls) == 1

    # 別値: 書き込み
    SessionManager.set_last_mission_id(789, account=account)
    assert len(calls) == 2
    assert SessionManager.get_last_mission_id(account=account) == 789


def test_set_last_mission_id_normalizes_string_input(monkeypatch):
    """文字列の mission_id でも int に正規化され、同値比較でスキップが機能する"""
    from hw_genie.core.repository import SessionRepository

    account = "str_mission_user"
    SessionManager.save(account, {"player": {"id": "sm_id", "name": "Str"}})

    calls = []
    real_update_config = SessionRepository.update_config

    def spy_update_config(self, a, d):
        calls.append((a, d))
        return real_update_config(self, a, d)

    monkeypatch.setattr(SessionRepository, "update_config", spy_update_config)

    # 文字列で設定 → int で保存される
    SessionManager.set_last_mission_id("456", account=account)
    assert len(calls) == 1
    assert SessionManager.get_last_mission_id(account=account) == 456

    # int 同値でも文字列でもスキップされる
    SessionManager.set_last_mission_id(456, account=account)
    SessionManager.set_last_mission_id("456", account=account)
    assert len(calls) == 1


def test_memo_persistence():
    """memoが正しく保存され、ロードされることを検証"""
    account = "memo_test_user"
    player_with_id = {"id": "memo_id", "name": "Memo User"}
    memo_text = "This is a test memo"

    data = {
        "player": player_with_id,
        "memo": memo_text,
        "some_key": "some_value"
    }
    
    SessionManager.save(account, data)
    
    # ロードして確認
    loaded = SessionManager.load(account)
    assert loaded.get("memo") == memo_text
    
    # データベースのモデルを直接確認
    from hw_genie.core.database import SessionLocal, Account
    with SessionLocal() as db:
        rec = db.query(Account).filter(Account.alias == account).first()
        assert rec is not None
        assert rec.memo == memo_text


def test_save_strips_alias_whitespace():
    """保存時にエイリアスの前後空白が除去されることを検証"""
    from hw_genie.core.database import SessionLocal, Account

    SessionManager.save("  spaced_user  ", {"player": {"id": "sp_id", "name": "Spaced"}})
    with SessionLocal() as db:
        rec = db.query(Account).filter(Account.player_id == "sp_id").first()
        assert rec.alias == "spaced_user"
    # トリム済みエイリアスでロードできる
    assert SessionManager.load("spaced_user")["player"]["name"] == "Spaced"


def test_save_normalizes_trailing_space_existing_alias():
    """既存レコードの alias に末尾スペースがあっても、トリム済みで上書きされる"""
    from hw_genie.core.database import SessionLocal, Account

    # 意図的に末尾スペース付きで作成（DB 層を直接操作して再現）
    with SessionLocal() as db:
        db.add(Account(player_id="tr_id", alias="Trailing ", player_name="Trailing"))
        db.commit()

    # トリム済みエイリアスで保存 → 既存レコードを正規化して上書き
    SessionManager.save("Trailing", {"player": {"id": "tr_id", "name": "Trailing Fixed"}})
    with SessionLocal() as db:
        recs = db.query(Account).filter(Account.player_id == "tr_id").all()
        assert len(recs) == 1
        assert recs[0].alias == "Trailing"
    assert SessionManager.load("Trailing")["player"]["name"] == "Trailing Fixed"
    # 大文字小文字・前後空白を無視した照合により、スペース付きでも正しくロードできる
    assert SessionManager.load("Trailing ")["player"]["name"] == "Trailing Fixed"

