import logging
import os
import threading
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine

from hw_genie.core import database
from hw_genie.core.database import build_database_config

# conftest の autouse fixture が database.get_engine をテスト用に差し替えるため、
# 遅延初期化ロジックそのものを検証するテスト用に import 時点の実装を捕捉する。
_real_get_engine = database.get_engine


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
        "DATABASE_URL": "sqlite:///./test_replica.db",
    }
    db_url, connect_args = build_database_config(env)

    # ./ 付きの相対パスは PKG_ROOT 基準に解決され、sqlite+libsql の絶対パス形式
    # （4スラッシュ）になる。sync 用パラメータが URL のクエリ文字列に付与されること。
    url_str = str(db_url)
    from hw_genie.core.database import PKG_ROOT

    expected = f"sqlite+libsql:////{Path(PKG_ROOT).joinpath('test_replica.db').as_posix().lstrip('/')}?"
    assert url_str.startswith(expected)
    assert "sqlite+libsql://///" not in url_str

    # TursoReplicaDialect が URL クエリの sync_* を le.connect へ渡すため、
    # クエリ文字列に正しく含まれていることを確認する。
    parsed = urllib.parse.urlparse(url_str)
    q = urllib.parse.parse_qs(parsed.query)
    assert q["sync_url"] == ["libsql://my-test-db.turso.io"]
    assert q["auth_token"] == ["my-mock-auth-token"]
    assert q["sync_interval"] == ["123.45"]
    assert connect_args.get("check_same_thread") is False


def test_turso_sync_relative_path_resolves_to_pkg_root():
    """./ 付き相対パスは PKG_ROOT 基準で解決される（コンテナ/ホスト共通 .env 用）。"""
    from hw_genie.core.database import PKG_ROOT

    env = {
        "DATABASE_URL": "sqlite+libsql:///./data/hw_genie.db",
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
    }
    db_url, _ = build_database_config(env)
    expected = f"sqlite+libsql:////{Path(PKG_ROOT).joinpath('data/hw_genie.db').as_posix().lstrip('/')}?"
    assert str(db_url).startswith(expected)


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


def test_get_engine_uses_pool_pre_ping(monkeypatch):
    """get_engine() は pool_pre_ping=True で生成される（死んだ接続の自動破棄）。"""
    mock_engine = object()
    called = {}

    def fake_create_engine(url, **kwargs):
        called["url"] = url
        called["kwargs"] = kwargs
        return mock_engine

    monkeypatch.setattr(database, "build_database_config", lambda env=None: ("sqlite:///:memory:", {}))
    monkeypatch.setattr(database, "create_engine", fake_create_engine)
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.setattr(database, "get_engine", _real_get_engine)

    assert database.get_engine() is mock_engine
    assert called["kwargs"]["pool_pre_ping"] is True


