from typing import Any, Dict
from .database import SessionLocal, Session

class SessionRepository:
    def get_data(self, account: str) -> Dict[str, Any]:
        with SessionLocal() as db:
            record = db.query(Session).filter(Session.account == account).first()
            return record.data if record else {}

    def save_data(self, account: str, data: Dict[str, Any]) -> None:
        with SessionLocal() as db:
            record = db.query(Session).filter(Session.account == account).first()
            if record:
                record.data = data
            else:
                db.add(Session(account=account, data=data))
            db.commit()
