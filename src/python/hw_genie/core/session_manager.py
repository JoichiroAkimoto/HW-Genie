import json
import os
from typing import Optional, Dict, Any

# セッションファイルの検索順序（client.pyと整合性をとる）
SEARCH_PATHS = [
    "session.json",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "session.json")),
    os.path.expanduser("~/.hw-genie/session.json"),
]

class SessionManager:
    _cached_data: Optional[Dict[str, Any]] = None
    _loaded_path: Optional[str] = None

    @classmethod
    def _get_session_path(cls) -> str:
        """読み込み可能なセッションファイルのパスを特定する"""
        for path in SEARCH_PATHS:
            if os.path.exists(path):
                return path
        return "session.json"  # デフォルト

    @classmethod
    def load(cls) -> Dict[str, Any]:
        if cls._cached_data is not None:
            return cls._cached_data
            
        cls._loaded_path = cls._get_session_path()
        if not os.path.exists(cls._loaded_path):
            cls._cached_data = {}
            return cls._cached_data
            
        with open(cls._loaded_path, "r") as f:
            cls._cached_data = json.load(f)
        return cls._cached_data

    @classmethod
    def save(cls):
        if cls._cached_data is None or cls._loaded_path is None:
            return
            
        with open(cls._loaded_path, "w") as f:
            json.dump(cls._cached_data, f, indent=2)

    @classmethod
    def get_last_mission_id(cls) -> Optional[int]:
        return cls.load().get("last_item_raid_mission_id")

    @classmethod
    def set_last_mission_id(cls, mission_id: int):
        data = cls.load()
        data["last_item_raid_mission_id"] = mission_id
        cls.save()
