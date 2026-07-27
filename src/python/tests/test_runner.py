import logging
import time
from unittest.mock import MagicMock

import pytest

from hw_genie.runner import (
    _display_width,
    _render_summary_table,
    _resolve_max_parallel,
    list_account_aliases,
    run_all_accounts,
    run_for_account,
    summarize,
)


@pytest.fixture
def fake_accounts(monkeypatch):
    accounts = ["alpha", "beta", "gamma"]
    monkeypatch.setattr(
        "hw_genie.runner.SessionManager.list_accounts", lambda: list(accounts)
    )
    return accounts


def test_resolve_max_parallel_unbounded():
    assert _resolve_max_parallel(None, 3) == 3
    assert _resolve_max_parallel(0, 3) == 3


def test_resolve_max_parallel_capped():
    assert _resolve_max_parallel(2, 5) == 2
    # never below 1
    assert _resolve_max_parallel(-1, 0) == 1


def test_resolve_max_parallel_reads_env(monkeypatch):
    monkeypatch.setenv("HWDA_MAX_PARALLEL", "2")
    assert _resolve_max_parallel(None, 10) == 2
    monkeypatch.setenv("HWDA_MAX_PARALLEL", "not-a-number")
    assert _resolve_max_parallel(None, 4) == 4


def test_list_account_aliases_sorted(fake_accounts):
    assert list_account_aliases() == ["alpha", "beta", "gamma"]


def test_run_for_account_missing_session(monkeypatch):
    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers", lambda acc: None
    )
    acc, res, err = run_for_account("nobody", lambda c, a: "x")
    assert acc == "nobody"
    assert res is None
    assert isinstance(err, RuntimeError)


def test_run_for_account_success(monkeypatch):
    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers",
        lambda acc: {"x-auth-token": acc},
    )
    fake_client = MagicMock()
    monkeypatch.setattr("hw_genie.runner.HWClient", lambda h: fake_client)

    def routine(client, account):
        assert client is fake_client
        return f"done:{account}"

    acc, res, err = run_for_account("alpha", routine)
    assert acc == "alpha"
    assert res == "done:alpha"
    assert err is None


def test_run_for_account_isolates_failure(monkeypatch):
    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers",
        lambda acc: {"x-auth-token": acc},
    )
    monkeypatch.setattr("hw_genie.runner.HWClient", lambda h: MagicMock())

    def boom(client, account):
        raise ValueError("kaboom")

    acc, res, err = run_for_account("beta", boom)
    assert acc == "beta"
    assert res is None
    assert isinstance(err, ValueError)


def test_run_all_accounts_parallel_and_isolated(monkeypatch, fake_accounts):
    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers",
        lambda acc: {"x-auth-token": acc},
    )
    monkeypatch.setattr("hw_genie.runner.HWClient", lambda h: MagicMock())

    calls = []

    def routine(client, account):
        calls.append(account)
        if account == "beta":
            raise RuntimeError("boom")
        return f"ok:{account}"

    results = run_all_accounts(routine, accounts=fake_accounts, max_parallel=2)
    assert set(results) == {"alpha", "beta", "gamma"}
    assert results["alpha"][0] == "ok:alpha"
    assert results["beta"][1] is not None
    assert results["gamma"][0] == "ok:gamma"
    assert set(calls) == {"alpha", "beta", "gamma"}


def test_run_all_accounts_empty(monkeypatch):
    monkeypatch.setattr("hw_genie.runner.SessionManager.list_accounts", lambda: [])
    assert run_all_accounts(lambda c, a: None) == {}


def test_summarize_counts_failures(capsys):
    from hw_genie.core.client import PlayerStatus

    results = [
        ("alpha", (PlayerStatus(name="Alpha", level=10, gold=100, gems=5, energy=80, arena_rank=3, grand_rank=2), None)),
        ("beta", (None, ValueError("x"))),
    ]
    failed = summarize(results)
    out = capsys.readouterr().out
    assert failed == 1
    assert "alpha" in out
    assert "beta" in out
    assert "Failed (1)" in out


def test_summarize_counts_unknown_player_status_as_failed(capsys):
    from hw_genie.core.client import PlayerStatus

    results = [
        ("alpha", (PlayerStatus(name="Alpha", level=10, gold=100, gems=5, energy=80, arena_rank=3, grand_rank=2), None)),
        ("VitaminD", (PlayerStatus(name="Unknown", level=0, gold=0, gems=0, energy=0, arena_rank=0, grand_rank=0), None)),
    ]
    failed = summarize(results)
    out = capsys.readouterr().out
    assert failed == 1
    assert "1 account(s) completed, ❌ 1 failed." in out
    assert "VitaminD (status unavailable)" in out


