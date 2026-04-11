from .database import SessionLocal, Session

class SessionRepository:
    def get_data(self, account: str):
        db = SessionLocal()
        record = db.query(Session).filter(Session.account == account).first()
        db.close()
        return record.data if record else {}

    def save_data(self, account: str, data: dict):
        db = SessionLocal()
        record = db.query(Session).filter(Session.account == account).first()
        if record:
            record.data = data
        else:
            db.add(Session(account=account, data=data))
        db.commit()
        db.close()
