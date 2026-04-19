import os
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
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def update_from_dict(self, player_data: dict):
        """
        Updates account status fields from a dictionary, with type conversion.
        
        Args:
            player_data (dict): Dictionary containing player info (name, level, gold, etc.)
        """
        if "name" in player_data:
            self.player_name = player_data["name"]
        
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
DEFAULT_DB_PATH = os.path.join(PKG_ROOT, "data", "hw_genie.db")
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH}"

# 環境変数 DATABASE_URL で接続先を切り替え可能に
db_url = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

# SQLite を使用する場合、書き込み待ち時間を延長してロックエラーを防ぐ
connect_args = {}
if "sqlite" in db_url:
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