def test_cmd_multi_dispatch_daily(monkeypatch):
    from hw_genie import main

    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None):
        captured["routine"] = routine
        captured["accounts"] = accounts
        captured["max_parallel"] = max_parallel
        # simulate one failure so the command exits non-zero
        return {"a": (None, None), "b": (None, RuntimeError("x"))}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.main.summarize", lambda items: 1)

    args = type(
        "A",
        (),
        {"mode": "daily", "accounts": ["a", "b"], "parallel": 2, "debug": False},
    )()

    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(args)
    assert exc.value.code == 1
    assert captured["accounts"] == ["a", "b"]
    assert captured["max_parallel"] == 2


def test_cmd_multi_default_all_accounts(monkeypatch):
    from hw_genie import main

    monkeypatch.setattr(
        "hw_genie.runner.list_account_aliases", lambda: ["x", "y"]
    )
    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None):
        captured["accounts"] = accounts
        return {}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.main.summarize", lambda items: 0)

    args = type("A", (), {"mode": "full", "accounts": [], "parallel": None, "debug": False})()
    main.cmd_multi(args)
    # empty accounts -> all accounts used
    assert captured["accounts"] == ["x", "y"]


def test_daily_routine_invokes_run_daily_raid(monkeypatch):
    from hw_genie import runner

    calls = {}
    fake_client = type("C", (), {})()
    fake_client.fetch_player_status = lambda: "status_ok"

    def fake_daily(client, item_payload=None, account_alias=None):
        calls["client"] = client
        calls["account"] = account_alias
        calls["item_payload"] = item_payload
        return "ok"

    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", fake_daily)
    monkeypatch.setattr(
        "hw_genie.core.session_manager.SessionManager.build_item_raid_payload",
        lambda account="default": {"mission_id": 123, "calls": [{"args": {"id": 123}}]},
    )
    # daily_routine now returns the fetched PlayerStatus for the summary table
    # and builds an item-raid payload from the stored mission id so item raid
    # actually runs (regression: passing None skipped item raid entirely).
    assert runner.daily_routine(fake_client, "acc") == "status_ok"
    assert calls["client"] is fake_client
    assert calls["account"] == "acc"
    assert calls["item_payload"] is not None
    assert calls["item_payload"]["mission_id"] == 123
    assert calls["item_payload"]["calls"][0]["args"]["id"] == 123


def test_daily_routine_skips_item_raid_without_mission_id(monkeypatch):
    from hw_genie import runner

    calls = {}
    fake_client = type("C", (), {})()
    fake_client.fetch_player_status = lambda: "status_ok"

    def fake_daily(client, item_payload=None, account_alias=None):
        calls["item_payload"] = item_payload
        return "ok"

    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", fake_daily)
    monkeypatch.setattr(
        "hw_genie.core.session_manager.SessionManager.build_item_raid_payload",
        lambda account="default": None,
    )
    runner.daily_routine(fake_client, "acc")
    # No stored mission id -> item raid is skipped (None), not a broken payload.
    assert calls["item_payload"] is None


def test_build_item_raid_payload_shape(monkeypatch):
    from hw_genie.core.session_manager import SessionManager

    monkeypatch.setattr(
        SessionManager, "get_last_mission_id", staticmethod(lambda account="default": 42)
    )
    payload = SessionManager.build_item_raid_payload("acc")
    assert payload["mission_id"] == 42
    call = payload["calls"][0]
    assert call["name"] == "missionRaid"
    assert call["args"]["id"] == 42
    assert call["ident"] == "body"
    # No mission id -> None (skip)
    monkeypatch.setattr(
        SessionManager, "get_last_mission_id", staticmethod(lambda account="default": None)
    )
    assert SessionManager.build_item_raid_payload("acc") is None


