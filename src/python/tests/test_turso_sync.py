import logging
from unittest.mock import patch

import pytest

from hw_genie.core.database import build_database_config


@pytest.fixture(autouse=True)
def mock_no_env():
    # .env ファイルが存在しないと判定させて、実際の .env の読み込みを防ぐ
    real_exists = __import__("os").path.exists

    def side_effect(path):
        if str(path).endswith(".env"):
            return False
        return real_exists(path)

    with patch("os.path.exists", side_effect=side_effect):
        yield


def test_turso_sync_config_not_set():
    """TURSO_SYNC_URL が設定されていない場合の動作を検証"""
    env = {
        "DATABASE_URL": "sqlite:///test_local.db",
    }
    db_url, connect_args = build_database_config(env)

    assert "sqlite:///test_local.db" in str(db_url)
    # connect_args に sync_url などが含まれていないことを確認
    assert "sync_url" not in connect_args
    assert "auth_token" not in connect_args
    assert "sync_interval" not in connect_args


def test_turso_sync_config_set():
    """TURSO_SYNC_URL などの各種同期用環境変数が設定されている場合の動作を検証"""
    env = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_SYNC_INTERVAL": "123.45",
        "DATABASE_URL": "sqlite:///test_replica.db",
    }
    db_url, connect_args = build_database_config(env)

    # 接続先URLが sqlite+libsql:/// に変更されていることを確認
    url_str = str(db_url)
    assert url_str.startswith("sqlite+libsql://")
    assert "test_replica.db" in url_str

    # connect_args に同期用パラメータが正しく渡されていることを確認
    assert connect_args.get("sync_url") == "libsql://my-test-db.turso.io"
    assert connect_args.get("auth_token") == "my-mock-auth-token"
    assert connect_args.get("sync_interval") == 123.45
    assert connect_args.get("check_same_thread") is True


def test_turso_sync_invalid_interval():
    """TURSO_SYNC_INTERVAL に無効な文字列が設定されている場合の動作を検証"""
    env = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_SYNC_INTERVAL": "not-a-number",
        "DATABASE_URL": "sqlite:///test_replica.db",
    }
    with patch.object(logging.getLogger("hw_genie.core.database"), "warning") as mock_warn:
        _, connect_args = build_database_config(env)

    assert connect_args.get("sync_url") == "libsql://my-test-db.turso.io"
    assert "sync_interval" not in connect_args
    # 無効な値は警告ログとして報告される
    mock_warn.assert_called_once()
