import os
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

# 環境変数 DATABASE_URL で接続先を切り替え可能に
db_url = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

# 接続時の引数
connect_args = {}

# Turso Syncs (Embedded Replicas) settings
turso_sync_url = os.getenv("TURSO_SYNC_URL")
turso_auth_token = os.getenv("TURSO_AUTH_TOKEN")
turso_sync_interval = os.getenv("TURSO_SYNC_INTERVAL")

if turso_sync_url:
    # Determine the local database file path to act as the replica
    if db_url.startswith("sqlite+libsql:///"):
        parsed = urllib.parse.urlparse(db_url)
        local_path = parsed.path
    elif db_url.startswith("sqlite:///"):
        parsed = urllib.parse.urlparse(db_url)
        local_path = parsed.path
    else:
        local_path = DEFAULT_DB_PATH

    # Clean up Windows absolute paths if needed (e.g. /C:/path -> C:/path)
    if local_path.startswith("/") and len(local_path) > 2 and local_path[2] == ":":
        local_path = local_path[1:]
    elif local_path.startswith("/") and not local_path.startswith("//"):
        local_path = os.path.abspath(local_path)

    # Force using sqlite+libsql dialect pointing to the local file
    db_url = f"sqlite+libsql:///{local_path}"

    # Setup the replica connection parameters
    connect_args["sync_url"] = turso_sync_url
    if turso_auth_token:
        connect_args["auth_token"] = turso_auth_token

    if turso_sync_interval:
        try:
            connect_args["sync_interval"] = float(turso_sync_interval)
        except (ValueError, TypeError):
            pass

    # Ensure check_same_thread is True for local-based replica (safe and default for multi-thread replica)
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

engine = create_engine(db_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db():
    # データベースファイルのディレクトリが存在することを確認
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
        if not db_path.startswith(":memory:"):
            db_dir = os.path.dirname(os.path.abspath(db_path))
            if not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
    Base.metadata.create_all(engine)
