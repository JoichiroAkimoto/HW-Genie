from typing import Any, Dict, List
from .database import SessionLocal, Account, AccountConfig


class SessionRepository:
    def get_data(self, account: str) -> Dict[str, Any]:

        with SessionLocal() as db:
            account_rec = db.query(Account).filter(Account.alias == account).first()
            if not account_rec:
                return {}

            data = {}
            player_info = {}

            configs = db.query(AccountConfig).filter(AccountConfig.account_id == account_rec.id).all()
            for cfg in configs:
                key = cfg.config_key
                val = cfg.config_value

                if key.startswith("player_"):
                    player_key = key[7:]
                    player_info[player_key] = val
                elif key == "headers":
                    data["headers"] = val
                elif key == "last_item_raid_mission_id":
                    # Backward compatibility: still check config if table column is empty
                    if account_rec.last_mission_id is None:
                        try:
                            data["last_item_raid_mission_id"] = int(val)
                        except (ValueError, TypeError):
                            pass
                else:
                    data[key] = val

            # Add basic player info from Account table
            if account_rec.player_name and account_rec.player_name != "Unknown":
                player_info["name"] = account_rec.player_name
            if account_rec.level is not None and account_rec.level != 0:
                player_info["level"] = account_rec.level
            if account_rec.gold is not None and account_rec.gold != 0:
                player_info["gold"] = account_rec.gold
            if account_rec.gems is not None and account_rec.gems != 0:
                player_info["gems"] = account_rec.gems
            if account_rec.energy is not None and account_rec.energy != 0:
                player_info["energy"] = account_rec.energy
            if account_rec.arena_rank is not None and account_rec.arena_rank != 0:
                player_info["arena_rank"] = account_rec.arena_rank
            if account_rec.grand_rank is not None and account_rec.grand_rank != 0:
                player_info["grand_rank"] = account_rec.grand_rank

            # Add last_mission_id to data
            if account_rec.last_mission_id is not None:
                data["last_item_raid_mission_id"] = account_rec.last_mission_id

            # If we have any player info, add it to data
            if player_info:
                data["player"] = player_info

            return data

    def list_accounts(self) -> List[str]:
        with SessionLocal() as db:
            records = db.query(Account.alias).all()
            return [r.alias for r in records]

    def save_data(self, account: str, data: Dict[str, Any]) -> None:
        """Backward compatibility wrapper for update_config"""
        self.update_config(account, data)

    def update_config(self, account: str, data: Dict[str, Any]) -> None:
        with SessionLocal() as db:
            # 1. Update/Create Account
            player = data.get("player")
            if player is not None and isinstance(player, dict):
                player_id = player.get("id")
                if not player_id:
                    # If player_id is missing, we fall back to alias-based lookup for now,
                    # but this is not ideal for the new unique requirement.
                    # However, for compatibility we can't just crash.
                    account_rec = db.query(Account).filter(Account.alias == account).first()
                else:
                    account_rec = db.query(Account).filter(Account.player_id == player_id).first()

                if not account_rec:
                    try:
                        # If player_id is provided, use it. Otherwise, this might fail unique constraint if player_id is missing.
                        # If player_id is missing, we'll let it fail or handle as a new account with a dummy/random ID if allowed.
                        # But according to PRD, we must use API ID.
                        if player_id:
                            account_rec = Account(player_id=player_id, alias=account)
                        else:
                            # This case should be rare if API response is correct
                            account_rec = Account(player_id=f"unknown_{account}", alias=account)
                        db.add(account_rec)
                        db.flush()
                    except Exception:
                        db.rollback()
                        account_rec = db.query(Account).filter(Account.alias == account).first()
                        if not account_rec:
                            raise

                # Update alias to the current one (supports alias changes)
                account_rec.alias = account

                if "name" in player:
                    account_rec.player_name = player["name"]
                if "level" in player:
                    try:
                        account_rec.level = int(player["level"])
                    except (ValueError, TypeError):
                        pass
                if "gold" in player:
                    try:
                        account_rec.gold = int(player["gold"])
                    except (ValueError, TypeError):
                        pass
                if "gems" in player:
                    try:
                        account_rec.gems = int(player["gems"])
                    except (ValueError, TypeError):
                        pass
                if "energy" in player:
                    try:
                        account_rec.energy = int(player["energy"])
                    except (ValueError, TypeError):
                        pass
                if "arena_rank" in player:
                    try:
                        account_rec.arena_rank = int(player["arena_rank"])
                    except (ValueError, TypeError):
                        pass
                if "grand_rank" in player:
                    try:
                        account_rec.grand_rank = int(player["grand_rank"])
                    except (ValueError, TypeError):
                        pass

                # Store other player configs in AccountConfig
                for k, v in player.items():
                    if k in ("id", "name", "level", "gold", "gems", "energy", "arena_rank", "grand_rank"):
                        continue
                    self._upsert_config(db, account_rec.id, f"player_{k}", v)
            elif player is None:
                # Ensure account exists by alias
                account_rec = db.query(Account).filter(Account.alias == account).first()
                if not account_rec:
                    try:
                        # Without player_id, we can only create with a dummy one.
                        account_rec = Account(player_id=f"unknown_{account}", alias=account)
                        db.add(account_rec)
                        db.flush()
                    except Exception:
                        db.rollback()
                        account_rec = db.query(Account).filter(Account.alias == account).first()

            # 2. Update other configs
            for k, v in data.items():
                if k == "player":
                    continue
                if k == "last_item_raid_mission_id":
                    try:
                        account_rec.last_mission_id = int(v)
                    except (ValueError, TypeError):
                        pass
                else:
                    self._upsert_config(db, account_rec.id, k, v)

            db.commit()

    def _upsert_config(self, db, account_id: int, key: str, value: Any) -> None:
        if not key or not isinstance(key, str):
            raise ValueError(f"Invalid config_key: {key}. config_key must be a non-empty string.")
        existing = db.query(AccountConfig).filter_by(account_id=account_id, config_key=key).first()
        if existing:
            existing.config_value = value
        else:
            db.add(AccountConfig(account_id=account_id, config_key=key, config_value=value))
