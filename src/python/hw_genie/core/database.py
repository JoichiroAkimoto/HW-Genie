import logging
import os
import re
import threading
from pathlib import Path
from typing import NotRequired, TypedDict
import urllib.parse
from sqlalchemy import create_engine
from sqlalchemy import util
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, JSON, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.dialects import registry
from sqlalchemy_libsql.libsql import SQLiteDialect_libsql

Base = declarative_base()

# Per-thread flag recording whether the most recent connect_args built a Turso
# embedded replica (i.e. opened a local file WITH a sync_url). ``on_connect``
# consults this to decide whether to force an explicit ``sync()``. A
# threading.local() is used because the dialect instance is shared across
# threads but each connection is built on a single thread.
_replica_mode = threading.local()


def _mark_replica_mode(enabled: bool) -> None:
    _replica_mode.active = enabled


def _is_replica_mode() -> bool:
    return bool(getattr(_replica_mode, "active", False))


class TursoReplicaDialect(SQLiteDialect_libsql):
    """libSQL dialect with Embedded Replica (Syncs) support for local files.

    ``sqlalchemy-libsql`` 0.2.0's stock dialect drops ``sync_url`` /
    ``auth_token`` / ``sync_interval`` when the URL points at a local file
    (it only forwards them in remote/ws mode), so the replica never syncs and
    opens in plain "File mode". This subclass re-reads those parameters from the
    URL query string and passes them straight to ``libsql_experimental.connect``,
    which *does* support embedded replicas.
    """

    # Enable SQLAlchemy's SQL compilation cache for this dialect (libSQL is
    # safe to cache statements). Silences the "will not make use of SQL
    # compilation caching" SAWarning emitted on every connection.
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls):
        import libsql_experimental as libsql

        return libsql

    def on_connect(self):
        """Force a blocking ``sync()`` on every replica connection.

        libSQL's background sync (``sync_interval``) only runs while the
        process is alive, so a short-lived CLI command (e.g. ``auth --list``)
        may query its local replica *before* any background pull completes,
        returning stale data even though the remote was updated from another
        client. Forcing an explicit ``sync()`` on connect guarantees the
        replica is up to date before any statement runs.

        This can be disabled by setting ``TURSO_SYNC_ON_CONNECT=false`` (the
        always-on container auth-server relies on ``sync_interval`` instead).
        """
        super_on_connect = super().on_connect()
        libsql = self.import_dbapi()

        def connect(conn):
            if super_on_connect is not None:
                super_on_connect(conn)
            # Only sync a real Turso replica connection that was opened with a
            # sync_url (tracked via the per-thread flag set in
            # create_connect_args). Remote (ws) connections and plain local
            # sqlite connections are skipped.
            if isinstance(conn, libsql.Connection) and _is_replica_mode():
                if os.environ.get("TURSO_SYNC_ON_CONNECT", "true").lower() != "false":
                    try:
                        conn.sync()
                    except Exception:  # pragma: no cover - best-effort
                        logger.warning(
                            "Turso replica sync() failed on connect; "
                            "continuing with locally cached data.",
                            exc_info=True,
                        )

        return connect

    def create_connect_args(self, url):
        pysqlite_args = (
            ("uri", bool),
            ("timeout", float),
            ("isolation_level", str),
            ("detect_types", int),
            ("check_same_thread", bool),
            ("cached_statements", int),
            ("secure", bool),
        )
        opts = dict(url.query)
        connect_args: dict = {}
        for key, type_ in pysqlite_args:
            util.coerce_kw_type(opts, key, type_, dest=connect_args)

        # Reset replica flag; it is only set below when a sync_url is attached.
        _mark_replica_mode(False)

        if url.host:
            # Remote (ws/wss) mode: keep stock behaviour.
            connect_args["uri"] = True
            filtered = {
                k: v for k, v in opts.items() if k not in dict(pysqlite_args)
            }
            query_str = urllib.parse.urlencode(sorted(filtered.items()))
            secure = connect_args.pop("secure", False)
            scheme = "https" if secure else "http"
            netloc = url.host
            if url.port:
                netloc += f":{url.port}"
            connect_url = urllib.parse.urlunsplit(
                (scheme, netloc, url.database or "", query_str, "")
            )
            return ([connect_url], connect_args)

        # Local file: open as an embedded replica and forward sync params.
        database = url.database or ":memory:"
        if database != ":memory:":
            database = os.path.abspath(database)
        connect_url = database

        sync_url = opts.get("sync_url")
        if sync_url:
            connect_args["sync_url"] = sync_url
            _mark_replica_mode(True)
            auth_token = opts.get("auth_token")
            if auth_token:
                connect_args["auth_token"] = auth_token
            sync_interval = opts.get("sync_interval")
            if sync_interval:
                try:
                    connect_args["sync_interval"] = float(sync_interval)
                except (ValueError, TypeError):
                    logger.warning(
                        "Invalid sync_interval=%r ignored; using libSQL default.",
                        sync_interval,
                    )

        connect_args.setdefault("check_same_thread", False)
        return ([connect_url], connect_args)


