from sqlalchemy import create_engine, Column, String, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# テスト環境と本番環境でパスを変えるべきですが、一旦テスト用パスを考慮
db_path = os.getenv("DB_PATH", "hw_genie.db")
engine = create_engine(f'sqlite:///{db_path}')
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Session(Base):
    __tablename__ = 'sessions'
    account = Column(String, primary_key=True)
    data = Column(JSON)

def init_db():
    Base.metadata.create_all(engine)
