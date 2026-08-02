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


def test_update_config_does_not_dispose_on_validation_error(monkeypatch, mock_sleep):
    """非一時的エラー（バリデーション失敗）ではプールを dispose しない。"""
    disposed = {"count": 0}
    real_sm = get_session_local()

    class ValidationErrorFactory:
        def __call__(self, *args, **kwargs):
            session = real_sm()

            def commit():
                raise ValueError("player_id is required for new account alias")

            session.commit = commit
            return session

    class FakePool:
        def dispose(self):
            disposed["count"] += 1

    class FakeEngine:
        pool = FakePool()

    monkeypatch.setattr(
        repo_module, "get_write_session_local", lambda: ValidationErrorFactory()
    )
    monkeypatch.setattr(
        repo_module, "get_write_engine", lambda: FakeEngine()
    )

    with pytest.raises(ValueError, match="player_id is required"):
        repo_module.SessionRepository().update_config(
            "no_dispose_alias", {"player": {"name": "NoId"}}
        )
    assert disposed["count"] == 0


def test_update_config_does_not_dispose_on_wal_contention(monkeypatch, mock_sleep):
    """WAL 競合はコネクション健全のため dispose しない（再 sync で競合を増幅させない）。"""
    disposed = {"count": 0}
    real_sm = get_session_local()

    class FlakyFactory:
        def __call__(self, *args, **kwargs):
            session = real_sm()

            def commit():
                raise ValueError("wal_insert_begin failed")

            session.commit = commit
            return session

    class FakePool:
        def dispose(self):
            disposed["count"] += 1

    class FakeEngine:
        pool = FakePool()

    monkeypatch.setattr(repo_module, "get_write_session_local", lambda: FlakyFactory())
    monkeypatch.setattr(repo_module, "get_write_engine", lambda: FakeEngine())

    with pytest.raises(ValueError, match="wal_insert_begin failed"):
        repo_module.SessionRepository().update_config(
            "wal_no_dispose_alias", {"player": {"id": "p", "name": "N"}}
        )
    assert disposed["count"] == 0


def test_update_config_dispose_failure_does_not_mask_original(monkeypatch, mock_sleep):
    """dispose 自体が失敗しても元の例外（stream not found）をマスクしない。"""
    real_sm = get_session_local()

    class FlakyFactory:
        def __call__(self, *args, **kwargs):
            session = real_sm()

            def commit():
                raise ValueError("stream not found: 49580f7d:ad779")

            session.commit = commit
            return session

    class ExplodingPool:
        def dispose(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(repo_module, "get_write_session_local", lambda: FlakyFactory())
    monkeypatch.setattr(
        repo_module, "get_write_engine", lambda: type("E", (), {"pool": ExplodingPool()})()
    )

    with pytest.raises(ValueError, match="stream not found"):
        repo_module.SessionRepository().update_config(
            "mask_alias", {"player": {"id": "p", "name": "M"}}
        )
