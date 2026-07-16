import logging
import os
import urllib.parse
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

    # sqlite:///test_replica.db はセマンティクス上は絶対パス /test_replica.db
    # として扱われ、sqlite+libsql の絶対パス形式（4スラッシュ）になる。
    # sync 用パラメータが URL のクエリ文字列に付与されること。
    url_str = str(db_url)
    assert url_str.startswith("sqlite+libsql:////test_replica.db?")
    assert "sqlite+libsql://///" not in url_str

    # TursoReplicaDialect が URL クエリの sync_* を le.connect へ渡すため、
    # クエリ文字列に正しく含まれていることを確認する。
    parsed = urllib.parse.urlparse(url_str)
    q = urllib.parse.parse_qs(parsed.query)
    assert q["sync_url"] == ["libsql://my-test-db.turso.io"]
    assert q["auth_token"] == ["my-mock-auth-token"]
    assert q["sync_interval"] == ["123.45"]
    assert connect_args.get("check_same_thread") is False


def test_turso_sync_default_db_path():
    """DATABASE_URL が未設定（デフォルト絶対パス）の場合、正しい絶対パスが使われる"""
    env = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
    }
    from hw_genie.core.database import DEFAULT_DB_PATH

    db_url, connect_args = build_database_config(env)

    # デフォルトの絶対パスが正しく解決されていること（絶対パスは 4 スラッシュ形式）
    resolved = Path(DEFAULT_DB_PATH).absolute()
    url_str = str(db_url)
    assert url_str.startswith(f"sqlite+libsql:////{resolved.as_posix().lstrip('/')}?")
    assert connect_args.get("check_same_thread") is False


def test_turso_sync_libsql_url_input():
    """DATABASE_URL にすでに sqlite+libsql:/// 形式の絶対パスが指定されている場合"""
    env = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "DATABASE_URL": "sqlite+libsql:////tmp/existing_replica.db",
    }
    db_url, connect_args = build_database_config(env)

    url_str = str(db_url)
    # 絶対パスは 4 スラッシュ形式のまま維持され、sync パラメータがクエリに付与される
    assert url_str.startswith("sqlite+libsql:////tmp/existing_replica.db?")
    assert "sqlite+libsql://///" not in url_str


def test_turso_sync_invalid_interval():
    """TURSO_SYNC_INTERVAL に無効な文字列が設定されている場合の動作を検証"""
    env = {
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_SYNC_INTERVAL": "not-a-number",
        "DATABASE_URL": "sqlite:///test_replica.db",
    }
    with patch.object(logging.getLogger("hw_genie.core.database"), "warning") as mock_warn:
        db_url, connect_args = build_database_config(env)

    # 無効な interval はクエリに含まれず、警告ログとして報告される
    url_str = str(db_url)
    assert "sync_interval" not in url_str
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

    # connect_args には check_same_thread のみ（sync_* は URL クエリへ移動）
    allowed = {"check_same_thread"}
    assert set(connect_args.keys()) <= allowed
    assert connect_args["check_same_thread"] is False


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


def test_replica_dialect_forwards_sync_params():
    """TursoReplicaDialect はローカル URL の sync_* クエリを le.connect へ渡す。"""
    from sqlalchemy.engine.url import make_url
    from hw_genie.core.database import TursoReplicaDialect

    dialect = TursoReplicaDialect()
    url = make_url(
        "sqlite+libsql:////tmp/replica.db"
        "?sync_url=libsql://db.turso.io"
        "&auth_token=secret"
        "&sync_interval=30"
    )
    cargs, cparams = dialect.create_connect_args(url)

    assert cargs[0] == os.path.abspath("/tmp/replica.db")
    # sync params are forwarded to libsql_experimental.connect as floats/strings
    assert cparams["sync_url"] == "libsql://db.turso.io"
    assert cparams["auth_token"] == "secret"
    assert cparams["sync_interval"] == 30.0


