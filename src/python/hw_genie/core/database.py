import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, JSON

Base = declarative_base()

class Session(Base):
    __tablename__ = 'sessions'
    account = Column(String, primary_key=True)
    data = Column(JSON)

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
