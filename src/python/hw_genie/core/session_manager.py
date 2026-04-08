import json
import os

SESSION_FILE = "session.json"

class SessionManager:
    @staticmethod
    def load():
        if not os.path.exists(SESSION_FILE):
            return {}
        with open(SESSION_FILE, "r") as f:
            return json.load(f)

    @staticmethod
    def save(data):
        with open(SESSION_FILE, "w") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def get_last_mission_id():
        return SessionManager.load().get("last_item_raid_mission_id")

    @staticmethod
    def set_last_mission_id(mission_id):
        data = SessionManager.load()
        data["last_item_raid_mission_id"] = mission_id
        SessionManager.save(data)
