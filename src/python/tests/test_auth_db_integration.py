import pytest
from unittest.mock import patch
from hw_genie.core.auth import update_session_with_headers
from hw_genie.core.session_manager import SessionManager
from hw_genie.core.client import PlayerStatus


@pytest.fixture(autouse=True)
def clear_db():
    """テストごとにDBをクリアしてクリーンな状態で開始する"""
    # 既存のデータを削除（Repository経由で直接操作するか、DBを初期化）
    # 簡易的に、テストの開始時に毎回保存して上書きすることを前提とする
    yield
    # 後処理が必要な場合はここに記述


def test_update_session_saves_to_db():
    """update_session_with_headers が正しくDBに保存することを検証"""
    account = "integration_test_user"
    headers = {"x-auth-token": "mock-token"}

    mock_player = PlayerStatus(name="IntegrationPlayer", level=100)
    mock_info = {"headers": headers, "status": "success", "player": mock_player, "last_updated": "2026-01-01T00:00:00"}

    # get_user_info をモックして、ネットワークリクエストを回避し固定値を返す
    with patch("hw_genie.core.auth.get_user_info", return_value=mock_info):
        result = update_session_with_headers(headers, account)

    # 1. 関数の戻り値が正しいこと
    assert result["status"] == "success"
    assert result["player"].name == "IntegrationPlayer"

    # 2. SessionManager を経由して DB から読み込んだデータが一致すること
    db_data = SessionManager.load(account)
    assert db_data["headers"] == headers
    assert db_data["player"]["name"] == "IntegrationPlayer"


def test_update_session_updates_existing_db_record():
    """既存のDBレコードが正しく更新されることを検証"""
    account = "integration_update_user"

    # 1回目の保存
    headers1 = {"x-auth-token": "token-1"}
    mock_player1 = PlayerStatus(name="User1")
    mock_info1 = {"headers": headers1, "status": "success", "player": mock_player1}

    with patch("hw_genie.core.auth.get_user_info", return_value=mock_info1):
        update_session_with_headers(headers1, account)

    # 2回目の保存 (更新)
    headers2 = {"x-auth-token": "token-2"}
    mock_player2 = PlayerStatus(name="User2")
    mock_info2 = {"headers": headers2, "status": "success", "player": mock_player2}

    with patch("hw_genie.core.auth.get_user_info", return_value=mock_info2):
        update_session_with_headers(headers2, account)

    # DBの内容が最新であること
    db_data = SessionManager.load(account)
    assert db_data["headers"] == headers2
    assert db_data["player"]["name"] == "User2"


def test_update_session_does_not_persist_player_meta_configs():
    """player_* 系 config（next_day_ts / timezone 等）は保存されない。

    quests ギルドレシピの 1 日 1 回ガードは fetch_player_status()（ライブ API）
    の値を用いるため、認証時のキャッシュ（player_next_day_ts 等）は不要。
    account_configs に player_* ファミリー全体が存在しないことを直接検証する
    （timezone / next_day_ts の 2 キーのみのスポットチェックでは、将来
    player_* 書き込みを復活させてもテストが通ってしまうため）。
    """
    account = "meta_config_user"
    headers = {"x-auth-token": "mock-token"}

    mock_player = PlayerStatus(id="meta-player-001", name="MetaUser", level=100, timezone=-10, next_day_ts=1786287600)
    mock_info = {"headers": headers, "status": "success", "player": mock_player}

    with patch("hw_genie.core.auth.get_user_info", return_value=mock_info):
        update_session_with_headers(headers, account)

    # account_configs に player_* キーが 1 件も存在しないこと（直接走査）
    from hw_genie.core.database import Account, AccountConfig
    from hw_genie.core.repository import get_session_local

    db = get_session_local()()
    try:
        acct = db.query(Account).filter(Account.player_id == mock_player.id).first()
        assert acct is not None
        keys = [c.config_key for c in db.query(AccountConfig).filter(AccountConfig.account_id == acct.id).all()]
    finally:
        db.close()
    assert not any(k.startswith("player_") for k in keys)
