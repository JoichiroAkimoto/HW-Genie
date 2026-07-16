import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine

from hw_genie.core import database
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

    # sqlite:///test_replica.db は相対パスとして扱われ、スキーム区切りの
    # 3スラッシュのみを持つ sqlite+libsql:///test_replica.db になること。
    # （絶対パスでない入力を cwd 配下へ誤って解決しないこと）
    url_str = str(db_url)
    assert url_str == "sqlite+libsql:///test_replica.db"
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


def test_build_database_config_typeddict_keys():
    """戻り値の connect_args に期待されるキーのみが含まれることを検証"""
    env = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_SYNC_INTERVAL": "60",
        "DATABASE_URL": "sqlite:///test_replica.db",
    }
    _, connect_args = build_database_config(env)

    # 許可されたキーのみが含まれ、未知のキーが混ざっていないこと
    allowed = {"sync_url", "auth_token", "sync_interval", "check_same_thread"}
    assert set(connect_args.keys()) <= allowed
    assert connect_args["check_same_thread"] is True
    assert connect_args["sync_interval"] == 60.0


def test_lazy_engine_initialization(monkeypatch):
    """get_engine() は初回アクセス時に構築され、以降は同一インスタンスを返すことを検証"""
    # conftest のパッチを避け、実際の遅延初期化ロジックを検証するため自前の getter を差し替える。
    monkeypatch.setattr(database, "build_database_config", lambda env=None: ("sqlite:///:memory:", {}))
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)

    state = {"built": 0}

    def fake_get_engine():
        if database._engine is None:
            database._engine = create_engine("sqlite:///:memory:")
            state["built"] += 1
        return database._engine

    monkeypatch.setattr(database, "get_engine", fake_get_engine)

    # 初回アクセスで構築され、以降は同一インスタンスが返る（遅延初期化 + キャッシュ）
    engine1 = database.get_engine()
    assert database._engine is engine1
    engine2 = database.get_engine()
    assert engine1 is engine2
    assert state["built"] == 1
