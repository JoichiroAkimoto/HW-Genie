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
            
            # Special mapping for specific config keys
            special_keys = {
                "headers": lambda val: data.update({"headers": val}),
                "last_item_raid_mission_id": lambda val: data.update({"last_item_raid_mission_id": int(val)}) if account_rec.last_mission_id is None else None
            }

            for cfg in configs:
                key = cfg.config_key
                val = cfg.config_value

                if key in special_keys:
                    try:
                        special_keys[key](val)
                    except (ValueError, TypeError):
                        pass
                elif key.startswith("player_"):
                    player_info[key[7:]] = val
                else:
                    data[key] = val

            # Add basic player info from Account table
            status_fields = {
                "player_name": "name",
                "level": "level",
                "gold": "gold",
                "gems": "gems",
                "energy": "energy",
                "arena_rank": "arena_rank",
                "grand_rank": "grand_rank",
            }
            for field, key in status_fields.items():
                val = getattr(account_rec, field)
                if val is not None and val != 0 and val != "Unknown":
                    player_info[key] = val

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
            try:
                # 1. Update/Create Account
                player = data.get("player")
                if player is not None and isinstance(player, dict):
                    player_id = player.get("id")
                    if not player_id:
                        # If player_id is missing, fall back to alias-based lookup for compatibility
                        account_rec = db.query(Account).filter(Account.alias == account).first()
                    else:
                        account_rec = db.query(Account).filter(Account.player_id == player_id).first()

                    if not account_rec:
                        if player_id:
                            account_rec = Account(player_id=player_id, alias=account)
                        else:
                            raise ValueError(f"player_id is required to create a new account for alias: {account}")
                        db.add(account_rec)
                        db.flush()

                    # Update alias and basic info
                    account_rec.alias = account
                    if "name" in player:
                        account_rec.player_name = player["name"]
                    
                    # Convert types safely
                    fields = {
                        "level": "level",
                        "gold": "gold",
                        "gems": "gems",
                        "energy": "energy",
                        "arena_rank": "arena_rank",
                        "grand_rank": "grand_rank"
                    }
                    for p_key, attr in fields.items():
                        if p_key in player:
                            try:
                                setattr(account_rec, attr, int(player[p_key]))
                            except (ValueError, TypeError):
                                pass

                    # Store other player configs
                    for k, v in player.items():
                        if k in ("id", "name", "level", "gold", "gems", "energy", "arena_rank", "grand_rank"):
                            continue
                        self._upsert_config(db, account_rec.id, f"player_{k}", v)
                
                elif player is None:
                    account_rec = db.query(Account).filter(Account.alias == account).first()
                    if not account_rec:
                        raise ValueError(f"Account not found for alias: {account}, and no player_id provided to create one.")

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
            except Exception:
                db.rollback()
                raise

    def _upsert_config(self, db, account_id: int, key: str, value: Any) -> None:
        if not key or not isinstance(key, str):
            raise ValueError(f"Invalid config_key: {key}. config_key must be a non-empty string.")
        existing = db.query(AccountConfig).filter_by(account_id=account_id, config_key=key).first()
        if existing:
            existing.config_value = value
        else:
            db.add(AccountConfig(account_id=account_id, config_key=key, config_value=value))