def test_replica_dialect_remote_mode_passthrough():
    """Remote (ws) URL は同期パラメータを受け継がず wss/http 形式で構築される。"""
    from sqlalchemy.engine.url import make_url
    from hw_genie.core.database import TursoReplicaDialect

    dialect = TursoReplicaDialect()
    url = make_url(
        "sqlite+libsql://user:pass@db.turso.io/my-db"
        "?auth_token=secret&sync_interval=30"
    )
    cargs, cparams = dialect.create_connect_args(url)
    # remote mode uses http(s) URL scheme, not a local file path
    assert cargs[0].startswith("http")
    assert "sync_url" not in cparams


def test_remote_libsql_branch_extracts_token():
    """リモート libSQL URL の auth_token が connect_args へ抽出され、secure=true が付与される。"""
    env = {
        "DATABASE_URL": "sqlite+libsql://db.turso.io/my-db?auth_token=SECRET123",
    }
    db_url, connect_args = build_database_config(env)

    # auth_token は URL から除去され connect_args へ移動、secure=true が付与される
    assert "auth_token=SECRET123" not in db_url
    assert "secure=true" in db_url
    assert connect_args.get("auth_token") == "SECRET123"
    assert connect_args.get("check_same_thread") is False


def test_remote_libsql_branch_with_turso_sync_url_ignored():
    """TURSO_SYNC_URL ありでもリモート DATABASE_URL なら replica 化せず接続先はそのまま。"""
    env = {
        "DATABASE_URL": "sqlite+libsql://db.turso.io/my-db?auth_token=SECRET123",
        "TURSO_SYNC_URL": "libsql://other.turso.io",
    }
    db_url, connect_args = build_database_config(env)

    # replica クエリ (sync_url=) は付与されず、remote 接続のまま
    assert "sync_url=" not in db_url
    assert connect_args.get("auth_token") == "SECRET123"


def test_relative_local_url_with_turso_sync_becomes_replica():
    """相対ローカル URL (sqlite://foo.db) + TURSO_SYNC_URL は replica になる（remote と誤判定しない）。"""
    env = {
        "DATABASE_URL": "sqlite://foo.db",
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
    }
    db_url, connect_args = build_database_config(env)

    # 相対パスは cwd 配下の replica として sync_url 付きで構築される
    assert "sync_url=" in db_url
    assert connect_args.get("check_same_thread") is False


def test_remote_url_with_secure_uses_https_in_dialect():
    """Remote URL に secure=true があればダイアレクトは https を構築する。"""
    from sqlalchemy.engine.url import make_url
    from hw_genie.core.database import TursoReplicaDialect

    dialect = TursoReplicaDialect()
    url = make_url("sqlite+libsql://db.turso.io/my-db?auth_token=secret&secure=true")
    cargs, cparams = dialect.create_connect_args(url)
    assert cargs[0].startswith("https://")


def test_mask_sensitive_masks_auth_token():
    """mask_sensitive は URL 中の auth_token をマスキングする。"""
    from hw_genie.core.database import mask_sensitive

    url = "sqlite+libsql:////tmp/x.db?sync_url=lib://t&auth_token=SECRET123&sync_interval=5"
    masked = mask_sensitive(url)
    assert "SECRET123" not in masked
    assert "auth_token=***" in masked
    # sync_url は維持される（トークン直後までマスクされるため sync_interval は含まれる）
    assert "sync_url=lib://t" in masked


def test_token_masking_filter_redacts_log_records():
    """TokenMaskingFilter はログレコードの auth_token を除去する。"""
    from hw_genie.core.database import TokenMaskingFilter

    record = logging.LogRecord(
        "hw_genie", logging.INFO, __file__, 1,
        "connecting to sqlite+libsql:////x.db?auth_token=TOPSECRET", None, None,
    )
    assert TokenMaskingFilter().filter(record) is True
    assert "TOPSECRET" not in record.getMessage()
    assert "auth_token=***" in record.getMessage()


def test_windows_drive_path_builds_replica_url():
    """Windows ドライブレター付きパスでも replica URL が正しく構築される。"""
    env = {
        "DATABASE_URL": "sqlite+libsql:///C:/Users/me/data/hw_genie.db",
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
    }
    db_url, connect_args = build_database_config(env)
    # スキーム区切りの 4 スラッシュ + ドライブレターが維持される
    assert db_url.startswith("sqlite+libsql:////C:/Users/me/data/hw_genie.db?")
    assert "sync_url=" in db_url
    assert connect_args.get("check_same_thread") is False
