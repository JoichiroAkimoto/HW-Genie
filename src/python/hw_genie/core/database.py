import logging
import os
from pathlib import Path
from typing import NotRequired, TypedDict
import urllib.parse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, JSON, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

Base = declarative_base()


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


# プロジェクトルートの絶対パスを基点に DB パスを確定させる
# 現在: src/python/hw_genie/core/database.py
# 1: core, 2: hw_genie, 3: python, 4: src, 5: プロジェクトルート
PKG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

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

    if turso_sync_url:
        # Determine the local database file path to act as the replica.
        # Use pathlib for OS-agnostic, robust path normalisation.
        if db_url.startswith(("sqlite+libsql:///", "sqlite:///")):
            parsed = urllib.parse.urlparse(db_url)
            # urlparse keeps the URL's leading "/" (the authority delimiter of
            # the sqlite:/// scheme) in the path. Keep it as-is: an absolute
            # path like "/app/data/hw_genie.db" stays absolute, while a relative
            # path like "foo.db" is resolved against the current working dir by
            # .absolute() below. Do NOT strip the leading slash, as that would
            # turn an absolute path into a cwd-relative one.
            local_path = Path(parsed.path or ".")
        else:
            local_path = Path(DEFAULT_DB_PATH)

        # Normalise to an absolute path. Use absolute() (not resolve()) so that
        # symlink-based paths like /tmp stay as the user specified, while still
        # resolving relative-to-cwd paths. Handles Windows drive paths too.
        local_path = local_path.absolute()

        # Force using sqlite+libsql dialect pointing to the local file.
        # local_path is absolute (starts with "/"); strip that leading slash and
        # prepend the scheme delimiter to yield exactly "sqlite+libsql:///abs/path".
        db_url = f"sqlite+libsql:///{local_path.as_posix().lstrip('/')}"

        # Setup the replica connection parameters
        connect_args["sync_url"] = turso_sync_url
        if turso_auth_token:
            connect_args["auth_token"] = turso_auth_token

        if turso_sync_interval:
            try:
                connect_args["sync_interval"] = float(turso_sync_interval)
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid TURSO_SYNC_INTERVAL=%r ignored; falling back to the "
                    "default sync interval.",
                    turso_sync_interval,
                )

        # Ensure check_same_thread is True for local-based replica
        connect_args["check_same_thread"] = True

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


# Module-level cache for the lazily-initialised engine / session factory.
# The engine is intentionally NOT created at import time: it is built on the
# first call to get_engine() / get_session_local(), so importing this module
# has no side effects (safe for tests and for controlling init order).
_engine = None
_SessionLocal = None


def get_engine():
    """Return the SQLAlchemy engine, creating it on first access (lazy init)."""
    global _engine, engine
    if _engine is None:
        db_url, connect_args = build_database_config()
        _engine = create_engine(db_url, connect_args=connect_args)
        engine = _engine
    return _engine


def get_session_local():
    """Return the scoped session factory, creating it on first access."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


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
    # データベースファイルのディレクトリが存在することを確認
    url_str = str(get_engine().url)
    if ":memory:" in url_str:
        Base.metadata.create_all(get_engine())
        return
    if url_str.startswith(("sqlite:///", "sqlite+libsql:///")):
        parsed = urllib.parse.urlparse(url_str)
        # Strip exactly one leading "/" (the scheme authority delimiter).
        db_path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        db_dir = os.path.dirname(os.path.abspath(db_path))
        if not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
    Base.metadata.create_all(get_engine())
