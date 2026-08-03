from typing import Optional, List
from .repository import SessionRepository, AccountData


class SessionManager:
    repo = SessionRepository()

    @classmethod
    def save(cls, account: str, data: AccountData):
        account = account.strip()
        resolved_account = account
        accounts = cls.list_accounts()
        # 大文字小文字・前後の空白を区別せずに一致するものを探す。
        # 一致した既存エイリアスに空白が含まれていても、保存時はトリム済みの
        # account を優先して使う（DB への空白混入を防ぐ）。
        match = next((a for a in accounts if a.strip().lower() == account.lower()), None)
        if match and match.strip() != account.strip():
            resolved_account = account.strip()

        # PlayerStatus などのオブジェクトを辞書に変換して JSON シリアライズ可能にする
        save_data = data.copy()
        player = save_data.get("player")
        if player and hasattr(player, "to_dict"):
            save_data["player"] = player.to_dict()

        cls.repo.save_data(resolved_account, save_data)

    @classmethod
    def list_accounts(cls) -> List[str]:
        return cls.repo.list_accounts()

    @classmethod
    def load(cls, account: str = "default") -> AccountData:
        account = account.strip()
        data = cls.repo.get_data(account)

        # もし見つからず、かつ大文字小文字の違いがある可能性を考慮して再検索
        if not data:
            accounts = cls.list_accounts()
            # 大文字小文字および前後の空白を区別せずに一致するものを探す
            match = next((a for a in accounts if a.strip().lower() == account.lower()), None)
            if match:
                data = cls.repo.get_data(match)

        return data

    @classmethod
    def get_last_mission_id(cls, account: str = "default") -> Optional[int]:
        account = account.strip()
        return cls.load(account).get("last_item_raid_mission_id")

    @classmethod
    def set_last_mission_id(cls, mission_id: int, account: str = "default"):
        account = account.strip()
        # 文字列（例: "100"）で渡されても int に正規化してから比較・保存する
        mission_id = int(mission_id)
        # 現在値と同値なら書き込みトランザクションをスキップする
        # （WAL 競合リトライの対象を減らす。読み取りのみで済む場合は軽い）
        if cls.get_last_mission_id(account) == mission_id:
            return
        # 大文字小文字および前後の空白を区別せずに正しいエイリアスを特定する
        resolved_account = account
        accounts = cls.list_accounts()
        match = next((a for a in accounts if a.strip().lower() == account.lower()), None)
        if match:
            resolved_account = match
        
        cls.repo.update_config(resolved_account, {"last_item_raid_mission_id": mission_id})

    @classmethod
    def build_item_raid_payload(cls, account: str = "default") -> dict | None:
        """Build an item-raid payload for ``account`` from the stored mission id.

        Returns ``None`` when no mission id is configured for the account
        (item raid should then be skipped). The payload structure (``calls`` /
        ``ident`` / ``context``) lives here so callers (e.g. the parallel
        runner) don't have to know the API request shape.
        """
        account = account.strip()
        mission_id = cls.get_last_mission_id(account)
        if not mission_id:
            return None
        return {
            "mission_id": mission_id,
            "calls": [
                {
                    "name": "missionRaid",
                    "args": {"id": mission_id, "times": 10},
                    "context": {"actionTs": 0},
                    "ident": "body",
                }
            ],
        }
