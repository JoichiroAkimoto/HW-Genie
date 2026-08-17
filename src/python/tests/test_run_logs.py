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
        hostname=kwargs.get("hostname"),
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


def test_build_run_log_summary_quests_failure():
    from hw_genie.main import _build_run_log_summary

    results = {
        "Joe": ((["q1"], ["q2"], []), None),
        "Ace": ((["q1"], [], []), None),
        "Bug": (([], [], []), RuntimeError("auth failed")),
    }
    entries, error_summary = _build_run_log_summary("quests", results)
    assert entries == [
        {"account": "Joe", "ok": False, "error": "1 quest(s) failed"},
        {"account": "Ace", "ok": True, "error": None},
        {"account": "Bug", "ok": False, "error": "auth failed"},
    ]
    assert error_summary == (
        "2 account(s) failed: Joe (1 quest(s) failed), Bug (auth failed)"
    )


def test_build_run_log_summary_consumable_failure():
    from hw_genie.core.client import ResponseStatus
    from hw_genie.core.inventory import ConsumableUseResult
    from hw_genie.main import _build_run_log_summary

    results = {
        "Joe": ([ConsumableUseResult(lib_id=1, status=ResponseStatus.SUCCESS)], None),
        "Ace": ([ConsumableUseResult(lib_id=2, status=ResponseStatus.ERROR)], None),
        "Bug": ([ConsumableUseResult(lib_id=3, status=ResponseStatus.UNEXPECTED)], None),
    }
    entries, error_summary = _build_run_log_summary("consumable", results)
    assert entries[0]["ok"] is True
    assert entries[1] == {
        "account": "Ace",
        "ok": False,
        "error": "1 consumable use(s) failed",
    }
    assert entries[2] == {
        "account": "Bug",
        "ok": False,
        "error": "1 consumable use(s) failed",
    }
    assert error_summary == (
        "2 account(s) failed: Ace (1 consumable use(s) failed), "
        "Bug (1 consumable use(s) failed)"
    )


def test_build_run_log_summary_asgard_failure():
    from hw_genie.commands.asgard_shop import AsgardResult, AsgardRunResult
    from hw_genie.core.client import ResponseStatus
    from hw_genie.main import _build_run_log_summary

    ok = AsgardRunResult(
        coins=1, spent=0, remaining=1, bought=0, skipped=False, items=[]
    )
    fetch_err = AsgardRunResult(
        coins=1, spent=0, remaining=1, bought=0, skipped=False, items=[],
        error="fetch boom",
    )
    purchase_err = AsgardRunResult(
        coins=1, spent=0, remaining=1, bought=0, skipped=False,
        items=[AsgardResult(action="x", status=ResponseStatus.ERROR)],
    )
    results = {"Joe": (ok, None), "Ace": (fetch_err, None), "Bug": (purchase_err, None)}
    entries, error_summary = _build_run_log_summary("asgard-shop", results)
    assert entries[0]["ok"] is True
    assert entries[1] == {"account": "Ace", "ok": False, "error": "shop fetch failed: fetch boom"}
    assert entries[2] == {"account": "Bug", "ok": False, "error": "1 purchase error(s)"}
    assert error_summary == (
        "2 account(s) failed: Ace (shop fetch failed: fetch boom), "
        "Bug (1 purchase error(s))"
    )


def test_build_run_log_summary_daily_status_unavailable():
    from hw_genie.core.client import PlayerStatus
    from hw_genie.main import _build_run_log_summary

    results = {
        "Joe": (PlayerStatus(name="Alpha", level=130), None),
        "Ace": (None, None),
        "Bug": (PlayerStatus(name="Unknown", level=0), None),
    }
    entries, error_summary = _build_run_log_summary("daily", results)
    assert entries[0]["ok"] is True
    assert entries[1] == {"account": "Ace", "ok": False, "error": "status unavailable"}
    assert entries[2] == {"account": "Bug", "ok": False, "error": "status unavailable"}
    assert error_summary == (
        "2 account(s) failed: Ace (status unavailable), Bug (status unavailable)"
    )


