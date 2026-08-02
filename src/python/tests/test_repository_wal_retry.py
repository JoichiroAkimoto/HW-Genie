"""update_config の WAL 競合リトライ動作のテスト。"""

import pytest

from hw_genie.core import repository as repo_module
from hw_genie.core.database import Account, get_session_local


def test_update_config_retries_wal_contention(monkeypatch, mock_sleep):
    """update_config は WAL 競合の commit 失敗を再試行して成功する。"""
    attempts = {"n": 0}
    real_sm = get_session_local()

    class FlakyFactory:
        def __call__(self, *args, **kwargs):
            session = real_sm()
            orig_commit = session.commit

            def commit():
                if attempts["n"] == 0:
                    attempts["n"] += 1
                    raise ValueError("wal_insert_begin failed")
                return orig_commit()

            session.commit = commit
            return session

    monkeypatch.setattr(repo_module, "get_write_session_local", lambda: FlakyFactory())
    monkeypatch.setattr(
        repo_module,
        "get_write_engine",
        lambda: type("E", (), {"pool": type("P", (), {"dispose": lambda self: None})()})(),
    )

    repo_module.SessionRepository().update_config(
        "wal_retry_alias", {"player": {"id": "player-1", "name": "RetryTest"}}
    )

    assert attempts["n"] == 1
    with get_session_local()() as db:
        rec = db.query(Account).filter(Account.alias == "wal_retry_alias").first()
        assert rec is not None
        assert rec.player_name == "RetryTest"


def test_update_config_raises_when_wal_contention_persists(monkeypatch, mock_sleep):
    """WAL 競合が解消しなければ最後に例外を再送出する。"""
    real_sm = get_session_local()

    class FlakyFactory:
        def __call__(self, *args, **kwargs):
            session = real_sm()

            def commit():
                raise ValueError("wal_insert_begin failed")

            session.commit = commit
            return session

    monkeypatch.setattr(repo_module, "get_write_session_local", lambda: FlakyFactory())
    monkeypatch.setattr(
        repo_module,
        "get_write_engine",
        lambda: type("E", (), {"pool": type("P", (), {"dispose": lambda self: None})()})(),
    )

    with pytest.raises(ValueError, match="wal_insert_begin failed"):
        repo_module.SessionRepository().update_config(
            "wal_retry_alias", {"player": {"id": "player-2", "name": "Fail"}}
        )
    # attempts=5 のうち 4 回バックオフ待ちする
    assert mock_sleep.call_count == 4


def test_update_config_retries_hrana_stream_and_disposes_pool(monkeypatch, mock_sleep):
    """Hrana ストリーム切断でも再試行し、プールを dispose して新規接続を張る。"""
    attempts = {"n": 0}
    real_sm = get_session_local()
    disposed = {"count": 0}

    class FlakyFactory:
        def __call__(self, *args, **kwargs):
            session = real_sm()
            orig_commit = session.commit

            def commit():
                if attempts["n"] == 0:
                    attempts["n"] += 1
                    raise ValueError("stream not found: 49580f7d:ad779")
                return orig_commit()

            session.commit = commit
            return session

    class FakePool:
        def dispose(self):
            disposed["count"] += 1

    class FakeEngine:
        pool = FakePool()

    monkeypatch.setattr(repo_module, "get_write_session_local", lambda: FlakyFactory())
    monkeypatch.setattr(
        repo_module, "get_write_engine", lambda: FakeEngine()
    )

    repo_module.SessionRepository().update_config(
        "hrana_retry_alias", {"player": {"id": "player-3", "name": "HranaRetry"}}
    )

    assert attempts["n"] == 1
    assert disposed["count"] == 1
    with get_session_local()() as db:
        rec = db.query(Account).filter(Account.alias == "hrana_retry_alias").first()
        assert rec is not None
        assert rec.player_name == "HranaRetry"
