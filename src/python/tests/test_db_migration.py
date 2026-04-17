from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from hw_genie.core.database import Base, Account, AccountConfig, Session as LegacySession
from hw_genie.core.migrations import migrate_sessions_to_normalized_schema


def test_db_migration_preserves_all_data(tmp_path, monkeypatch):
    # 1. Setup temporary database
    db_path = tmp_path / "test_migration.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SessionLocal = sessionmaker(bind=engine)

    # Mock SessionLocal in migrations module
    monkeypatch.setattr("hw_genie.core.migrations.SessionLocal", SessionLocal)

    # Create all tables (including LegacySession and Account/AccountConfig from Base)
    Base.metadata.create_all(engine)

    # 2. Insert legacy data
    with SessionLocal() as db:
        legacy_data = {
            "acc1": {
                "player": {
                    "name": "Player 1",
                    "level": 100,
                    "gold": 1000000,
                    "gems": 500,
                    "energy": 100,
                    "arena_rank": 10,
                    "grand_rank": 5,
                    "custom_stat": "awesome",
                },
                "headers": {"User-Agent": "HW-Genie"},
                "last_item_raid_mission_id": 12345,
                "some_other_key": "some_other_value",
            },
            "acc2": {
                "player": {
                    "name": "Player 2",
                    "level": "50",  # test string level
                    "gold": "2000",  # test string gold
                },
                "headers": {"User-Agent": "HW-Genie-2"},
                "last_item_raid_mission_id": "67890",  # test string id
            },
        }

        for alias, data in legacy_data.items():
            db.add(LegacySession(account=alias, data=data))
        db.commit()

    # 3. Run migration
    migrate_sessions_to_normalized_schema()

    # 4. Verify results
    with SessionLocal() as db:
        # Verify Account 1
        acc1 = db.query(Account).filter_by(alias="acc1").first()
        assert acc1 is not None
        assert acc1.player_name == "Player 1"
        assert acc1.level == 100
        assert acc1.gold == 1000000
        assert acc1.gems == 500
        assert acc1.energy == 100
        assert acc1.arena_rank == 10
        assert acc1.grand_rank == 5

        # Verify AccountConfig for acc1
        headers = db.query(AccountConfig).filter_by(account_id=acc1.id, config_key="headers").first()
        assert headers is not None
        assert headers.config_value == {"User-Agent": "HW-Genie"}

        last_mission = db.query(AccountConfig).filter_by(account_id=acc1.id, config_key="last_item_raid_mission_id").first()
        assert last_mission is not None
        assert last_mission.config_value == "12345"
        assert acc1.last_mission_id == 12345

        custom_stat = db.query(AccountConfig).filter_by(account_id=acc1.id, config_key="player_custom_stat").first()
        assert custom_stat is not None
        assert custom_stat.config_value == "awesome"

        other_key = db.query(AccountConfig).filter_by(account_id=acc1.id, config_key="some_other_key").first()
        assert other_key is not None
        assert other_key.config_value == "some_other_value"

        # Verify Account 2 (type conversion)
        acc2 = db.query(Account).filter_by(alias="acc2").first()
        assert acc2 is not None
        assert acc2.level == 50
        assert acc2.gold == 2000

        last_mission2 = db.query(AccountConfig).filter_by(account_id=acc2.id, config_key="last_item_raid_mission_id").first()
        assert last_mission2 is not None
        assert last_mission2.config_value == "67890"
