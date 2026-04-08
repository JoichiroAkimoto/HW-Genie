import json
import os
from typing import Optional, Dict, Any

# セッションファイルの検索順序
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
    def load(cls, path: Optional[str] = None) -> Dict[str, Any]:
        """指定されたパスまたはデフォルトのパスからセッションをロードする"""
        target_path = path or cls._get_session_path()
        if not os.path.exists(target_path):
            return {}
            
        with open(target_path, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}

    @classmethod
    def get_last_mission_id(cls, account: str = "default") -> Optional[int]:
        """指定されたアカウントのセッションファイルからミッションIDを取得する"""
        from hw_genie.core.auth import get_session_path
        path = get_session_path(account)
        return cls.load(path).get("last_item_raid_mission_id")

    @classmethod
    def set_last_mission_id(cls, mission_id: int, account: str = "default"):
        """指定されたアカウントのセッションファイルにミッションIDを保存する"""
        from hw_genie.core.auth import get_session_path
        path = get_session_path(account)
        data = cls.load(path)
        data["last_item_raid_mission_id"] = mission_id
        
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
