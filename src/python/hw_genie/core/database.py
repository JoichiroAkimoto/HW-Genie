import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, JSON

Base = declarative_base()

class Session(Base):
    __tablename__ = 'sessions'
    account = Column(String, primary_key=True)
    data = Column(JSON)

# 環境変数 DATABASE_URL で接続先を切り替え可能に
db_url = os.getenv("DATABASE_URL", "sqlite:///hw_genie.db")
connect_args = {"check_same_thread": False} if "sqlite" in db_url else {}
engine = create_engine(db_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)