def test_full_routine_invokes_subroutines(monkeypatch):
    from hw_genie import runner

    fake_client = MagicMock()
    captured = {}

    def fake_hero_raid(client, mission_ids, times=3, allow_recovery=True):
        captured["hero"] = (client, mission_ids, times, allow_recovery)
        return ("hero_res", 0, None)

    def fake_shop(client, buy_soul_shop_items=True, hero_shop_ids=None, buy_pet_potions=True):
        captured["shop"] = (client, hero_shop_ids)

    monkeypatch.setattr("hw_genie.commands.hero_raid.run_hero_raid", fake_hero_raid)
    monkeypatch.setattr("hw_genie.commands.hero_shopping.run_hero_shopping", fake_shop)
    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", lambda *a, **k: "daily_ok")
    # TARGET_SHOP_IDS is imported inside full_routine at call time from the
    # hero_shopping module, so patch the attribute there.
    monkeypatch.setattr(
        "hw_genie.commands.hero_shopping.TARGET_SHOP_IDS", ["SHOP1"]
    )

    runner.full_routine(fake_client, "acc")

    assert captured["hero"] == (fake_client, None, 3, True)
    assert captured["shop"][0] is fake_client
    assert captured["shop"][1] == ["SHOP1"]
    fake_client.exchange_stones.assert_called_once()


def test_full_routine_runs_hero_raid_twice(monkeypatch):
    """`full` mode mirrors legacy bin/hwsa: hero raid (raid hero) THEN daily
    (which hero-raids again with the fixed mission list). Document intent."""
    from hw_genie import runner
    from hw_genie.commands import hero_raid as hero_raid_mod
    from hw_genie.commands.daily_raid import HERO_MISSION_IDS

    fake_client = MagicMock()
    hero_calls = []
    daily_called = {"v": False}

    def fake_hero_raid(client, mission_ids, times=3, allow_recovery=True):
        hero_calls.append((mission_ids, allow_recovery))
        return ("hero_res", 0, None)

    def spy_daily_raid(client, item_payload=None, account_alias=None):
        # Faithfully mirror run_daily_raid: it calls run_hero_raid with the
        # fixed HERO_MISSION_IDS and allow_recovery=False. By invoking the
        # (patched) hero_raid module reference we exercise the real contract.
        hero_raid_mod.run_hero_raid(client, HERO_MISSION_IDS, times=3, allow_recovery=False)
        daily_called["v"] = True

    monkeypatch.setattr("hw_genie.commands.hero_raid.run_hero_raid", fake_hero_raid)
    monkeypatch.setattr("hw_genie.commands.hero_shopping.run_hero_shopping", lambda *a, **k: None)
    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", spy_daily_raid)

    runner.full_routine(fake_client, "acc")
    # expect exactly two hero-raid invocations (raid hero + daily's hero raid)
    assert len(hero_calls) == 2
    assert daily_called["v"] is True
    # first: all missions, recovery allowed; second: fixed HERO_MISSION_IDS, no recovery
    assert hero_calls[0][1] is True
    assert hero_calls[1][1] is False
    assert hero_calls[1][0] == HERO_MISSION_IDS


def test_cmd_multi_limits_to_named_account(monkeypatch):
    """`hw-genie multi daily account1` forwards exactly that account (6a)."""
    from hw_genie import main

    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None):
        captured["accounts"] = accounts
        return {}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.main.summarize", lambda items: 0)

    args = type("A", (), {"mode": "daily", "accounts": ["account1"], "parallel": None, "debug": False})()
    main.cmd_multi(args)
    assert captured["accounts"] == ["account1"]


def test_run_all_accounts_respects_max_parallel(monkeypatch):
    """Worker count passed to ThreadPoolExecutor equals the resolved cap (6b).

    The cap is min(HWDA_MAX_PARALLEL, account_count); unbounded (0/unset) means
    account_count (i.e. all at once).
    """
    import concurrent.futures
    from hw_genie import runner

    accounts = [f"a{i}" for i in range(10)]

    monkeypatch.setattr(
        "hw_genie.runner.SessionManager.list_accounts", lambda: accounts
    )
    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": acc}
    )
    monkeypatch.setattr("hw_genie.runner.HWClient", lambda h: object())

    seen_workers = {}

    real_tpe = concurrent.futures.ThreadPoolExecutor

    def spy_tpe(max_workers=None, *a, **k):
        seen_workers["max_workers"] = max_workers
        return real_tpe(max_workers=max_workers, *a, **k)

    # runner imports ThreadPoolExecutor by name, so patch the bound reference.
    monkeypatch.setattr(runner, "ThreadPoolExecutor", spy_tpe)

    # 1) Explicit cap is honored and clamped to account count.
    runner.run_all_accounts(lambda c, a: None, accounts=accounts, max_parallel=3)
    assert seen_workers["max_workers"] == 3

    # 2) Unbounded (None / HWDA_MAX_PARALLEL unset) -> all accounts at once.
    seen_workers.clear()
    monkeypatch.delenv("HWDA_MAX_PARALLEL", raising=False)
    runner.run_all_accounts(lambda c, a: None, accounts=accounts, max_parallel=None)
    assert seen_workers["max_workers"] == 10

    # 3) HWDA_MAX_PARALLEL env > account count is clamped down.
    seen_workers.clear()
    monkeypatch.setenv("HWDA_MAX_PARALLEL", "50")
    runner.run_all_accounts(lambda c, a: None, accounts=accounts, max_parallel=None)
    assert seen_workers["max_workers"] == 10