def test_light_migration_adds_missing_column():
    """既存 DB（hostname 列なし）にマイグレーションで列が追加される。"""
    from sqlalchemy import text

    from hw_genie.core.database import (
        Base,
        _apply_light_migrations,
        get_engine,
    )

    eng = get_engine()
    # setup_db が hostname 込みで run_logs を作るため、hostname なしの
    # 旧スキーマを再現する。
    Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE run_logs ("
                "id INTEGER PRIMARY KEY, mode VARCHAR NOT NULL, status VARCHAR NOT NULL)"
            )
        )
    _apply_light_migrations(eng)
    with eng.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(run_logs)"))}
    assert "hostname" in columns
    _apply_light_migrations(eng)  # 冪等


def test_record_hostname():
    run_id = _record(hostname="ak@ak-mac")
    row = get_run_log(run_id)
    assert row is not None
    assert row.hostname == "ak@ak-mac"


def test_run_host_identifier_explicit():
    """HWGENIE_HOST による明示上書きが最優先される。"""
    from hw_genie.main import _run_host_identifier

    with patch.dict("os.environ", {"HWGENIE_HOST": "win-pc"}, clear=True):
        assert _run_host_identifier() == "win-pc"


def test_run_host_identifier_compose_env():
    """Docker Compose 経由（HWGENIE_USER / HWGENIE_MACHINE）を優先する。"""
    from hw_genie.main import _run_host_identifier

    with patch.dict(
        "os.environ",
        {"HWGENIE_USER": "joe", "HWGENIE_MACHINE": "WIN-PC"},
        clear=True,
    ):
        assert _run_host_identifier() == "joe@WIN-PC"


def test_run_host_identifier_unix_env():
    """ホスト直接実行時は USER / HOSTNAME（Unix 標準）を使う。"""
    from hw_genie.main import _run_host_identifier

    with patch.dict(
        "os.environ",
        {"USER": "ak", "HOSTNAME": "ak-mac"},
        clear=True,
    ):
        assert _run_host_identifier() == "ak@ak-mac"


def test_run_host_identifier_windows_env():
    """Windows の USERNAME / COMPUTERNAME にも対応する。"""
    from hw_genie.main import _run_host_identifier

    with patch.dict(
        "os.environ",
        {"USERNAME": "joe", "COMPUTERNAME": "WIN-PC"},
        clear=True,
    ):
        assert _run_host_identifier() == "joe@WIN-PC"


def test_run_host_identifier_fallback():
    """環境変数が無い場合も getpass / socket でフォールバックする。"""
    import getpass
    import socket

    from hw_genie.main import _run_host_identifier

    with patch.dict("os.environ", {}, clear=True):
        assert _run_host_identifier() == (
            f"{getpass.getuser()}@{socket.gethostname()}"
        )


def test_run_host_identifier_getpass_failure():
    """getpass が失敗する環境（Docker --user 等）でもクラッシュせず unknown になる。"""
    import socket

    from hw_genie.main import _run_host_identifier

    with patch("getpass.getuser", side_effect=OSError("no passwd entry")):
        with patch.dict("os.environ", {}, clear=True):
            value = _run_host_identifier()
    assert value == f"unknown@{socket.gethostname()}"


def test_output_capture_inherits_existing_filters():
    """既存ハンドラの TokenMaskingFilter がキャプチャ内ログにも適用される。"""
    from hw_genie.core.database import install_token_masking_filter
    from hw_genie.core.run_log import OutputCapture

    root = logging.getLogger()
    previous_level = root.level
    previous_handlers = list(root.handlers)
    root.setLevel(logging.INFO)
    try:
        if not root.handlers:
            logging.basicConfig()
        install_token_masking_filter()
        capture = OutputCapture()
        with capture:
            logging.getLogger("hw_genie.tests").info(
                "connecting to sqlite:///x?auth_token=SECRET123"
            )
        assert "SECRET123" not in capture.getvalue()
    finally:
        root.setLevel(previous_level)
        root.handlers = previous_handlers
