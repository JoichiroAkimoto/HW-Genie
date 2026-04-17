import pytest
import concurrent.futures
import tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from hw_genie.core.repository import SessionRepository
from hw_genie.core.database import Base


@pytest.fixture
def repo():
    # Use a temporary file for each test to ensure isolation and realism
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_url = f"sqlite:///{tmp.name}"
        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)

        # Override SessionLocal for the repository instance
        import hw_genie.core.repository as repo_mod

        original_session_local = repo_mod.SessionLocal
        repo_mod.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        repository = SessionRepository()
        yield repository

        # Restore original SessionLocal
        repo_mod.SessionLocal = original_session_local


def test_invalid_config_key(repo):
    # Test None key
    with pytest.raises(ValueError, match="Invalid config_key"):
        repo.update_config("test_acc", {None: "value"})

    # Test empty string key
    with pytest.raises(ValueError, match="Invalid config_key"):
        repo.update_config("test_acc", {"": "value"})

    # Test non-string key
    with pytest.raises(ValueError, match="Invalid config_key"):
        repo.update_config("test_acc", {123: "value"})


def test_parallel_account_updates(repo):
    accounts = [f"acc_{i}" for i in range(10)]

    def update_account(acc):
        for i in range(10):
            repo.update_config(acc, {f"key_{i}": f"val_{i}"})
            repo.get_data(acc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(update_account, acc) for acc in accounts]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                pytest.fail(f"Thread raised exception: {e}")

    for acc in accounts:
        data = repo.get_data(acc)
        for i in range(10):
            assert data[f"key_{i}"] == f"val_{i}"


def test_parallel_same_account_updates(repo):
    acc = "parallel_acc"

    def update_val(i):
        repo.update_config(acc, {f"key_{i}": f"val_{i}"})

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(update_val, i) for i in range(50)]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                pytest.fail(f"Thread raised exception: {e}")

    data = repo.get_data(acc)
    for i in range(50):
        assert data[f"key_{i}"] == f"val_{i}"
