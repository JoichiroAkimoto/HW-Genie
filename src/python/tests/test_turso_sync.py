import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hw_genie.core.database import build_database_config


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

    # 接続先URLが正確に sqlite+libsql:/// + 絶対パス になっていることを確認
    # （余分なスラッシュや cwd 配下への誤った解決がないこと）
    url_str = str(db_url)
    resolved = Path("test_replica.db").absolute()
    assert url_str == f"sqlite+libsql:///{resolved.as_posix().lstrip('/')}"
    assert "sqlite+libsql:////" not in url_str

    # connect_args に同期用パラメータが正しく渡されていることを確認
    assert connect_args.get("sync_url") == "libsql://my-test-db.turso.io"
    assert connect_args.get("auth_token") == "my-mock-auth-token"
    assert connect_args.get("sync_interval") == 123.45
    assert connect_args.get("check_same_thread") is True


def test_turso_sync_default_db_path():
    """DATABASE_URL が未設定（デフォルト絶対パス）の場合、正しい絶対パスが使われる"""
    # DATABASE_URL を明示的に与えない（デフォルト値 DEFAULT_DB_PATH が使われる）
    env = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
    }
    from hw_genie.core.database import DEFAULT_DB_PATH

    db_url, connect_args = build_database_config(env)

    url_str = str(db_url)
    # デフォルトの絶対パスが正しく解決されていること（余分なスラッシュで cwd 配下になっていない）
    resolved = Path(DEFAULT_DB_PATH).absolute()
    assert url_str == f"sqlite+libsql:///{resolved.as_posix().lstrip('/')}"
    assert "sqlite+libsql:////" not in url_str
    assert connect_args.get("sync_url") == "libsql://my-test-db.turso.io"


def test_turso_sync_libsql_url_input():
    """DATABASE_URL にすでに sqlite+libsql:/// 形式の絶対パスが指定されている場合"""
    env = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "DATABASE_URL": "sqlite+libsql:////tmp/existing_replica.db",
    }
    db_url, connect_args = build_database_config(env)

    url_str = str(db_url)
    assert url_str == "sqlite+libsql:///tmp/existing_replica.db"
    assert "sqlite+libsql:////" not in url_str
    assert connect_args.get("sync_url") == "libsql://my-test-db.turso.io"


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
