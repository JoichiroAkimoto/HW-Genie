import json
import os
from typing import Optional, Dict, Any
from .repository import SessionRepository


class SessionManager:
    repo = SessionRepository()

    @classmethod
    def save(cls, account: str, data: Dict[str, Any]):
        # PlayerStatus などのオブジェクトを辞書に変換して JSON シリアライズ可能にする
        save_data = data.copy()
        player = save_data.get("player")
        if player and hasattr(player, "to_dict"):
            save_data["player"] = player.to_dict()

        cls.repo.save_data(account, save_data)

    @classmethod
    def load(cls, account: str = "default") -> Dict[str, Any]:
        data = cls.repo.get_data(account)
        if not data:
            # 自動移行ロジック: DBにデータがない場合、既存のjsonファイルからの移行を試みる
            from hw_genie.core.auth import get_session_path

            path = get_session_path(account)
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        old_data = json.load(f)
                    if old_data:
                        cls.repo.save_data(account, old_data)
                        # 移行成功時は標準エラーに出力（ログ用途）
                        import sys

                        print(f"INFO: Migrated session for '{account}' from {path} to database.", file=sys.stderr)
                        return old_data
                except (json.JSONDecodeError, IOError):
                    pass
        return data

    @classmethod
    def get_last_mission_id(cls, account: str = "default") -> Optional[int]:
        return cls.load(account).get("last_item_raid_mission_id")

    @classmethod
    def set_last_mission_id(cls, mission_id: int, account: str = "default"):
        data = cls.load(account)
        data["last_item_raid_mission_id"] = mission_id
        cls.repo.save_data(account, data)