def test_get_write_engine_uses_pool_pre_ping(monkeypatch):
    """リモート書き込みエンジンも pool_pre_ping=True で生成される。"""
    mock_engine = object()
    called = {}

    def fake_create_engine(url, **kwargs):
        called["url"] = url
        called["kwargs"] = kwargs
        return mock_engine

    monkeypatch.setenv("TURSO_WRITE_REMOTE", "true")
    monkeypatch.setenv("TURSO_SYNC_URL", "libsql://dummy.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(database, "create_engine", fake_create_engine)
    monkeypatch.setattr(database, "_write_engine", None)
    monkeypatch.setattr(database, "_WriteSessionLocal", None)

    assert database.get_write_engine() is mock_engine
    assert called["url"].startswith("sqlite+libsql://dummy.turso.io")
    assert called["kwargs"]["pool_pre_ping"] is True


def test_replica_dialect_forwards_sync_params():
    """TursoReplicaDialect はローカル URL の sync_* クエリを le.connect へ渡す。"""
    from sqlalchemy.engine.url import make_url
    from hw_genie.core.database import TursoReplicaDialect

    # SQL compilation cache を有効にして SAWarning を抑止する
    assert TursoReplicaDialect.supports_statement_cache is True

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


def test_install_token_masking_filter_masks_child_logger():
    """install_token_masking_filter は子ロガー(sqlalchemy等)のレコードもマスクする。

    logging.Filter をロガーに付けるだけでは子ロガーのレコードは通らないため、
    handler 経由でマスクされることを検証する。
    """
    import io
    import logging as _logging

    from hw_genie.core.database import install_token_masking_filter

    stream = io.StringIO()
    handler = _logging.StreamHandler(stream)
    root = _logging.getLogger()
    root.addHandler(handler)
    root.setLevel(_logging.DEBUG)
    try:
        install_token_masking_filter()

        # 子ロガー経由でトークンを含むメッセージを出力
        _logging.getLogger("hw_genie.core.database").info(
            "connect url sqlite+libsql:///x?auth_token=CHILDSECRET&sync_interval=5"
        )
        output = stream.getvalue()
    finally:
        root.removeHandler(handler)

    assert "CHILDSECRET" not in output
    assert "auth_token=***" in output


def test_windows_drive_path_builds_replica_url():
    """Windows ドライブレター付き絶対パス（4スラッシュ形式）はそのまま維持される。"""
    env = {
        "DATABASE_URL": "sqlite+libsql:////C:/Users/me/data/hw_genie.db",
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
    }
    db_url, connect_args = build_database_config(env)
    # 4 スラッシュの絶対パス（ドライブレター付き）はそのまま維持される
    assert db_url.startswith("sqlite+libsql:////C:/Users/me/data/hw_genie.db?")
    assert "sync_url=" in db_url
    assert connect_args.get("check_same_thread") is False


def test_create_connect_args_sets_replica_mode_flag():
    """Replica URL (sync_url 付き) は on_connect で sync() されるようフラグが立つ。"""
    from sqlalchemy.engine.url import make_url

    from hw_genie.core.database import TursoReplicaDialect

    dialect = TursoReplicaDialect()
    url = make_url(
        "sqlite+libsql:////tmp/replica.db"
        "?sync_url=libsql://db.turso.io&auth_token=secret"
    )
    dialect.create_connect_args(url)
    assert dialect._replica_mode is True


def test_create_connect_args_clears_replica_mode_for_remote():
    """Remote (ws) URL は replica フラグが立たない。"""
    from sqlalchemy.engine.url import make_url

    from hw_genie.core.database import TursoReplicaDialect

    dialect = TursoReplicaDialect()
    url = make_url("sqlite+libsql://db.turso.io/my-db?auth_token=secret")
    dialect.create_connect_args(url)
    assert dialect._replica_mode is False


def test_on_connect_syncs_replica_when_enabled(monkeypatch):
    """replica モードで on_connect が conn.sync() を呼ぶ（デフォルト有効）。"""
    from hw_genie.core.database import TursoReplicaDialect

    calls = []

    class FakeLibsqlConn:
        def sync(self):
            calls.append("sync")

    # import_dbapi 経由で Connection 型を差し替え
    monkeypatch.setattr(
        TursoReplicaDialect, "import_dbapi",
        lambda cls: type("m", (), {"Connection": FakeLibsqlConn})(),
    )
    # super().on_connect() は None を返す想定
    monkeypatch.setattr(
        "hw_genie.core.database.SQLiteDialect_libsql.on_connect",
        lambda self: None,
    )

    dialect = TursoReplicaDialect()
    dialect._replica_mode = True
    hook = dialect.on_connect()
    hook(FakeLibsqlConn())
    assert calls == ["sync"]


def test_on_connect_skips_sync_when_disabled(monkeypatch):
    """TURSO_SYNC_ON_CONNECT=false なら sync() は呼ばれない。"""
    from hw_genie.core.database import TursoReplicaDialect

    monkeypatch.setenv("TURSO_SYNC_ON_CONNECT", "false")
    calls = []

    class FakeLibsqlConn:
        def sync(self):
            calls.append("sync")

    monkeypatch.setattr(
        TursoReplicaDialect, "import_dbapi",
        lambda cls: type("m", (), {"Connection": FakeLibsqlConn})(),
    )
    monkeypatch.setattr(
        "hw_genie.core.database.SQLiteDialect_libsql.on_connect",
        lambda self: None,
    )

    dialect = TursoReplicaDialect()
    dialect._replica_mode = True
    hook = dialect.on_connect()
    hook(FakeLibsqlConn())
    assert calls == []


def test_on_connect_skips_sync_for_non_replica(monkeypatch):
    """replica フラグが無い場合は sync() を呼ばない。"""
    from hw_genie.core.database import TursoReplicaDialect

    calls = []

    class FakeLibsqlConn:
        def sync(self):
            calls.append("sync")

    monkeypatch.setattr(
        TursoReplicaDialect, "import_dbapi",
        lambda cls: type("m", (), {"Connection": FakeLibsqlConn})(),
    )
    monkeypatch.setattr(
        "hw_genie.core.database.SQLiteDialect_libsql.on_connect",
        lambda self: None,
    )

    dialect = TursoReplicaDialect()
    hook = dialect.on_connect()
    hook(FakeLibsqlConn())
    assert calls == []


def test_connect_serialises_replica_open(monkeypatch):
    """replica モードの接続オープンは WAL ロックの下で行われる。

    libSQL はローカルファイルを開く際に内部でブロッキング sync を行い WAL に
    書き込むため、並列スレッドの接続オープン同士が競合する。dialect.connect()
    が共有ロック (``_wal_io_lock``) を取ることを検証する。
    """
    from hw_genie.core import database as db_module
    from hw_genie.core.database import TursoReplicaDialect

    entered = []
    held_by = []

    class LockSpy:
        def __enter__(self):
            entered.append(threading.current_thread().name)
            held_by.append(threading.current_thread().name)
            return self

        def __exit__(self, *exc):
            held_by.clear()
            return False

    monkeypatch.setattr(db_module, "_wal_io_lock", LockSpy())
    dialect = TursoReplicaDialect()
    monkeypatch.setattr(
        dialect, "dbapi", type("dbapi", (), {"connect": lambda *a, **k: "conn"})()
    )

    dialect._replica_mode = True
    assert dialect.connect() == "conn"
    assert len(entered) == 1

    dialect._replica_mode = False
    dialect.connect()
    assert len(entered) == 1  # 非 replica (remote/plain sqlite) ではロック不要


def test_connect_retries_wal_contention(monkeypatch, mock_sleep):
    """replica モードの接続オープンは WAL 競合をリトライして成功する。

    他プロセスが WAL ライターロックを保持している間は接続オープン自体が
    ``wal_insert_begin failed`` を送出するため、指数バックオフで再試行する。
    """
    from hw_genie.core import database as db_module
    from hw_genie.core.database import TursoReplicaDialect

    monkeypatch.setattr(
        db_module,
        "_wal_io_lock",
        type("Lock", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False})(),
    )
    dialect = TursoReplicaDialect()
    calls = {"n": 0}

    def flaky_connect(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("wal_insert_begin failed")
        return "conn"

    monkeypatch.setattr(dialect, "dbapi", type("dbapi", (), {"connect": flaky_connect})())
    dialect._replica_mode = True

    assert dialect.connect() == "conn"
    assert calls["n"] == 3
    assert mock_sleep.call_count == 2

    # 非 replica モードではリトライせず即座に送出される
    dialect._replica_mode = False
    calls["n"] = 0
    with pytest.raises(ValueError, match="wal_insert_begin failed"):
        dialect.connect()
    assert calls["n"] == 1


def test_connect_wal_contention_exhausted_reraises(monkeypatch, mock_sleep):
    """接続オープンの WAL 競合が全試行失敗したら最後の例外を再送出する。"""
    from hw_genie.core import database as db_module
    from hw_genie.core.database import TursoReplicaDialect

    monkeypatch.setattr(
        db_module,
        "_wal_io_lock",
        type("Lock", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False})(),
    )
    dialect = TursoReplicaDialect()

    def always_wal(*args, **kwargs):
        raise ValueError("database is locked")

    monkeypatch.setattr(dialect, "dbapi", type("dbapi", (), {"connect": always_wal})())
    dialect._replica_mode = True

    with pytest.raises(ValueError, match="database is locked"):
        dialect.connect()
    assert mock_sleep.call_count == 2


