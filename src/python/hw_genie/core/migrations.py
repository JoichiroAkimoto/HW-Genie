import json
from hw_genie.core.database import SessionLocal, Session, Account, AccountConfig


def migrate_sessions_to_normalized_schema():
    """
    Migrates data from the legacy 'sessions' table to the normalized 'accounts' and 'account_configs' tables.
    """
    with SessionLocal() as db:
        # Get all legacy sessions
        legacy_sessions = db.query(Session).all()
        if not legacy_sessions:
            print("No legacy sessions found to migrate.")
            return

        for leg_session in legacy_sessions:
            alias = leg_session.account
            data = leg_session.data or {}

            print(f"Migrating session: {alias}...")

            # 1. Extract identity and status information for 'accounts' table
            player = data.get("player", {})
            if isinstance(player, str):  # Handle cases where player might be a stringified JSON
                try:
                    player = json.loads(player)
                except json.JSONDecodeError:
                    player = {}

            def get_int(val):
                try:
                    return int(val) if val is not None else 0
                except (ValueError, TypeError):
                    return 0

            # Create Account record with all status fields
            player_id = player.get("id") or f"legacy_{alias}"

            account = Account(
                player_id=player_id,
                alias=alias,
                player_name=player.get("name", "Unknown"),
                level=get_int(player.get("level")),
                gold=get_int(player.get("gold")),
                gems=get_int(player.get("gems")),
                energy=get_int(player.get("energy")),
                arena_rank=get_int(player.get("arena_rank")),
                grand_rank=get_int(player.get("grand_rank")),
            )

            # Merge to avoid duplicates
            account = db.merge(account)
            db.flush()  # Ensure account.id is populated
            account_id = account.id

            # 2. Migrate all other data to 'account_configs'
            # We want to store everything that is not explicitly in the Account model.

            # Store headers
            headers = data.get("headers")
            if headers is not None:
                config_val = headers
                existing = db.query(AccountConfig).filter_by(account_id=account_id, config_key="headers").first()
                if existing:
                    existing.config_value = config_val
                else:
                    db.add(AccountConfig(account_id=account_id, config_key="headers", config_value=config_val))

            # Store last_item_raid_mission_id
            last_mission_id = data.get("last_item_raid_mission_id")
            if last_mission_id is not None:
                # Also update the Account table column
                try:
                    account.last_mission_id = int(last_mission_id)
                except (ValueError, TypeError):
                    pass

                # Maintain compatibility by also storing it in config
                config_val = str(last_mission_id)
                existing = db.query(AccountConfig).filter_by(account_id=account_id, config_key="last_item_raid_mission_id").first()
                if existing:
                    existing.config_value = config_val
                else:
                    db.add(AccountConfig(account_id=account_id, config_key="last_item_raid_mission_id", config_value=config_val))

            # Store individual player status that are NOT in the Account table
            status_fields = ("id", "name", "level", "gold", "gems", "energy", "arena_rank", "grand_rank")
            if isinstance(player, dict):
                for k, v in player.items():
                    if k in status_fields:
                        continue
                    config_key = f"player_{k}"
                    config_val = v
                    existing = db.query(AccountConfig).filter_by(account_id=account_id, config_key=config_key).first()
                    if existing:
                        existing.config_value = config_val
                    else:
                        db.add(AccountConfig(account_id=account_id, config_key=config_key, config_value=config_val))

            # Store any other top-level keys that aren't 'player' or 'headers' or 'last_item_raid_mission_id'
            for k, v in data.items():
                if k not in ("player", "headers", "last_item_raid_mission_id"):
                    config_key = k
                    config_val = v
                    existing = db.query(AccountConfig).filter_by(account_id=account_id, config_key=config_key).first()
                    if existing:
                        existing.config_value = config_val
                    else:
                        db.add(AccountConfig(account_id=account_id, config_key=config_key, config_value=config_val))

        db.commit()
        print("Migration completed successfully.")


if __name__ == "__main__":
    migrate_sessions_to_normalized_schema()
