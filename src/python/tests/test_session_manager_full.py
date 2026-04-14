from hw_genie.core.session_manager import SessionManager


def test_save_and_load_via_manager():
    """SessionManager.save で保存し、SessionManager.load で復元できることを検証"""
    account = "tdd_test_user"
    data = {"headers": {"Auth": "123"}, "player": {"name": "TDD User"}}

    # 実装予定のメソッドを呼び出し
    SessionManager.save(account, data)

    loaded = SessionManager.load(account)
    assert loaded == data


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