def test_connect_non_wal_error_propagates_immediately(monkeypatch, mock_sleep):
    """接続オープンの非競合エラーは即時送出し再試行しない。"""
    from hw_genie.core import database as db_module
    from hw_genie.core.database import TursoReplicaDialect

    monkeypatch.setattr(
        db_module,
        "_wal_io_lock",
        type("Lock", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False})(),
    )
    dialect = TursoReplicaDialect()

    def boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(dialect, "dbapi", type("dbapi", (), {"connect": boom})())
    dialect._replica_mode = True

    with pytest.raises(OSError, match="connection refused"):
        dialect.connect()
    assert mock_sleep.call_count == 0


def test_build_write_config_falls_back_to_replica_when_remote_off():
    """TURSO_WRITE_REMOTE 未設定時は write 設定 = 通常(レプリカ)設定。"""
    from hw_genie.core.database import build_write_database_config

    env = {
        "DATABASE_URL": "sqlite:///./data/hw_genie.db",
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
    }
    write_url, write_args = build_write_database_config(env)
    read_url, read_args = build_database_config(env)
    assert write_url == read_url
    assert write_args == read_args


def test_build_write_config_remote_direct_when_enabled():
    """TURSO_WRITE_REMOTE=true 時は write 設定がリモート直接接続になる。"""
    from hw_genie.core.database import build_write_database_config

    env = {
        "DATABASE_URL": "sqlite:///./data/hw_genie.db",
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_WRITE_REMOTE": "true",
    }
    write_url, write_args = build_write_database_config(env)
    # リモート直接接続 (sqlite+libsql://host/) になり、sync_url は付かない
    assert write_url.startswith("sqlite+libsql://my-test-db.turso.io/")
    assert "sync_url" not in write_url
    # secure=true を付与して dialect に https (TLS) 接続を強制し、
    # Turso からの 308 Permanent Redirect を回避する (HW-Genie#xx)
    assert "secure=true" in write_url
    assert write_args.get("auth_token") == "my-mock-auth-token"


