from typing import Optional, Dict, Any
from .repository import SessionRepository

class SessionManager:
    repo = SessionRepository()

    @classmethod
    def load(cls, account: str = "default") -> Dict[str, Any]:
        return cls.repo.get_data(account)

    @classmethod
    def get_last_mission_id(cls, account: str = "default") -> Optional[int]:
        return cls.load(account).get("last_item_raid_mission_id")

    @classmethod
    def set_last_mission_id(cls, mission_id: int, account: str = "default"):
        data = cls.load(account)
        data["last_item_raid_mission_id"] = mission_id
        cls.repo.save_data(account, data)
