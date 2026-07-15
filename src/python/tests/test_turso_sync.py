import os
import importlib
from unittest.mock import patch
import pytest

import hw_genie.core.database


@pytest.fixture(autouse=True)
def mock_no_env():
    # .env ファイルが存在しないと判定させて、実際の .env の読み込みを防ぐ
    real_exists = os.path.exists
    def side_effect(path):
        if str(path).endswith(".env"):
            return False
        return real_exists(path)
        
    with patch("os.path.exists", side_effect=side_effect):
        yield


@pytest.fixture(autouse=True)
def cleanup_database_module():
    yield
    # テスト終了後にモジュールを元の状態（デフォルト）にリロード
    importlib.reload(hw_genie.core.database)


def test_turso_sync_config_not_set():
    """TURSO_SYNC_URL が設定されていない場合の動作を検証"""
    env_mock = {
        "TURSO_SYNC_URL": "",
        "TURSO_AUTH_TOKEN": "",
        "TURSO_SYNC_INTERVAL": "",
        "DATABASE_URL": "sqlite:///test_local.db"
    }
    with patch.dict(os.environ, env_mock, clear=True):
        # モジュールを再ロードして設定を再評価
        importlib.reload(hw_genie.core.database)
        
        assert "sqlite:///test_local.db" in str(hw_genie.core.database.engine.url)
        # connect_args に sync_url などが含まれていないことを確認
        connect_args = hw_genie.core.database.connect_args
        assert "sync_url" not in connect_args
        assert "auth_token" not in connect_args
        assert "sync_interval" not in connect_args


def test_turso_sync_config_set():
    """TURSO_SYNC_URL などの各種同期用環境変数が設定されている場合の動作を検証"""
    env_mock = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_SYNC_INTERVAL": "123.45",
        "DATABASE_URL": "sqlite:///test_replica.db"
    }
    with patch.dict(os.environ, env_mock, clear=True):
        # モジュールを再ロードして設定を再評価
        importlib.reload(hw_genie.core.database)
        
        # 接続先URLが sqlite+libsql:/// に変更されていることを確認
        url_str = str(hw_genie.core.database.engine.url)
        assert url_str.startswith("sqlite+libsql://")
        assert "test_replica.db" in url_str
        
        # connect_args に同期用パラメータが正しく渡されていることを確認
        connect_args = hw_genie.core.database.connect_args
        assert connect_args.get("sync_url") == "libsql://my-test-db.turso.io"
        assert connect_args.get("auth_token") == "my-mock-auth-token"
        assert connect_args.get("sync_interval") == 123.45
        assert connect_args.get("check_same_thread") is True


def test_turso_sync_invalid_interval():
    """TURSO_SYNC_INTERVAL に無効な文字列が設定されている場合の動作を検証"""
    env_mock = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_SYNC_INTERVAL": "not-a-number",
        "DATABASE_URL": "sqlite:///test_replica.db"
    }
    with patch.dict(os.environ, env_mock, clear=True):
        # モジュールを再ロードして設定を再評価
        importlib.reload(hw_genie.core.database)
        
        connect_args = hw_genie.core.database.connect_args
        assert connect_args.get("sync_url") == "libsql://my-test-db.turso.io"
        assert "sync_interval" not in connect_args
