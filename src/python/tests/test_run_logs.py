import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hw_genie.core.database import RunLog, get_write_session_local
from hw_genie.core.run_log import (
    OutputCapture,
    get_run_log,
    list_run_logs,
    record_run_log,
    strip_ansi,
)


def _run_entry(account: str, ok: bool = True, error: str | None = None) -> dict:
    return {"account": account, "ok": ok, "error": error}


def _record(now=None, **kwargs):
    now = now or datetime.now(timezone.utc)
    return record_run_log(
        started_at=now,
        finished_at=now + timedelta(seconds=10),
        mode=kwargs.get("mode", "daily"),
        status=kwargs.get("status", "ok"),
        exit_code=kwargs.get("exit_code", 0),
        accounts=kwargs.get("accounts", [_run_entry("Joe")]),
        error_summary=kwargs.get("error_summary"),
        log_text=kwargs.get("log_text", "line1\nline2\n"),
        log_file=kwargs.get("log_file"),
    )


def _insert_raw(now: datetime, log_text: str = "old"):
    """run_logs に直接レコードを差し込む（prune テスト用の古い行の作成）。"""
    with get_write_session_local()() as db:
        row = RunLog(
            started_at=now.replace(tzinfo=None),
            finished_at=now.replace(tzinfo=None),
            mode="daily",
            status="ok",
            exit_code=0,
            accounts=[_run_entry("Joe")],
            error_summary=None,
            log_text=log_text,
            log_file=None,
        )
        db.add(row)
        db.commit()
        return row.id


def test_strip_ansi():
    assert strip_ansi("\x1b[31mred\x1b[0m plain") == "red plain"
    assert strip_ansi("no ansi") == "no ansi"


def test_record_and_list():
    run_id = _record(log_file="data/logs/hwda_20260812_013936.log")
    assert run_id is not None
    rows = list_run_logs(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == run_id
    assert row.mode == "daily"
    assert row.status == "ok"
    assert row.exit_code == 0
    assert row.accounts == [{"account": "Joe", "ok": True, "error": None}]
    assert row.error_summary is None
    assert row.log_text == "line1\nline2\n"
    assert row.log_file == "data/logs/hwda_20260812_013936.log"


def test_record_failed_with_error_summary():
    run_id = _record(
        status="failed",
        exit_code=1,
        accounts=[
            _run_entry("Joe", ok=False, error="Auth token expired"),
            _run_entry("The Best"),
        ],
        error_summary="1 account(s) failed: Joe (Auth token expired)",
    )
    row = get_run_log(run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.exit_code == 1
    assert row.error_summary == "1 account(s) failed: Joe (Auth token expired)"


def test_get_run_log_missing():
    assert get_run_log(9999) is None


def test_prune_older_than_keep_days():
    now = datetime.now(timezone.utc)
    old_id = _insert_raw(now - timedelta(days=10))
    new_id = _insert_raw(now - timedelta(days=1))
    with patch.dict("os.environ", {"HW_LOG_KEEP_DAYS": "7"}):
        fresh_id = _record(now=now)
    assert old_id is not None and new_id is not None and fresh_id is not None
    ids = {r.id for r in list_run_logs(limit=10)}
    assert old_id not in ids
    assert new_id in ids
    assert fresh_id in ids


def test_prune_disabled_when_zero():
    now = datetime.now(timezone.utc)
    old_id = _insert_raw(now - timedelta(days=30))
    with patch.dict("os.environ", {"HW_LOG_KEEP_DAYS": "0"}):
        _record(now=now)
    assert old_id is not None
    assert old_id in {r.id for r in list_run_logs(limit=10)}


def test_output_capture_captures_print_and_logging():
    # 本番は main.setup_logging が root を INFO に設定する。テストでは未設定
    # （デフォルト WARNING）のため INFO レコードが root まで届かない。
    # レベルは前後のテストに影響しないよう復元する。
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        capture = OutputCapture()
        with capture:
            print("hello")
            logging.getLogger("hw_genie.tests").info("world")
        text = capture.getvalue()
        assert "hello" in text
        assert "world" in text
    finally:
        root.setLevel(previous_level)


def test_output_capture_strips_ansi():
    capture = OutputCapture()
    with capture:
        print("\x1b[32mgreen\x1b[0m")
    text = capture.getvalue()
    assert "\x1b[" not in text
    assert "green" in text


def test_output_capture_restores_stdout():
    import sys

    original = sys.stdout
    capture = OutputCapture()
    with capture:
        assert sys.stdout is not original
    assert sys.stdout is original


def test_record_best_effort_on_db_error():
    import hw_genie.core.run_log as run_log

    def boom():
        raise RuntimeError("boom")

    with patch.object(run_log, "get_write_session_local", boom):
        assert (
            record_run_log(
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                mode="daily",
                status="ok",
                exit_code=0,
                accounts=[_run_entry("Joe")],
                error_summary=None,
                log_text=None,
            )
            is None
        )