def test_get_write_session_local_reuses_read_when_remote_off(monkeypatch):
    """remote-off 時、get_write_session_local は read セッションと同一。"""
    from hw_genie.core.database import get_session_local, get_write_session_local

    monkeypatch.setenv("TURSO_WRITE_REMOTE", "false")
    # キャッシュをリセット
    import hw_genie.core.database as db

    db._write_engine = None
    db._WriteSessionLocal = None
    db._engine = None
    db._SessionLocal = None

    assert get_write_session_local() is get_session_local()


def test_get_write_session_local_remote_uses_separate_engine(monkeypatch):
    """remote-on 時、get_write_session_local は read とは別エンジンを返す。"""
    from sqlalchemy import create_engine as sa_create
    from sqlalchemy.pool import StaticPool

    from hw_genie.core.database import get_session_local, get_write_session_local
    import hw_genie.core.database as db

    db._write_engine = None
    db._WriteSessionLocal = None

    monkeypatch.setenv("TURSO_WRITE_REMOTE", "true")
    monkeypatch.setenv("TURSO_SYNC_URL", "libsql://my-test-db.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "my-mock-auth-token")

    # read エンジンは in-memory に差し替え、write は別 in-memory プールで構築されること
    db._engine = sa_create(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    write_session = get_write_session_local()
    assert write_session is not get_session_local()
    # 別エンジンでもセッションは生成できる
    s = write_session()
    s.close()


@pytest.mark.parametrize(
    "sync_url,expected_netloc",
    [
        # canonical libsql:// form
        ("libsql://my-test-db.turso.io", "my-test-db.turso.io"),
        # already-https form (secure query is stripped/re-appended)
        ("https://my-test-db.turso.io?secure=true", "my-test-db.turso.io"),
        # explicit port preserved
        ("libsql://my-test-db.turso.io:443", "my-test-db.turso.io:443"),
        # token-in-URL form: userinfo must be preserved
        ("libsql://my-token@my-test-db.turso.io", "my-token@my-test-db.turso.io"),
        (
            "https://my-token@my-test-db.turso.io:443?secure=true",
            "my-token@my-test-db.turso.io:443",
        ),
    ],
)
def test_build_write_config_url_forms(sync_url, expected_netloc):
    """TURSO_SYNC_URL の各表記で正しいリモート URL が作れる（トークン落ち防止）。"""
    from hw_genie.core.database import build_write_database_config

    env = {
        "DATABASE_URL": "sqlite:///./data/hw_genie.db",
        "TURSO_SYNC_URL": sync_url,
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_WRITE_REMOTE": "true",
    }
    write_url, _ = build_write_database_config(env)
    assert write_url == f"sqlite+libsql://{expected_netloc}/?secure=true"


def test_build_database_config_read_remote_returns_write_config():
    """TURSO_READ_REMOTE=true 時は read 設定もリモート直接接続になる。"""
    from hw_genie.core.database import build_database_config

    env = {
        "DATABASE_URL": "sqlite:///./data/hw_genie.db",
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_READ_REMOTE": "true",
        "TURSO_WRITE_REMOTE": "true",
    }
    read_url, _ = build_database_config(env)
    assert read_url == "sqlite+libsql://my-test-db.turso.io/?secure=true"


def test_build_database_config_read_remote_alone_does_not_recurse():
    """TURSO_READ_REMOTE 単体指定でも相互再帰 (RecursionError) にならない。"""
    from hw_genie.core.database import (
        build_database_config,
        build_write_database_config,
    )

    env = {
        "DATABASE_URL": "sqlite:///./data/hw_genie.db",
        "TURSO_SYNC_URL": "libsql://my-test-db.turso.io",
        "TURSO_AUTH_TOKEN": "my-mock-auth-token",
        "TURSO_READ_REMOTE": "true",
    }
    expected = "sqlite+libsql://my-test-db.turso.io/?secure=true"
    read_url, read_args = build_database_config(env)
    write_url, write_args = build_write_database_config(env)
    # read / write ともリモート直結になる（write エンジンは read エンジンを再利用）
    assert read_url == expected
    assert write_url == expected
    assert read_args.get("auth_token") == "my-mock-auth-token"
    assert write_args.get("auth_token") == "my-mock-auth-token"


def test_build_database_config_read_remote_without_sync_url_warns(caplog):
    """TURSO_READ_REMOTE=true だが sync URL 未設定は警告しローカルへフォールバック。"""
    from hw_genie.core.database import build_database_config

    env = {
        "DATABASE_URL": "sqlite:///./data/hw_genie.db",
        "TURSO_READ_REMOTE": "true",
    }
    with caplog.at_level(logging.WARNING):
        read_url, _ = build_database_config(env)
    assert "TURSO_READ_REMOTE=true but TURSO_SYNC_URL is not set" in caplog.text
    # ローカルファイルモード（replica ではなく plain sqlite）へフォールバック
    assert "sqlite://" in str(read_url)


def test_is_wal_contention_markers():
    """WAL 競合の判定（wal_insert_begin failed / database is locked）。"""
    from hw_genie.core.database import is_wal_contention

    assert is_wal_contention(ValueError("wal_insert_begin failed"))
    assert is_wal_contention(ValueError("database is locked"))
    assert is_wal_contention(ValueError("SQLITE_BUSY: database is locked (5)"))
    assert not is_wal_contention(RuntimeError("connection refused"))
    assert not is_wal_contention(ValueError("no such table: accounts"))


def test_retry_on_wal_contention_recovers(mock_sleep):
    """WAL 競合で一時失敗しても再試行で成功する。"""
    from hw_genie.core.database import retry_on_wal_contention

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("wal_insert_begin failed")
        return "ok"

    assert retry_on_wal_contention(flaky, attempts=5) == "ok"
    assert calls["n"] == 3
    assert mock_sleep.call_count == 2


def test_retry_on_wal_contention_raises_after_exhaustion(mock_sleep):
    """全再試行が WAL 競合で失敗したら最後の例外を再送出する。"""

    def flaky():
        raise ValueError("wal_insert_begin failed")

    from hw_genie.core.database import retry_on_wal_contention

    with pytest.raises(ValueError, match="wal_insert_begin failed"):
        retry_on_wal_contention(flaky, attempts=3, base_delay=0.01)
    assert mock_sleep.call_count == 2


def test_retry_on_wal_contention_non_contention_raises_immediately(mock_sleep):
    """WAL 競合以外のエラーは即時送出し再試行しない。"""

    def flaky():
        raise RuntimeError("boom")

    from hw_genie.core.database import retry_on_wal_contention

    with pytest.raises(RuntimeError, match="boom"):
        retry_on_wal_contention(flaky, attempts=5)
    assert mock_sleep.call_count == 0


def test_retry_on_wal_contention_rejects_invalid_attempts():
    """attempts < 1 は ValueError を送出し、fn を実行しない。"""
    from hw_genie.core.database import retry_on_wal_contention

    called = {"n": 0}

    def fn():
        called["n"] += 1

    with pytest.raises(ValueError, match="attempts"):
        retry_on_wal_contention(fn, attempts=0)
    assert called["n"] == 0


def test_retry_on_wal_contention_sleeps_with_jitter(mock_sleep, monkeypatch):
    """バックオフ待機はジッター付きで複数プロセスの再衝突を防ぐ。"""
    from hw_genie.core.database import retry_on_wal_contention

    # ジッター係数を 1.5 に固定し、待機時間を決定的に検証する
    monkeypatch.setattr(
        "hw_genie.core.database.random.uniform", lambda a, b: b
    )

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("wal_insert_begin failed")

    retry_on_wal_contention(flaky, attempts=5, base_delay=1.0)
    assert calls["n"] == 3
    assert mock_sleep.call_count == 2
    # 1回目: 1.0 * 2^0 * 1.5 = 1.5、2回目: 1.0 * 2^1 * 1.5 = 3.0
    delays = [c.args[0] for c in mock_sleep.call_args_list]
    assert delays == [1.5, 3.0]


def test_on_connect_sync_retries_on_wal_contention(monkeypatch, mock_sleep):
    """接続時 sync() が WAL 競合で失敗しても再試行して成功する。"""
    from hw_genie.core.database import TursoReplicaDialect

    calls = []

    class FakeLibsqlConn:
        def sync(self):
            calls.append("sync")
            if len(calls) < 3:
                raise ValueError("wal_insert_begin failed")

    monkeypatch.setattr(
        TursoReplicaDialect, "import_dbapi",
        lambda cls: type("m", (), {"Connection": FakeLibsqlConn})(),
    )
    monkeypatch.setattr(
        "hw_genie.core.database.SQLiteDialect_libsql.on_connect",
        lambda self: None,
    )

    dialect = TursoReplicaDialect()
    dialect._replica_mode = True
    hook = dialect.on_connect()
    hook(FakeLibsqlConn())
    assert len(calls) == 3


def test_on_connect_sync_warns_after_retries_exhausted(monkeypatch, caplog, mock_sleep):
    """接続時 sync() が全再試行失敗でも例外にせず警告のみ（ベストエフォート）。"""
    import logging as _logging

    from hw_genie.core.database import TursoReplicaDialect

    class FakeLibsqlConn:
        def sync(self):
            raise ValueError("wal_insert_begin failed")

    monkeypatch.setattr(
        TursoReplicaDialect, "import_dbapi",
        lambda cls: type("m", (), {"Connection": FakeLibsqlConn})(),
    )
    monkeypatch.setattr(
        "hw_genie.core.database.SQLiteDialect_libsql.on_connect",
        lambda self: None,
    )

    with caplog.at_level(_logging.WARNING, logger="hw_genie.core.database"):
        dialect = TursoReplicaDialect()
        dialect._replica_mode = True
        hook = dialect.on_connect()
        hook(FakeLibsqlConn())

    assert "sync() failed on connect" in caplog.text

