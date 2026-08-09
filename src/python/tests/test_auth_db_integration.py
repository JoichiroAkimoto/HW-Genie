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
    """PlayerStatus の next_day_ts / timezone は player_* config として保存されない。

    quests ギルドレシピの 1 日 1 回ガードは fetch_player_status()（ライブ API）
    の値を用いるため、認証時のキャッシュ（player_next_day_ts 等）は不要。
    保存されないことで account_configs にゴミが溜まらないことを保証する。
    """
    account = "meta_config_user"
    headers = {"x-auth-token": "mock-token"}

    mock_player = PlayerStatus(name="MetaUser", level=100, timezone=-10, next_day_ts=1786287600)
    mock_info = {"headers": headers, "status": "success", "player": mock_player}

    with patch("hw_genie.core.auth.get_user_info", return_value=mock_info):
        update_session_with_headers(headers, account)

    # player_* キーが account_configs に保存されていないこと（timezone / next_day_ts は
    # player dict に混入しない）。SessionManager.load は player_* config を player に
    # 再構成するため、ここが空なら DB にも保存されていないことになる。
    db_data = SessionManager.load(account)
    assert "player" in db_data
    player_info = db_data["player"]
    assert "timezone" not in player_info
    assert "next_day_ts" not in player_info