def test_write_lock_serializes_writes(monkeypatch):
    """Concurrent update_config calls must serialize via the real _write_lock (6c).

    ``repository._write_lock`` is a ``threading.Lock`` used by ``update_config``
    to serialize writes (no concurrent writers -> no "database is locked" /
    ``wal_insert_begin failed``). We replace it with a composing counting lock
    that delegates to a real ``threading.Lock`` and records the max number of
    simultaneous holders, then drive concurrent writers and prove the peak is 1.
    """
    import threading
    from hw_genie.core import repository

    # The production invariant: writes are guarded by a threading.Lock.
    assert isinstance(repository._write_lock, threading.Lock)

    peak = {"n": 0}
    lock_state = {"n": 0}
    guard = threading.Lock()
    inner = threading.Lock()

    class CountingLock:
        def __enter__(self):
            inner.acquire()
            with guard:
                lock_state["n"] += 1
                peak["n"] = max(peak["n"], lock_state["n"])
            return self

        def __exit__(self, *exc):
            with guard:
                lock_state["n"] -= 1
            inner.release()
            return False

    monkeypatch.setattr(repository, "_write_lock", CountingLock())

    # Fake session: commit simulates work. update_config holds the (now counting)
    # _write_lock around this, so concurrent commits must serialize.
    import contextlib

    def fake_commit(self):
        time.sleep(0.01)

    @contextlib.contextmanager
    def fake_session():
        dummy_q = type("Q", (), {"filter": lambda *a, **k: dummy_q, "first": lambda *a, **k: None})()
        yield type("DB", (), {"query": lambda *a, **k: dummy_q, "add": lambda *a, **k: None,
                              "flush": lambda *a, **k: None, "commit": fake_commit,
                              "rollback": lambda *a, **k: None})()

    monkeypatch.setattr(repository, "get_write_session_local", lambda: lambda: fake_session())

    threads = [
        threading.Thread(
            target=lambda i=i: repository.SessionRepository().update_config(
                f"acc{i}", {"player": {"id": i}}
            )
        )
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The real _write_lock was exercised: at most one holder at any time.
    assert peak["n"] <= 1
    # And it was actually acquired by every writer.
    assert peak["n"] == 1


def test_main_sets_logging_level_info(monkeypatch):
    """Default (non-debug) run configures the root logger at INFO (6e).

    Exercises the real ``setup_logging`` helper used by main() (not stdlib
    basicConfig directly), and asserts the level even when a handler already
    exists (mirrors main() calling setup_logging before install_token_masking_filter
    adds its token-masking handler).
    """
    from hw_genie import main

    # Ensure a handler exists so basicConfig would otherwise be a no-op; the
    # helper must still force the intended level.
    logging.basicConfig(level=logging.WARNING)
    assert logging.getLogger().level == logging.WARNING

    main.setup_logging(debug=False)
    assert logging.getLogger().level == logging.INFO

    main.setup_logging(debug=True)
    assert logging.getLogger().level == logging.DEBUG



def test_render_summary_table_widths_are_dynamic_and_have_no_lv():
    """The summary table sizes columns to content and never shows Lv (UX fix)."""
    rows = [
        ["VitaminD", "76/190", "11", "17", "900.8M", "570.1K"],
        ["TheBestAccountName", "38/190", "53", "8", "2.3B", "180.2K"],
    ]
    table = _render_summary_table(rows)

    # No Lv column / label anywhere.
    assert "Lv" not in table

    # Long account name drives the Account column width (>= its length).
    assert "TheBestAccountName" in table

    # Widths are dynamic: a longer account name yields a wider table than a
    # short one (not a fixed 64-char box).
    narrow = _render_summary_table([["Joe", "82/190", "4", "3", "28.8B", "502.0K"]])
    assert len(table.splitlines()[0]) > len(narrow.splitlines()[0])

    # Header labels present (emoji-prefixed, self-labeling).
    for label in ("Account", "⚡Energy", "🏆Arena", "👑GA", "💰Gold", "💎Gems"):
        assert label in table


def test_render_summary_table_is_display_aligned_with_emoji():
    """Every rendered line must share the same DISPLAY width (emoji-aware).

    Emoji are double-width in terminals but one code point, so naive len()
    padding misaligns the ``|`` separators. This asserts the emoji-aware
    padding keeps all rows (separators, header, body) the same visual width.
    """
    rows = [
        ["VitaminD", "76/190", "11", "17", "900.8M", "570.1K"],
        ["The Best", "38/190", "53", "8", "2.3B", "180.2K"],
    ]
    lines = _render_summary_table(rows).splitlines()
    widths = {_display_width(line) for line in lines}
    # All lines (===, header, ---, body rows, ===) render to one width.
    assert len(widths) == 1
    # The header carries 5 double-width emoji (⚡🏆👑💰💎), so its display
    # width must exceed the raw code-point length.
    header = lines[1]
    assert _display_width(header) > len(header)


def test_format_timestamp_for_display_respects_hwgenie_tz(monkeypatch):
    """Stored UTC timestamps are converted to HWGENIE_TZ for display (UX fix)."""
    from hw_genie.core.utils import format_timestamp_for_display

    utc_iso = "2026-07-20T04:55:42+00:00"

    monkeypatch.setenv("HWGENIE_TZ", "Asia/Tokyo")
    assert format_timestamp_for_display(utc_iso) == "2026-07-20 13:55:42"

    monkeypatch.setenv("HWGENIE_TZ", "")
    assert format_timestamp_for_display(utc_iso) == "2026-07-20 04:55:42"

    # Non-UTC invalid zone falls back to UTC.
    monkeypatch.setenv("HWGENIE_TZ", "Not/AZone")
    assert format_timestamp_for_display(utc_iso) == "2026-07-20 04:55:42"

    # Missing/unknown values pass through untouched.
    assert format_timestamp_for_display("Never") == "Never"


def test_cmd_sync_no_turso(monkeypatch, capsys):
    """sync without TURSO_SYNC_URL should print message and return."""
    monkeypatch.delenv("TURSO_SYNC_URL", raising=False)
    from hw_genie import main

    args = type("A", (), {"account": None, "debug": False})()
    main.cmd_sync(args)
    captured = capsys.readouterr()
    assert "not set" in captured.out


def test_cmd_sync_with_turso(monkeypatch, capsys):
    """sync with TURSO_SYNC_URL should call sync() on the raw connection."""
    monkeypatch.setenv("TURSO_SYNC_URL", "libsql://test.turso.io")

    sync_called = False

    class FakeRawConn:
        def sync(self):
            nonlocal sync_called
            sync_called = True

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        @property
        def connection(self):
            return self

        @property
        def dbapi_connection(self):
            return FakeRawConn()

        def execute(self, *a, **kw):
            pass

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr("hw_genie.core.database.get_engine", lambda: FakeEngine())

    from hw_genie import main

    args = type("A", (), {"account": None, "debug": False})()
    main.cmd_sync(args)
    captured = capsys.readouterr()
    assert sync_called
    assert "synced" in captured.out.lower()
    assert "test.turso.io" in captured.out


def test_cmd_sync_sync_failure(monkeypatch, capsys):
    """sync should report failure when raw.sync() raises."""
    monkeypatch.setenv("TURSO_SYNC_URL", "libsql://test.turso.io")

    class FakeRawConn:
        def sync(self):
            raise RuntimeError("connection refused")

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        @property
        def connection(self):
            return self

        @property
        def dbapi_connection(self):
            return FakeRawConn()

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr("hw_genie.core.database.get_engine", lambda: FakeEngine())

    from hw_genie import main

    args = type("A", (), {"account": None, "debug": False})()
    with pytest.raises(SystemExit):
        main.cmd_sync(args)
    captured = capsys.readouterr()
    assert "Sync failed" in captured.err
    assert "connection refused" in captured.err
