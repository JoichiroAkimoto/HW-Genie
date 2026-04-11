from sqlalchemy import create_engine, Column, String, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class Session(Base):
    __tablename__ = 'sessions'
    account = Column(String, primary_key=True)
    data = Column(JSON)

engine = create_engine('sqlite:///hw_genie.db')
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(engine)