# Override the stock ``sqlite.libsql`` dialect so that ANY ``sqlite+libsql://``
# URL (including the one built by build_database_config) gains replica support.
registry.register("sqlite.libsql", __name__, "TursoReplicaDialect")


# Matches an auth_token (or authToken) query parameter anywhere in a logged
# string (e.g. a SQLAlchemy connection URL) so it can be masked. Tokens are
# long base64url/JWT-like strings with no whitespace or '&'.
_TOKEN_RE = re.compile(r"(auth_token=|authToken=)\S+", re.IGNORECASE)


def mask_sensitive(value: str) -> str:
    """Mask auth_token / authToken values in a string (e.g. a DB URL)."""
    if not value:
        return value
    return _TOKEN_RE.sub(r"\1***", value)


class TokenMaskingFilter(logging.Filter):
    """Redacts auth_token / authToken query params from any log record.

    The Turso auth token is embedded in the SQLAlchemy connection URL (query
    string). SQLAlchemy may log the URL on connect errors / debug, so this
    filter masks the token before it reaches the handler output.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "auth" in msg.lower() and _TOKEN_RE.search(msg):
            record.msg = mask_sensitive(str(record.msg))
            record.args = None
        return True


def install_token_masking_filter(logger: logging.Logger | None = None) -> None:
    """Attach :class:`TokenMaskingFilter` to the root logger's handlers.

    A ``logging.Filter`` on a logger only filters records emitted by that
    logger itself, not by child loggers (e.g. ``sqlalchemy``, ``hw_genie``).
    Tokens appear in SQLAlchemy/URL logs, so the filter must be attached to
    the *handlers* (which every record passes through) rather than the logger.
    Idempotent.
    """
    root = logging.getLogger() if logger is None else logger
    # Ensure there is at least one handler to attach to.
    if not root.handlers:
        logging.basicConfig()
    for handler in root.handlers:
        if any(isinstance(f, TokenMaskingFilter) for f in handler.filters):
            continue
        handler.addFilter(TokenMaskingFilter())


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(String, unique=True, nullable=False)
    alias = Column(String)
    player_name = Column(String)
    level = Column(Integer, default=0)
    gold = Column(Integer, default=0)
    gems = Column(Integer, default=0)
    energy = Column(Integer, default=0)
    arena_rank = Column(Integer, default=0)
    grand_rank = Column(Integer, default=0)
    last_mission_id = Column(Integer, default=None)
    memo = Column(String, nullable=True)
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def update_from_dict(self, player_data: dict):
        """
        Updates account status fields from a dictionary, with type conversion.
        
        Args:
            player_data (dict): Dictionary containing player info (name, level, gold, etc.)
        """
        if "name" in player_data:
            self.player_name = player_data["name"]
        if "memo" in player_data:
            self.memo = player_data["memo"]
        
        fields = {
            "level": "level",
            "gold": "gold",
            "gems": "gems",
            "energy": "energy",
            "arena_rank": "arena_rank",
            "grand_rank": "grand_rank"
        }
        for p_key, attr in fields.items():
            if p_key in player_data:
                try:
                    setattr(self, attr, int(player_data[p_key]))
                except (ValueError, TypeError):
                    pass


class AccountConfig(Base):
    __tablename__ = "account_configs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    config_key = Column(String, nullable=False)
    config_value = Column(JSON)
    __table_args__ = (UniqueConstraint("account_id", "config_key", name="_account_config_uc"),)
    # Using a unique constraint on (account_id, config_key) to ensure Key-Value uniqueness per account


# プロジェクトルートの絶対パスを基点に DB パスを確定させる。
# 開発環境では src/python/hw_genie/core/database.py、コンテナでは
# /app/hw_genie/core/database.py のようにネスト深さが異なるため、固定の
# ".." 数ではなく「hw_genie パッケージを直接含むディレクトリ」を探索する。
def _find_pkg_root(start: str) -> str:
    current = os.path.dirname(os.path.abspath(start))
    # プロジェクトルートを特定する。
    # 1) .git がある階層（開発環境のリポジトリルート）を優先。
    # 2) .git が無い環境（コンテナ等）は、hw_genie パッケージを直接含む階層
    #    (/app 等) をルートとする。
    # 開発環境(src/python/hw_genie/...)でもコンテナ(/app/hw_genie/...)でも
    # data/ や .env の位置が正しく解決される。
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            # ルートまで到達しても .git が無い場合は、hw_genie パッケージの
            # 親ディレクトリ（/app など）を返す。
            # start = .../hw_genie/core/database.py -> 3 階層上がパッケージの親。
            pkg_parent = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(start)))
            )
            return pkg_parent
        current = parent


PKG_ROOT = _find_pkg_root(__file__)

# .env ファイルが存在する場合は環境変数にロードする
env_path = os.path.join(PKG_ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

DEFAULT_DB_PATH = os.path.join(PKG_ROOT, "data", "hw_genie.db")
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH}"

logger = logging.getLogger(__name__)


class DatabaseConfig(TypedDict):
    """SQLAlchemy connect_args produced by ``build_database_config``.

    Turso Syncs-specific keys are optional and only present when
    ``TURSO_SYNC_URL`` is configured.
    """

    sync_url: NotRequired[str]
    auth_token: NotRequired[str]
    sync_interval: NotRequired[float]
    check_same_thread: bool
    timeout: NotRequired[int]


def build_database_config(env: dict[str, str] | None = None) -> tuple[str, DatabaseConfig]:
    """
    Build the database connection URL and SQLAlchemy connect_args.

    Reads the relevant environment variables (or ``os.environ`` when ``env`` is
    omitted) and resolves the final ``db_url`` / ``connect_args`` pair. This is a
    pure function: it has no module-level side effects and is safe to call from
    tests with a mocked environment.

    Turso Embedded Replicas (Syncs) are enabled by setting ``TURSO_SYNC_URL``.
    In that mode the local SQLite file (from ``DATABASE_URL``) becomes a replica
    that is synchronised with the remote database.

    Returns:
        tuple: ``(db_url, connect_args)``.
    """
    env = os.environ if env is None else env

    # 環境変数 DATABASE_URL で接続先を切り替え可能に
    db_url = env.get("DATABASE_URL", DEFAULT_DB_URL)

    # 接続時の引数
    connect_args: DatabaseConfig = {}

    turso_sync_url = env.get("TURSO_SYNC_URL")
    turso_auth_token = env.get("TURSO_AUTH_TOKEN")
    turso_sync_interval = env.get("TURSO_SYNC_INTERVAL")

    # A remote libSQL URL (e.g. sqlite+libsql://host/db) cannot be turned into a
    # local embedded replica, so TURSO_SYNC_URL is ignored in that case and the
    # remote connection is used as-is.
    # A relative local URL like ``sqlite://foo.db`` parses with hostname="foo.db"
    # but an empty path; a real remote libSQL URL always carries a non-empty db
    # path, so require both hostname AND path to avoid misclassifying relatives.
    _parsed = urllib.parse.urlparse(db_url)
    is_remote_libsql = bool(
        turso_sync_url
        and ("libsql" in db_url)
        and _parsed.hostname
        and _parsed.path
    )
    del _parsed

    if env.get("TURSO_READ_REMOTE", "false").lower() == "true":
        if not turso_sync_url:
            # 設定漏れ: read remote を希望しているのに sync URL が無い。
            # サイレントにローカル replica へフォールバックせず警告する。
            logger.warning(
                "TURSO_READ_REMOTE=true but TURSO_SYNC_URL is not set; "
                "falling back to local file mode."
            )
        else:
            # Read directly from the remote Turso primary instead of a local
            # embedded replica. This avoids wal_insert_begin failed contention
            # that occurs when multiple parallel hw-genie processes each open the
            # SAME local replica file and race to write its WAL during sync().
            # Reads stay fresh because they hit the primary directly. Pair with
            # TURSO_WRITE_REMOTE=true for a fully remote (no local file) setup.
            return build_write_database_config(env)

    if turso_sync_url and not is_remote_libsql:
        # Determine the local database file path to act as the replica.
        # Use pathlib for OS-agnostic, robust path normalisation.
        if db_url.startswith(("sqlite+libsql:///", "sqlite:///")):
            parsed = urllib.parse.urlparse(db_url)
            # urlparse keeps the URL's leading "/" (the authority delimiter of
            # the sqlite:/// scheme) in the path. A leading "./" marks a path
            # *relative to the project root* (PKG_ROOT): this lets the same
            # .env work both inside the Docker container (PKG_ROOT=/app ->
            # /app/data) and on a host machine (PKG_ROOT=<repo root> ->
            # <repo>/data). Any other (absolute) path is used verbatim; an
            # explicit absolute path may also use the 4-slash form
            # (sqlite+libsql:////abs/path).
            path_str = parsed.path or "."
            if path_str.startswith("/."):
                local_path = Path(PKG_ROOT) / path_str[2:].lstrip("/")
            else:
                local_path = Path(path_str)
        elif "libsql" in db_url or "sqlite" in db_url:
            # Triple-slash でないローカル/相対指定 (例: sqlite+libsql://my.db,
            # sqlite://my.db) の場合はユーザー指定のパスを尊重し、DEFAULT で
            # 上書きしてデータ場所が意図せず変わるのを防ぐ。
            parsed = urllib.parse.urlparse(db_url)
            local_path = Path(parsed.path or DEFAULT_DB_PATH)
        else:
            local_path = Path(DEFAULT_DB_PATH)

        # Normalise to an absolute path. Use absolute() (not resolve()) so that
        # symlink-based paths like /tmp stay as the user specified, while still
        # resolving relative-to-cwd paths. Handles Windows drive paths too.
        local_path = local_path.absolute()

        # Build a libSQL Embedded Replica URL pointing at the LOCAL file.
        # The TursoReplicaDialect (registered for ``sqlite.libsql``) reads the
        # sync_url/auth_token/sync_interval query parameters and forwards them to
        # libsql_experimental.connect so the local file is opened as a synced
        # replica rather than a plain "File mode" database.
        # Normalise to exactly one leading slash so absolute paths render as
        # "sqlite+libsql:////abs/path" (4 slashes) and relative paths as
        # "sqlite+libsql:///rel/path" (3 slashes).
        local_uri = "/" + local_path.as_posix().lstrip("/")
        query = {"sync_url": turso_sync_url}
        if turso_auth_token:
            query["auth_token"] = turso_auth_token
        if turso_sync_interval:
            try:
                query["sync_interval"] = str(float(turso_sync_interval))
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid TURSO_SYNC_INTERVAL=%r ignored; falling back to the "
                    "default sync interval.",
                    turso_sync_interval,
                )
        db_url = (
            f"sqlite+libsql:///{local_uri}?"
            f"{urllib.parse.urlencode(query)}"
        )
        connect_args["check_same_thread"] = False

    elif "libsql" in db_url:
        # URLに認証トークンが含まれている場合、sqlalchemy-libsql のバグ/制限を回避するため、
        # クエリ文字列からトークンを抽出して connect_args["auth_token"] に設定する
        parsed = urllib.parse.urlparse(db_url)
        query_params = urllib.parse.parse_qs(parsed.query)

        token = None
        if "auth_token" in query_params:
            token = query_params["auth_token"][0]
        elif "authToken" in query_params:
            token = query_params["authToken"][0]

        if token:
            connect_args["auth_token"] = token

            # クエリパラメータからトークンを除去し、重複エラーを防ぐ
            new_query_params = {k: v for k, v in query_params.items() if k not in ("auth_token", "authToken")}
            # secure=true がない場合は付加して強制的に SSL 接続にする（308 リダイレクト回避）
            if "secure" not in new_query_params:
                new_query_params["secure"] = ["true"]

            new_query = urllib.parse.urlencode(new_query_params, doseq=True)
            # 末尾スラッシュ付きのパスにしてパースエラーを防ぐ
            path = parsed.path if parsed.path else "/"
            db_url = urllib.parse.urlunparse(parsed._replace(path=path, query=new_query))

    elif "sqlite" in db_url:
        # ローカル SQLite の場合は書き込みロックを防止する設定を付与
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30  # 30秒まで待機（並列アクセス対策）

    # libsql ブランチで check_same_thread が未設定の場合の安全なデフォルトを保証。
    # TypedDict の必須キー check_same_thread を常に満たすため。
    connect_args.setdefault("check_same_thread", False)


    return db_url, connect_args


def build_write_database_config(env: dict[str, str] | None = None) -> tuple[str, DatabaseConfig]:
    """Build the DB config used for WRITE operations.

    When ``TURSO_WRITE_REMOTE=true`` (and ``TURSO_SYNC_URL`` is configured),
    writes go **directly to the remote Turso primary** instead of through the
    local embedded replica. This avoids replica-vs-replica write conflicts when
    the same database is written from multiple machines (e.g. Windows writes,
    Mac reads): every writer talks straight to the single primary, and readers
    pull the latest via their replica's ``sync()``.

    With the flag off (default), writes use the same replica config as reads
    (backwards compatible).
    """
    env = os.environ if env is None else env
    turso_sync_url = env.get("TURSO_SYNC_URL")
    turso_auth_token = env.get("TURSO_AUTH_TOKEN")

    if (
        env.get("TURSO_WRITE_REMOTE", "false").lower() == "true"
        and turso_sync_url
    ):
        # Build a direct remote libSQL connection URL from the sync URL.
        # TURSO_SYNC_URL may be expressed either as:
        #   - libsql://host            (canonical form, per AGENTS.md)
        #   - https://host(?secure=...)  (already https; the ?secure=true
        #     query is just stripped and re-appended as a dialect hint)
        # In every case we end up with an https remote connection so the
        # dialect uses TLS instead of defaulting to http://, which triggers a
        # 308 Permanent Redirect from Turso.
        # The full netloc (user:pass@host:port) is preserved so that token-in-URL
        # forms (e.g. libsql://<token>@host) are not silently dropped.
        parsed = urllib.parse.urlparse(turso_sync_url)
        netloc = parsed.netloc or turso_sync_url
        # urlparse on a non-standard scheme (libsql://) may leave the scheme in
        # netloc; strip a stray "libsql://" prefix so only host[:port] (and any
        # userinfo) remains. userinfo (token@host) is preserved as-is.
        if netloc.startswith("libsql://"):
            netloc = netloc[len("libsql://"):]
        remote_url = f"sqlite+libsql://{netloc}/?secure=true"
        connect_args: DatabaseConfig = {"check_same_thread": False}
        if turso_auth_token:
            connect_args["auth_token"] = turso_auth_token
        return remote_url, connect_args

    # Fall back to the standard (replica) config for writes.
    return build_database_config(env)


# Module-level cache for the lazily-initialised engine / session factory.
# The engine is intentionally NOT created at import time: it is built on the
# first call to get_engine() / get_session_local(), so importing this module
# has no side effects (safe for tests and for controlling init order).
#
# A single module-level lock guards the lazy initialisers. They are invoked
# from multiple threads once ``hw-genie multi`` runs every account's routine in
# a thread pool (see hw_genie.runner). Without the lock two threads could race
# to build competing singletons (e.g. two session factories) on first use.
_engine = None
_SessionLocal = None
_write_engine = None
_WriteSessionLocal = None
_init_lock = threading.RLock()


def get_engine():
    """Return the SQLAlchemy engine, creating it on first access (lazy init)."""
    global _engine, engine
    if _engine is None:
        with _init_lock:
            if _engine is None:
                db_url, connect_args = build_database_config()
                _engine = create_engine(db_url, connect_args=connect_args)
                engine = _engine
    return _engine


def get_session_local():
    """Return the scoped session factory, creating it on first access."""
    global _SessionLocal
    if _SessionLocal is None:
        with _init_lock:
            if _SessionLocal is None:
                _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def get_write_engine():
    """Return the engine used for WRITE operations (see build_write_database_config).

    When remote writes are disabled (``TURSO_WRITE_REMOTE`` unset/false) the
    write engine is exactly the read engine, so no second connection is opened
    and behaviour is identical to before this feature existed.
    """
    global _write_engine
    if _write_engine is None:
        with _init_lock:
            if _write_engine is None:
                env = os.environ
                if env.get("TURSO_WRITE_REMOTE", "false").lower() != "true":
                    # No remote writes configured: reuse the (replica) read engine.
                    _write_engine = get_engine()
                else:
                    db_url, connect_args = build_write_database_config()
                    _write_engine = create_engine(db_url, connect_args=connect_args)
    return _write_engine


def get_write_session_local():
    """Return the session factory used for WRITE operations.

    When remote writes are disabled the write session is the read session,
    resolved on every call (not cached) so that test patching of
    ``get_session_local`` stays effective across tests.
    """
    global _WriteSessionLocal
    if _WriteSessionLocal is None or get_write_engine() is get_engine():
        if get_write_engine() is get_engine():
            # Delegate to the read session factory (remote-write disabled).
            # Resolved on every call so live patching of get_session_local
            # (used by tests) is respected.
            return get_session_local()
        with _init_lock:
            if _WriteSessionLocal is None:
                _WriteSessionLocal = sessionmaker(
                    bind=get_write_engine(), expire_on_commit=False
                )
    return _WriteSessionLocal


# Backwards-compatible module-level aliases. These are lazy proxies so that
# existing ``from hw_genie.core.database import SessionLocal`` imports and
# ``patch("hw_genie.core.database.engine", ...)`` style monkeypatching keep
# working, while the heavy engine object is still only built on first use.
class _LazySessionLocal:
    def __call__(self, *args, **kwargs):
        return get_session_local()(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(get_session_local(), name)


engine = None  # replaced lazily; accessible for introspection / patching
SessionLocal = _LazySessionLocal()


def init_db():
    # Ensure the schema exists on BOTH the read (replica) and write (remote,
    # when TURSO_WRITE_REMOTE is enabled) engines so that writes can persist
    # directly to the remote primary.
    read_engine = get_engine()
    write_engine = get_write_engine()
    # Pre-warm the session factories on the (single) main thread BEFORE any
    # thread-pool work, so the lazy getters don't race to build competing
    # singletons when ``hw-genie multi`` runs accounts in parallel.
    get_session_local()
    if write_engine is not read_engine:
        get_write_session_local()
    engines = {read_engine}
    if write_engine is not read_engine:
        engines.add(write_engine)

    for eng in engines:
        url_str = str(eng.url)
        if ":memory:" in url_str:
            Base.metadata.create_all(eng)
            continue
        if url_str.startswith(("sqlite:///", "sqlite+libsql:///")):
            parsed = urllib.parse.urlparse(url_str)
            # Strip exactly one leading "/" (the scheme authority delimiter).
            db_path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
            db_dir = os.path.dirname(os.path.abspath(db_path))
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
        Base.metadata.create_all(eng)
