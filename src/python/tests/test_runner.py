import logging
import time
from unittest.mock import MagicMock

import pytest

from hw_genie.runner import (
    _display_width,
    _render_summary_table,
    resolve_max_parallel,
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
    assert resolve_max_parallel(None, 3) == 3
    assert resolve_max_parallel(0, 3) == 3


def test_resolve_max_parallel_capped():
    assert resolve_max_parallel(2, 5) == 2
    # never below 1
    assert resolve_max_parallel(-1, 0) == 1


def test_resolve_max_parallel_reads_env(monkeypatch):
    monkeypatch.setenv("HW_MAX_PARALLEL", "2")
    assert resolve_max_parallel(None, 10) == 2
    monkeypatch.setenv("HW_MAX_PARALLEL", "not-a-number")
    assert resolve_max_parallel(None, 4) == 4


def test_list_account_aliases_registration_order(fake_accounts):
    """登録順（list_accounts の返り値）をそのまま返す（ソートしない）。"""
    assert list_account_aliases() == ["alpha", "beta", "gamma"]


def test_list_account_aliases_not_alphabetically_sorted(monkeypatch):
    """アルファベット順でない登録順もそのまま維持される。"""
    monkeypatch.setattr(
        "hw_genie.runner.SessionManager.list_accounts",
        lambda: ["zulu", "alpha", "mike"],
    )
    assert list_account_aliases() == ["zulu", "alpha", "mike"]


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
        ("Bob", (PlayerStatus(name="Unknown", level=0, gold=0, gems=0, energy=0, arena_rank=0, grand_rank=0), None)),
    ]
    failed = summarize(results)
    out = capsys.readouterr().out
    assert failed == 1
    assert "1 account(s) completed, ❌ 1 failed." in out
    assert "Bob (status unavailable)" in out


def test_run_all_accounts_orders_results_by_submission(monkeypatch):
    """完了順が投入順と異なっても、結果は投入順（登録順）で返る。"""
    accounts = ["zulu", "alpha", "mike"]
    monkeypatch.setattr("hw_genie.runner.SessionManager.list_accounts", lambda: accounts)
    monkeypatch.setattr(
        "hw_genie.runner.run_for_account",
        lambda acc, routine: (acc, f"{acc}-ok", None),
    )
    # 完了順が投入順の逆になるよう as_completed をモックする
    monkeypatch.setattr(
        "hw_genie.runner.as_completed", lambda futures: reversed(list(futures))
    )
    results = run_all_accounts(lambda c, a: None, accounts=accounts)
    assert list(results) == ["zulu", "alpha", "mike"]
    assert results["zulu"] == ("zulu-ok", None)


def test_summarize_heading_width_matches_table(capsys, monkeypatch):
    """見出し・失敗一覧の罫線幅はテーブルの罫線幅と一致する。"""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    from hw_genie.core.client import PlayerStatus
    from hw_genie.core.utils import display_width

    results = [
        ("alpha", (PlayerStatus(name="Alpha", level=130, gold=1000000, gems=5000, energy=80, arena_rank=3, grand_rank=2), None)),
        ("beta", (None, ValueError("x"))),
    ]
    summarize(results)
    lines = capsys.readouterr().out.splitlines()
    # \n の直後の見出し上罫線（= のみで構成される全罫線の代表）
    eq_lines = [line for line in lines if line and set(line) == {"="}]
    dash_lines = [line for line in lines if line and set(line) == {"-"}]
    assert eq_lines, "見出し/テーブルの罫線が存在する"
    assert dash_lines, "失敗一覧の罫線が存在する"
    widths = {len(line) for line in eq_lines + dash_lines}
    assert len(widths) == 1, f"全罫線が同幅であること（{widths}）"
    rule_width = widths.pop()
    # ヘッダー行の表示幅も罫線幅に一致する
    header_line = next(line for line in lines if "Account" in line)
    assert display_width(header_line) == rule_width


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


def test_quests_routine_calls_run_quest_execute(monkeypatch):
    """quests_routine wraps run_quest_execute non-interactively."""
    from hw_genie import runner

    calls = {}
    fake_client = object()

    def fake_execute(client, account_alias=None, dry_run=False, confirm=False):
        calls["client"] = client
        calls["account_alias"] = account_alias
        calls["dry_run"] = dry_run
        calls["confirm"] = confirm
        return ([{"account": account_alias, "quest_id": 10024, "quest_name": "x"}], [], [])

    monkeypatch.setattr("hw_genie.commands.quests.run_quest_execute", fake_execute)

    routine = runner.quests_routine()
    result = routine(fake_client, "alpha")

    assert calls == {
        "client": fake_client,
        "account_alias": "alpha",
        "dry_run": False,
        "confirm": True,
    }
    assert result[0][0]["quest_id"] == 10024
    assert result[1] == []
    assert result[2] == []


def test_quests_routine_forwards_dry_run(monkeypatch):
    from hw_genie import runner

    calls = {}

    def fake_execute(client, account_alias=None, dry_run=False, confirm=False):
        calls["dry_run"] = dry_run
        return ([], [], [])

    monkeypatch.setattr("hw_genie.commands.quests.run_quest_execute", fake_execute)
    runner.quests_routine(dry_run=True)(object(), "alpha")
    assert calls["dry_run"] is True


def test_daily_routine_runs_quest_completion(monkeypatch):
    """daily_routine completes enabled quests without failing the run."""
    from hw_genie import runner

    calls = {}
    fake_client = type("C", (), {})()
    fake_client.fetch_player_status = lambda: "status_ok"

    def fake_daily(client, item_payload=None, account_alias=None, item_max_iterations=9999):
        return "ok"

    def fake_quests(client, account_alias=None, dry_run=False, confirm=False):
        calls["quests"] = (account_alias, dry_run, confirm)
        # quest failures are reported by the command, not raised
        return ([], [{"account": account_alias, "quest_id": 1, "quest_name": "q", "step": "x", "error": "boom"}], [])

    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", fake_daily)
    monkeypatch.setattr("hw_genie.commands.quests.run_quest_execute", fake_quests)
    monkeypatch.setattr(
        "hw_genie.core.session_manager.SessionManager.build_item_raid_payload",
        lambda account="default": None,
    )

    assert runner.daily_routine(fake_client, "acc") == "status_ok"
    assert calls["quests"] == ("acc", False, True)


def test_summarize_quests_counts_failures(capsys):
    from hw_genie.runner import summarize_quests

    results = [
        ("alpha", (([{"quest_id": 10024}], [], []), None)),
        ("beta", (([], [{"quest_id": 10028, "error": "bought"}], []), None)),
        ("gamma", (None, ValueError("x"))),
    ]
    failed = summarize_quests(results)
    out = capsys.readouterr().out
    assert failed == 2
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    assert "Multi-quest summary" in out
    assert "Failed (2)" in out
    # ok counts only accounts without quest failures (alpha only)
    assert "1 account(s) completed, ❌ 2 failed." in out


def test_summarize_quests_shows_skipped_column(capsys):
    """Skipped (quest_defaults disabled) quests appear as a count column."""
    from hw_genie.runner import summarize_quests

    results = [
        ("alpha", (([{"quest_id": 10024}], [], [10007, 10028]), None)),
        ("beta", (([], [], [10007]), None)),
    ]
    failed = summarize_quests(results)
    out = capsys.readouterr().out
    assert failed == 0
    assert "⏭️ Skipped" in out
    assert "2 account(s) completed, ❌ 0 failed." in out


def test_summarize_quests_unavailable_result_marks_failed(capsys):
    """A routine result that is not a (succeeded, failed, skipped) triple is a failure."""
    from hw_genie.runner import summarize_quests

    failed = summarize_quests([("alpha", ("unexpected", None))])
    out = capsys.readouterr().out
    assert failed == 1
    assert "alpha (quest result unavailable)" in out
    assert "0 account(s) completed, ❌ 1 failed." in out


def test_summarize_quests_dry_run_uses_planned(capsys):
    """dry-run サマリは completed ではなく planned と表示する（何も実行していないため）。"""
    from hw_genie.runner import summarize_quests

    results = [
        ("alpha", (([{"quest_id": 10024}], [], []), None)),
        ("beta", (([], [{"quest_id": 10028, "error": "bought"}], []), None)),
    ]
    failed = summarize_quests(results, dry_run=True)
    out = capsys.readouterr().out
    assert failed == 1
    assert "1 account(s) planned, ❌ 1 failed." in out
    assert "completed" not in out


def test_cmd_multi_quests_success_exits_zero(monkeypatch):
    """multi quests with no failed quests exits 0."""
    from hw_genie import main

    monkeypatch.setattr("hw_genie.main.run_all_accounts", lambda *a, **k: {})
    monkeypatch.setattr("hw_genie.runner.summarize_quests", lambda items, dry_run=False: 0)

    args = type("A", (), {"mode": "quests", "accounts": [], "parallel": None, "debug": False})()
    main.cmd_multi(args)  # must not raise SystemExit


def test_cmd_multi_dry_run_rejected_outside_quests(monkeypatch):
    """--dry-run with daily/full is rejected: those modes always execute."""
    from hw_genie import main

    for mode in ("daily", "full"):
        args = type("A", (), {"mode": mode, "accounts": [], "parallel": None, "debug": False, "dry_run": True})()
        with pytest.raises(SystemExit) as exc:
            main.cmd_multi(args)
        assert exc.value.code == 2


def test_cmd_multi_quests_mode_exits_on_failure(monkeypatch):
    """multi quests routes to quests_routine + summarize_quests and exits 1."""
    from hw_genie import main

    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None):
        captured["routine"] = routine
        return {"a": ((1, 2), None)}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    # cmd_multi imports summarize_quests inside the function, so patch the
    # runner module (the import source) rather than main's namespace.
    monkeypatch.setattr("hw_genie.runner.summarize_quests", lambda items, dry_run=False: 1)

    args = type("A", (), {"mode": "quests", "accounts": [], "parallel": None, "debug": False})()
    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(args)
    assert exc.value.code == 1
    assert callable(captured["routine"])


def test_cmd_multi_records_run_log_ok(monkeypatch):
    """cmd_multi は成功時に status=ok / error_summary=None で run_logs に記録する。"""
    from hw_genie import main
    from hw_genie.core.client import PlayerStatus

    records = {}

    def fake_record(**kwargs):
        records.update(kwargs)
        return 1

    def fake_run(routine, accounts=None, max_parallel=None):
        print("progress line")
        return {"a": (PlayerStatus(name="Alpha", level=130), None)}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.main.summarize", lambda items: 0)
    # cmd_multi imports record_run_log inside the function, so patch the
    # run_log module (the import source) rather than main's namespace.
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", fake_record)

    args = type("A", (), {"mode": "daily", "accounts": ["a"], "parallel": 1, "debug": False})()
    main.cmd_multi(args)

    assert records["mode"] == "daily"
    assert records["status"] == "ok"
    assert records["exit_code"] == 0
    assert records["accounts"] == [{"account": "a", "ok": True, "error": None}]
    assert records["error_summary"] is None
    assert "progress line" in records["log_text"]


def test_cmd_multi_records_run_log_business_failure(monkeypatch):
    """quests の失敗は例外なしでも failed として per-account に記録される。"""
    from hw_genie import main

    records = {}

    def fake_record(**kwargs):
        records.update(kwargs)
        return 1

    monkeypatch.setattr(
        "hw_genie.main.run_all_accounts",
        lambda routine, accounts=None, max_parallel=None: {
            "a": ((["q1"], ["q10024"], []), None)
        },
    )
    monkeypatch.setattr("hw_genie.runner.summarize_quests", lambda items, dry_run=False: 1)
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", fake_record)

    args = type("A", (), {"mode": "quests", "accounts": ["a"], "parallel": 1, "debug": False, "dry_run": False})()
    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(args)
    assert exc.value.code == 1

    assert records["status"] == "failed"
    assert records["exit_code"] == 1
    assert records["accounts"] == [
        {"account": "a", "ok": False, "error": "1 quest(s) failed"}
    ]
    assert records["error_summary"] == "1 account(s) failed: a (1 quest(s) failed)"


def test_cmd_multi_records_run_log_on_exception(monkeypatch):
    """run_all_accounts が例外を投げたら failed で記録し、例外は再送出される。"""
    from hw_genie import main

    records = {}

    def fake_record(**kwargs):
        records.update(kwargs)
        return None

    def boom(routine, accounts=None, max_parallel=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("hw_genie.main.run_all_accounts", boom)
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", fake_record)

    args = type("A", (), {"mode": "daily", "accounts": ["a"], "parallel": 1, "debug": False})()
    with pytest.raises(RuntimeError):
        main.cmd_multi(args)

    assert records["status"] == "failed"
    assert records["exit_code"] == 1
    assert records["error_summary"] == "boom"
    assert "Traceback" in records["log_text"]


def test_cmd_multi_records_run_log_on_interrupt(monkeypatch):
    """KeyboardInterrupt は exit_code=130 で failed 記録され、再送出される。"""
    from hw_genie import main

    records = {}

    def fake_record(**kwargs):
        records.update(kwargs)
        return None

    def interrupt(routine, accounts=None, max_parallel=None):
        raise KeyboardInterrupt

    monkeypatch.setattr("hw_genie.main.run_all_accounts", interrupt)
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", fake_record)

    args = type("A", (), {"mode": "daily", "accounts": ["a"], "parallel": 1, "debug": False})()
    with pytest.raises(KeyboardInterrupt):
        main.cmd_multi(args)

    assert records["status"] == "failed"
    assert records["exit_code"] == 130


def test_cmd_multi_quests_reads_dry_run_flag(monkeypatch):
    """--dry-run is forwarded to quests_routine (getattr keeps old args safe)."""

    captured = {}

    def fake_builder(dry_run=False):
        captured["dry_run"] = dry_run
        return lambda c, a: ([], [])

    monkeypatch.setattr("hw_genie.runner.quests_routine", fake_builder)
    monkeypatch.setattr("hw_genie.main.run_all_accounts", lambda *a, **k: {})
    monkeypatch.setattr("hw_genie.runner.summarize_quests", lambda items, dry_run=False: 0)

    # args without dry_run attribute (legacy shape) -> default False
    from hw_genie import main

    args = type("A", (), {"mode": "quests", "accounts": [], "parallel": None, "debug": False})()
    main.cmd_multi(args)
    assert captured["dry_run"] is False

    # args WITH dry_run=True -> forwarded
    args2 = type("A", (), {"mode": "quests", "accounts": [], "parallel": None, "debug": False, "dry_run": True})()
    main.cmd_multi(args2)
    assert captured["dry_run"] is True


def test_cmd_multi_quests_dry_run_runs_sequentially(monkeypatch):
    """multi quests --dry-run runs accounts sequentially to keep the plan order readable."""
    from hw_genie import main

    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None):
        captured["max_parallels"].append(max_parallel)
        return {}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.runner.quests_routine", lambda dry_run=False: lambda c, a: ([], [], []))
    monkeypatch.setattr("hw_genie.runner.summarize_quests", lambda items, dry_run=False: 0)

    captured["max_parallels"] = []
    args = type("A", (), {"mode": "quests", "accounts": [], "parallel": 4, "debug": False, "dry_run": True})()
    main.cmd_multi(args)
    assert captured["max_parallels"] == [1]

    args2 = type("A", (), {"mode": "quests", "accounts": [], "parallel": 4, "debug": False, "dry_run": False})()
    main.cmd_multi(args2)
    assert captured["max_parallels"] == [1, 4]


def test_daily_routine_invokes_run_daily_raid(monkeypatch):
    from hw_genie import runner

    calls = {}
    fake_client = type("C", (), {})()
    fake_client.fetch_player_status = lambda: "status_ok"

    def fake_daily(client, item_payload=None, account_alias=None, item_max_iterations=9999):
        calls["client"] = client
        calls["account"] = account_alias
        calls["item_payload"] = item_payload
        return "ok"

    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", fake_daily)
    monkeypatch.setattr(
        "hw_genie.commands.quests.run_quest_execute",
        lambda *a, **k: ([], []),
    )
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


def test_daily_routine_threads_item_max_iterations(monkeypatch):
    from hw_genie import runner

    fake_client = type("C", (), {})()
    fake_client.fetch_player_status = lambda: "status_ok"
    captured = {}

    def fake_daily(client, item_payload=None, account_alias=None, item_max_iterations=9999):
        captured["item_max_iterations"] = item_max_iterations
        return "ok"

    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", fake_daily)
    monkeypatch.setattr(
        "hw_genie.commands.quests.run_quest_execute", lambda *a, **k: ([], [])
    )
    monkeypatch.setattr(
        "hw_genie.core.session_manager.SessionManager.build_item_raid_payload",
        lambda account="default": {"mission_id": 123, "calls": [{"args": {"id": 123}}]},
    )

    runner.daily_routine(fake_client, "acc", item_max_iterations=7)
    assert captured["item_max_iterations"] == 7


def test_full_routine_threads_item_max_iterations(monkeypatch):
    from hw_genie import runner

    fake_client = MagicMock()

    def fake_hero_raid(client, mission_ids, times=3, allow_recovery=True):
        return ("hero_res", 0, None)

    def fake_shop(client, buy_soul_shop_items=True, hero_shop_ids=None, buy_pet_potions=True):
        pass

    captured = {}

    def fake_daily(client, item_payload=None, account_alias=None, item_max_iterations=9999):
        captured["item_max_iterations"] = item_max_iterations
        return "ok"

    monkeypatch.setattr("hw_genie.commands.hero_raid.run_hero_raid", fake_hero_raid)
    monkeypatch.setattr("hw_genie.commands.hero_shopping.run_hero_shopping", fake_shop)
    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", fake_daily)
    monkeypatch.setattr(
        "hw_genie.commands.quests.run_quest_execute", lambda *a, **k: ([], [])
    )
    monkeypatch.setattr(
        "hw_genie.commands.hero_shopping.TARGET_SHOP_IDS", ["SHOP1"]
    )

    runner.full_routine(fake_client, "acc", item_max_iterations=3)
    assert captured["item_max_iterations"] == 3


def test_daily_routine_skips_item_raid_without_mission_id(monkeypatch):
    from hw_genie import runner

    calls = {}
    fake_client = type("C", (), {})()
    fake_client.fetch_player_status = lambda: "status_ok"

    def fake_daily(client, item_payload=None, account_alias=None, item_max_iterations=9999):
        calls["item_payload"] = item_payload
        return "ok"

    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", fake_daily)
    monkeypatch.setattr(
        "hw_genie.commands.quests.run_quest_execute",
        lambda *a, **k: ([], []),
    )
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
    monkeypatch.setattr(
        "hw_genie.commands.quests.run_quest_execute",
        lambda *a, **k: ([], []),
    )
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

    def spy_daily_raid(client, item_payload=None, account_alias=None, item_max_iterations=9999):
        # Faithfully mirror run_daily_raid: it calls run_hero_raid with the
        # fixed HERO_MISSION_IDS and allow_recovery=False. By invoking the
        # (patched) hero_raid module reference we exercise the real contract.
        hero_raid_mod.run_hero_raid(client, HERO_MISSION_IDS, times=3, allow_recovery=False)
        daily_called["v"] = True

    monkeypatch.setattr("hw_genie.commands.hero_raid.run_hero_raid", fake_hero_raid)
    monkeypatch.setattr("hw_genie.commands.hero_shopping.run_hero_shopping", lambda *a, **k: None)
    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", spy_daily_raid)
    monkeypatch.setattr(
        "hw_genie.commands.quests.run_quest_execute",
        lambda *a, **k: ([], []),
    )

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

    The cap is min(HW_MAX_PARALLEL, account_count); unbounded (0/unset) means
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

    # 2) Unbounded (None / HW_MAX_PARALLEL unset) -> all accounts at once.
    seen_workers.clear()
    monkeypatch.delenv("HW_MAX_PARALLEL", raising=False)
    runner.run_all_accounts(lambda c, a: None, accounts=accounts, max_parallel=None)
    assert seen_workers["max_workers"] == 10

    # 3) HW_MAX_PARALLEL env > account count is clamped down.
    seen_workers.clear()
    monkeypatch.setenv("HW_MAX_PARALLEL", "50")
    runner.run_all_accounts(lambda c, a: None, accounts=accounts, max_parallel=None)
    assert seen_workers["max_workers"] == 10


def test_write_lock_serializes_writes(monkeypatch):
    """Concurrent update_config calls must serialize via the real _wal_io_lock (6c).

    ``repository._wal_io_lock`` is the process-wide lock (shared with the
    on-connect ``sync()``) used by ``update_config`` to serialize writes
    (no concurrent writers -> no "database is locked" / ``wal_insert_begin
    failed``). We replace it with a composing counting lock that delegates to a
    real ``threading.Lock`` and records the max number of simultaneous holders,
    then drive concurrent writers and prove the peak is 1.
    """
    import threading
    from hw_genie.core import database as db_module
    from hw_genie.core import repository

    # The production invariant: update_config and the on-connect sync share the
    # same process-wide lock (RLock -- reentrant, so a write transaction may
    # open its own connection whose sync() re-acquires it).
    assert repository._wal_io_lock is db_module._wal_io_lock
    lock = repository._wal_io_lock
    lock.acquire()
    lock.acquire()  # reentrant: a plain Lock would deadlock here
    lock.release()
    lock.release()

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

    monkeypatch.setattr(repository, "_wal_io_lock", CountingLock())

    # Fake session: commit simulates work. update_config holds the (now counting)
    # _wal_io_lock around this, so concurrent commits must serialize.
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

    # The real _wal_io_lock was exercised: at most one holder at any time.
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
        ["Alice", "76/190", "11", "17", "900.8M", "570.1K"],
        ["AVeryLongTestAccountName", "38/190", "53", "8", "2.3B", "180.2K"],
    ]
    table = _render_summary_table(rows)

    # No Lv column / label anywhere.
    assert "Lv" not in table

    # Long account name drives the Account column width (>= its length).
    assert "AVeryLongTestAccountName" in table

    # Widths are dynamic: a longer account name yields a wider table than a
    # short one (not a fixed 64-char box).
    narrow = _render_summary_table([["Test", "82/190", "4", "3", "28.8B", "502.0K"]])
    assert len(table.splitlines()[0]) > len(narrow.splitlines()[0])

    # Header labels present (emoji-prefixed, self-labeling).
    for label in ("Account", "⚡Energy", "🏆Arena", "👑GA", "💰Gold", "💎Gems"):
        assert label in table


def test_render_summary_table_is_display_aligned_with_emoji(monkeypatch):
    """Every rendered line must share the same DISPLAY width (emoji-aware).

    Emoji are double-width in terminals but one code point, so naive len()
    padding misaligns the ``|`` separators. This asserts the emoji-aware
    padding keeps all rows (separators, header, body) the same visual width.
    """
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    rows = [
        ["Alice", "76/190", "11", "17", "900.8M", "570.1K"],
        ["Test Account", "38/190", "53", "8", "2.3B", "180.2K"],
    ]
    lines = _render_summary_table(rows).splitlines()
    widths = {_display_width(line) for line in lines}
    # All lines (===, header, ---, body rows, ===) render to one width.
    assert len(widths) == 1
    # The header carries 5 double-width emoji (⚡🏆👑💰💎), so its display
    # width must exceed the raw code-point length.
    header = lines[1]
    assert _display_width(header) > len(header)


def test_render_summary_table_colors_when_supported(monkeypatch):
    """TTY ではヘッダー・アカウント・順位・エネルギー超過が色付けされる。"""
    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    table = _render_summary_table([
        ["Alice", "76/190", "1", "8", "900.8M", "570.1K"],
        ["Test Account", "200/190", "53", "3", "2.3B", "180.2K"],
    ])
    assert "\033[1;36m" in table  # ヘッダー
    assert "\033[1m" in table  # 1行目アカウント名太字
    assert "\033[33m" in table  # Arena 1位 = 金
    assert "\033[32m" in table  # GA 8位 = 緑
    assert "\033[31m" in table  # 2行目 Energy 200/190 超過 = 赤
    # ゼブラ行でも色付きセルは dim されない
    assert "\033[2;31m" not in table
    assert "\033[2;32m" not in table


def test_render_summary_table_zebra_dims_even_rows(monkeypatch):
    """偶数番目の行は全体 dim され、行の区別がつく。"""
    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    table = _render_summary_table([
        ["Alice", "76/190", "11", "17", "900.8M", "570.1K"],
        ["Test Account", "38/190", "53", "8", "2.3B", "180.2K"],
    ]).splitlines()
    assert table[3].startswith("\033[1m") and not table[3].startswith("\033[1;2m")
    assert table[4].startswith("\033[1;2m")


def test_render_summary_table_no_red_when_energy_within_cap(monkeypatch):
    """上限内の Energy は赤にならない。"""
    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    table = _render_summary_table([["Alice", "76/190", "11", "17", "900.8M", "570.1K"]])
    assert "\033[31m" not in table


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


def test_summarize_asgard_shop_counts_failures(capsys):
    """Asgard shop summary: purchase errors and routine errors count as failed."""
    from hw_genie.commands.asgard_shop import (
        AsgardResult,
        AsgardRunResult,
        ResponseStatus,
    )
    from hw_genie.runner import summarize_asgard_shop

    results = [
        (
            "alpha",
            (
                AsgardRunResult(
                    coins=1000, spent=1000, remaining=0, bought=13, skipped=False,
                    items=[AsgardResult(action="buy 8", status=ResponseStatus.SUCCESS)],
                ),
                None,
            ),
        ),
        (
            "beta",
            (
                AsgardRunResult(
                    coins=1000, spent=100, remaining=900, bought=1, skipped=False,
                    items=[AsgardResult(action="buy 8", status=ResponseStatus.ERROR, error="NotEnough")],
                ),
                None,
            ),
        ),
        ("gamma", (None, ValueError("x"))),
    ]
    failed = summarize_asgard_shop(results)
    out = capsys.readouterr().out
    assert failed == 2
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    assert "Multi asgard-shop summary" in out
    assert "Failed (2)" in out
    assert "1 account(s) completed, ❌ 2 failed." in out


def test_summarize_asgard_shop_skipped_week_is_ok(capsys):
    """Maestro 週（skipped=True）は失敗扱いにせず Skipped 列に表示する。"""
    from hw_genie.commands.asgard_shop import AsgardRunResult
    from hw_genie.runner import summarize_asgard_shop

    results = [
        ("alpha", (AsgardRunResult(coins=1000, spent=0, remaining=1000, bought=0, skipped=True, items=[]), None)),
    ]
    failed = summarize_asgard_shop(results)
    out = capsys.readouterr().out
    assert failed == 0
    assert "⏭️" in out
    assert "1 account(s) completed, ❌ 0 failed." in out


def test_summarize_asgard_shop_unavailable_result_marks_failed(capsys):
    """AsgardRunResult 以外のルーチン結果は失敗扱いにする。"""
    from hw_genie.runner import summarize_asgard_shop

    failed = summarize_asgard_shop([("alpha", ("unexpected", None))])
    out = capsys.readouterr().out
    assert failed == 1
    assert "alpha (asgard-shop result unavailable)" in out
    assert "0 account(s) completed, ❌ 1 failed." in out


def test_summarize_asgard_shop_fetch_error_marks_failed(capsys):
    """在庫取得失敗（result.error）は失敗扱いにして exit 対象にする。"""
    from hw_genie.commands.asgard_shop import AsgardRunResult
    from hw_genie.runner import summarize_asgard_shop

    results = [
        ("alpha", (AsgardRunResult(coins=0, spent=0, remaining=0, bought=0, skipped=False, items=[], error="clanRaid_getInfo failed (notFound)"), None)),
    ]
    failed = summarize_asgard_shop(results)
    out = capsys.readouterr().out
    assert failed == 1
    assert "alpha (shop fetch failed: clanRaid_getInfo failed (notFound))" in out
    assert "0 account(s) completed, ❌ 1 failed." in out


def test_summarize_asgard_shop_shows_gold_buffs(capsys):
    """ゴールドバフ購入があった場合、サマリ表に bought / spent が表示される。"""
    from hw_genie.commands.asgard_shop import AsgardRunResult
    from hw_genie.runner import summarize_asgard_shop

    results = [
        ("alpha", (AsgardRunResult(coins=1000, spent=1000, remaining=0, bought=13, skipped=False, items=[], gold_bought=15, gold_spent=15_000_000), None)),
    ]
    failed = summarize_asgard_shop(results)
    out = capsys.readouterr().out
    assert failed == 0
    assert "15 / 15.0M" in out


def test_asgard_shop_routine_forwards_gold_buffs():
    """asgard_shop_routine は gold_buffs フラグを run_asgard_shop に伝播する。"""
    from unittest.mock import MagicMock, patch

    from hw_genie.runner import asgard_shop_routine

    with patch("hw_genie.commands.asgard_shop.run_asgard_shop") as mock_run:
        routine = asgard_shop_routine(gold_buffs=False)
        client = MagicMock()
        routine(client, "alpha")
        mock_run.assert_called_once_with(client, dry_run=False, account_alias="alpha", gold_buffs=False)

    with patch("hw_genie.commands.asgard_shop.run_asgard_shop") as mock_run:
        routine = asgard_shop_routine()
        client = MagicMock()
        routine(client, "alpha")
        mock_run.assert_called_once_with(client, dry_run=False, account_alias="alpha", gold_buffs=None)


def test_cmd_item_raid_prefers_iterations_over_times(monkeypatch):
    from hw_genie import main
    import json

    payload = json.dumps({"calls": [{"name": "missionRaid", "args": {"id": 1}, "ident": "body"}]})
    args = MagicMock()
    args.account = None
    args.curl = None
    args.payload = payload
    args.iterations = 5
    args.times = 9999

    monkeypatch.setattr("hw_genie.main._ensure_session", lambda a: {"headers": {}})
    monkeypatch.setattr("hw_genie.main.HWClient", MagicMock())
    captured = {}

    def fake_item_raid(client, payload, max_iterations=9999, account=None):
        captured["max_iterations"] = max_iterations

    monkeypatch.setattr("hw_genie.main.run_item_raid", fake_item_raid)

    main.cmd_raid_item(args)
    assert captured["max_iterations"] == 5


def test_cmd_item_raid_falls_back_to_times(monkeypatch):
    from hw_genie import main
    import json

    payload = json.dumps({"calls": [{"name": "missionRaid", "args": {"id": 1}, "ident": "body"}]})
    args = MagicMock()
    args.account = None
    args.curl = None
    args.payload = payload
    args.iterations = None
    args.times = 3

    monkeypatch.setattr("hw_genie.main._ensure_session", lambda a: {"headers": {}})
    monkeypatch.setattr("hw_genie.main.HWClient", MagicMock())
    captured = {}

    def fake_item_raid(client, payload, max_iterations=9999, account=None):
        captured["max_iterations"] = max_iterations

    monkeypatch.setattr("hw_genie.main.run_item_raid", fake_item_raid)

    main.cmd_raid_item(args)
    assert captured["max_iterations"] == 3


def test_cmd_daily_passes_iterations(monkeypatch):
    from hw_genie import main

    args = MagicMock()
    args.curl = None
    args.account = "acc"
    args.iterations = 4

    monkeypatch.setattr("hw_genie.main._ensure_session", lambda a: {"headers": {}})
    monkeypatch.setattr("hw_genie.main.HWClient", MagicMock())
    captured = {}

    def fake_daily(client, item_payload=None, account_alias=None, item_max_iterations=9999):
        captured["item_max_iterations"] = item_max_iterations
        captured["account_alias"] = account_alias

    monkeypatch.setattr("hw_genie.main.run_daily_raid", fake_daily)

    main.cmd_daily(args)
    assert captured["item_max_iterations"] == 4
    assert captured["account_alias"] == "acc"


def test_cmd_multi_daily_threads_iterations(monkeypatch):
    from hw_genie import main
    from hw_genie import runner

    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None):
        captured["routine"] = routine
        return {}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.main.summarize", lambda items: 0)
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda *a, **k: None)

    args = type(
        "A",
        (),
        {"mode": "daily", "accounts": None, "parallel": None, "debug": False, "iterations": 9},
    )()

    main.cmd_multi(args)
    assert captured["routine"].func is runner.daily_routine
    assert captured["routine"].keywords["item_max_iterations"] == 9


def test_toe_routine_runs_tier_with_rotation(monkeypatch):
    """toe_routine drives run_titan_arena_tier with saved-team rotation."""
    from hw_genie import runner

    calls = {}

    class FakeClient:
        headers = {"x-auth-user-id": "99"}

    def fake_tier(client, titans=None, engine=None, attack_score_threshold=250, seeds_per_team=2, **kw):
        calls["titans"] = titans
        calls["engine"] = type(engine).__name__
        calls["threshold"] = attack_score_threshold
        calls["seeds"] = seeds_per_team
        return {"rival_results": [{"win": True}], "errors": [], "completed_tier": True, "daily_reward": "claimed"}

    monkeypatch.setattr("hw_genie.commands.titan_arena.run_titan_arena_tier", fake_tier)
    res = runner.toe_routine(engine="hybrid", seeds_per_team=3, threshold=200)(FakeClient(), "Alice")
    assert calls == {"titans": None, "engine": "JsBridgeBattleEngine", "threshold": 200, "seeds": 3}
    assert res["completed_tier"] is True


def test_summarize_toe_counts():
    from hw_genie.runner import summarize_toe

    results = [
        ("a", ({"rival_results": [{"win": True}], "errors": [], "completed_tier": True, "daily_reward": "claimed"}, None)),
        ("b", ({"rival_results": [{"win": False}], "errors": [], "completed_tier": False, "daily_reward": None}, None)),
        ("c", (None, RuntimeError("x"))),
    ]
    assert summarize_toe(results) == 2


def test_summarize_toe_empty_without_errors_is_ok():
    """Idle/cleared accounts (no targets, no errors) must not fail."""
    from hw_genie.runner import summarize_toe

    results = [
        ("idle", ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)),
    ]
    assert summarize_toe(results) == 0


def test_summarize_toe_empty_with_errors_still_fails():
    from hw_genie.runner import summarize_toe

    results = [
        ("bad", ({"rival_results": [], "errors": [{"stage": "tier_loop"}], "completed_tier": False, "daily_reward": None}, None)),
    ]
    assert summarize_toe(results) == 1


def test_run_log_toe_empty_without_errors_is_ok():
    """Parity: run-log failure judgement matches summarize_toe for idle."""
    from hw_genie.main import _run_log_account_failure

    idle = {"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}
    assert _run_log_account_failure("toe", idle, None) is None
    attempted_loss = {"rival_results": [{"win": False}], "errors": [], "completed_tier": False}
    assert _run_log_account_failure("toe", attempted_loss, None) == "no rival cleared"
    with_err = {"rival_results": [], "errors": [{"stage": "x"}], "completed_tier": False}
    assert _run_log_account_failure("toe", with_err, None) is not None


def test_cmd_multi_toe_passes_parallel_through(monkeypatch):
    """multi toe honors --parallel like the other modes (no forced sequential)."""
    from hw_genie import main

    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None):
        captured["max_parallel"] = max_parallel
        return {"a": ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    args = type(
        "A",
        (),
        {"mode": "toe", "accounts": ["a"], "parallel": 4, "debug": False,
         "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
         "auth_server_url": "http://127.0.0.1:8765", "max_attempts": None},
    )()
    # empty rival_results with no errors is idle (nothing to do) -> ok, no exit
    main.cmd_multi(args)
    assert captured["max_parallel"] == 4


def test_cmd_multi_toe_parallel_defaults_to_env(monkeypatch):
    """multi toe without --parallel passes None so HW_MAX_PARALLEL applies downstream."""
    from hw_genie import main

    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None):
        captured["max_parallel"] = max_parallel
        return {"a": ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    args = type(
        "A",
        (),
        {"mode": "toe", "accounts": ["a"], "parallel": None, "debug": False,
         "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
         "auth_server_url": "http://127.0.0.1:8765", "max_attempts": None},
    )()
    main.cmd_multi(args)
    assert captured["max_parallel"] is None


def test_cmd_multi_toe_threads_max_attempts(monkeypatch):
    """multi toe --max-attempts reaches toe_routine as max_total_attempts."""
    from hw_genie import main

    captured = {}

    def fake_toe_routine(engine="hybrid", seeds_per_team=2, threshold=250, auth_server_url="http://127.0.0.1:8765", max_total_attempts=None, progress="line", **kw):
        captured["cap"] = max_total_attempts
        captured["progress"] = progress
        return lambda c, a: ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)

    monkeypatch.setattr("hw_genie.runner.toe_routine", fake_toe_routine)
    monkeypatch.setattr(
        "hw_genie.main.run_all_accounts",
        lambda routine, accounts=None, max_parallel=None: {"a": ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)},
    )
    args = type(
        "A",
        (),
        {"mode": "toe", "accounts": ["a"], "parallel": 1, "debug": False,
         "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
         "auth_server_url": "http://127.0.0.1:8765", "max_attempts": 6},
    )()
    main.cmd_multi(args)
    assert captured["cap"] == 6


def test_toe_routine_threads_max_total_attempts(monkeypatch):
    """toe_routine forwards max_total_attempts to run_titan_arena_tier."""
    from hw_genie import runner

    seen = {}

    class FakeClient:
        headers = {"x-auth-user-id": "99"}

    def fake_tier(client, titans=None, engine=None, attack_score_threshold=250, seeds_per_team=2, max_total_attempts=None, **kw):
        seen["cap"] = max_total_attempts
        return {"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}

    monkeypatch.setattr("hw_genie.commands.titan_arena.run_titan_arena_tier", fake_tier)
    runner.toe_routine(engine="estimate", max_total_attempts=7)(FakeClient(), "Alice")
    assert seen["cap"] == 7


def test_cmd_multi_toe_env_parallel_resolved_downstream(monkeypatch):
    """HW_MAX_PARALLEL applies to multi toe via resolve_max_parallel."""
    from hw_genie.runner import resolve_max_parallel

    monkeypatch.setenv("HW_MAX_PARALLEL", "3")
    assert resolve_max_parallel(None, 4) == 3
    monkeypatch.delenv("HW_MAX_PARALLEL")
    assert resolve_max_parallel(None, 4) == 4
    assert resolve_max_parallel(2, 4) == 2


def test_toe_routine_threads_auth_server_url(monkeypatch):
    """toe_routine forwards a custom auth server URL to the bridge engine."""
    from hw_genie import runner

    seen = {}

    class FakeClient:
        headers = {"x-auth-user-id": "99"}

    def fake_get_default_engine(mode=None, auth_server_url=None, user_id=None, headers=None):
        seen["url"] = auth_server_url
        seen["user"] = user_id
        from hw_genie.battle.engine import PythonBattleEngine

        return PythonBattleEngine()

    monkeypatch.setattr("hw_genie.battle.engine.get_default_engine", fake_get_default_engine)
    monkeypatch.setattr(
        "hw_genie.commands.titan_arena.run_titan_arena_tier",
        lambda client, **kw: {"ok": True},
    )
    runner.toe_routine(engine="hybrid", auth_server_url="http://127.0.0.1:9999")(FakeClient(), "Alice")
    assert seen == {"url": "http://127.0.0.1:9999", "user": "99"}


def test_cmd_multi_toe_threads_auth_server_url(monkeypatch):
    """multi toe --auth-server-url reaches toe_routine."""
    from hw_genie import main

    captured = {}

    def fake_toe_routine(engine="hybrid", seeds_per_team=2, threshold=250, auth_server_url="http://127.0.0.1:8765", max_total_attempts=None, progress="line", **kw):
        captured.update(
            {"engine": engine, "seeds": seeds_per_team, "threshold": threshold, "url": auth_server_url, "cap": max_total_attempts, "progress": progress}
        )
        return lambda c, a: ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)

    monkeypatch.setattr("hw_genie.runner.toe_routine", fake_toe_routine)
    monkeypatch.setattr(
        "hw_genie.main.run_all_accounts",
        lambda routine, accounts=None, max_parallel=None: {"a": ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)},
    )
    args = type(
        "A",
        (),
        {"mode": "toe", "accounts": ["a"], "parallel": 1, "debug": False,
         "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
         "auth_server_url": "http://127.0.0.1:9999", "max_attempts": None},
    )()
    try:
        main.cmd_multi(args)
    except SystemExit:
        pass
    assert captured["url"] == "http://127.0.0.1:9999"


def test_cmd_multi_failed_logging_does_not_mask_original_error(monkeypatch, capsys):
    """記録自体の失敗（2回目の Ctrl+C 等）が元のエラーを隠さない。"""
    from hw_genie import main

    def boom(routine, accounts=None, max_parallel=None):
        raise RuntimeError("boom")

    def log_boom(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("hw_genie.main.run_all_accounts", boom)
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", log_boom)

    args = type("A", (), {"mode": "daily", "accounts": ["a"], "parallel": 1, "debug": False})()
    with pytest.raises(RuntimeError, match="boom"):
        main.cmd_multi(args)
    err = capsys.readouterr().err
    assert "run log" in err
    # str(KeyboardInterrupt) == "" so the fallback type name must appear.
    assert "KeyboardInterrupt" in err


def test_main_keyboard_interrupt_exits_130(monkeypatch, capsys):
    """トップレベルの Ctrl+C はトレースバック無しで exit 130。"""
    import sys

    from hw_genie import main

    def interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr("hw_genie.core.database.init_db", interrupt)
    monkeypatch.setattr(sys, "argv", ["hw-genie", "sync"])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 130
    assert "Interrupted" in capsys.readouterr().err


def test_summarize_toe_shows_final_tier_and_remaining(capsys):
    """Summary table carries the final tier and remaining rival count."""
    from hw_genie.runner import summarize_toe

    results = [
        ("a", ({"rival_results": [{"win": True}], "errors": [], "completed_tier": True,
                "daily_reward": "claimed", "final_tier": 8, "remaining_rivals": 0}, None)),
        ("b", ({"rival_results": [{"win": False}], "errors": [], "completed_tier": False,
                "daily_reward": None, "final_tier": 5, "remaining_rivals": 3}, None)),
    ]
    assert summarize_toe(results) == 1
    out = capsys.readouterr().out
    assert "Tier" in out and "Left" in out
    assert "8" in out and "0" in out


def test_summarize_toe_fully_cleared_counts_ok(capsys):
    """remaining_rivals == 0 means complete even with banked losses only."""
    from hw_genie.runner import summarize_toe

    results = [
        ("a", ({"rival_results": [{"win": False}], "errors": [],
                "completed_tier": False, "daily_reward": None,
                "final_tier": 8, "remaining_rivals": 0}, None)),
    ]
    assert summarize_toe(results) == 0
    out = capsys.readouterr().out
    assert "Left" in out


def test_summarize_toe_table_columns_align(capsys):
    """All table lines share the same display width (emoji/CJK aware)."""
    from hw_genie.core.utils import display_width
    from hw_genie.runner import summarize_toe

    results = [
        ("Alice", ({"rival_results": [{"win": True}], "errors": [],
                  "completed_tier": True, "daily_reward": "claimed",
                  "final_tier": 8, "remaining_rivals": 0}, None)),
        ("長い名前のアカウント", ({"rival_results": [{"win": False}], "errors": [],
                  "completed_tier": False, "daily_reward": None,
                  "final_tier": 12, "remaining_rivals": 3}, None)),
    ]
    summarize_toe(results)
    out = capsys.readouterr().out
    table = [line for line in out.splitlines() if " | " in line]
    assert len(table) == 3  # header + 2 rows
    widths = {display_width(line) for line in table}
    assert widths and len(widths) == 1


def test_run_log_toe_fully_cleared_is_ok():
    """Parity: remaining_rivals == 0 is ok in run_logs too."""
    from hw_genie.main import _run_log_account_failure

    cleared = {"rival_results": [{"win": False}], "errors": [],
               "completed_tier": False, "daily_reward": None,
               "final_tier": 8, "remaining_rivals": 0}
    assert _run_log_account_failure("toe", cleared, None) is None
    stuck = dict(cleared, remaining_rivals=2)
    assert _run_log_account_failure("toe", stuck, None) == "no rival cleared"


def test_run_all_accounts_partial_on_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt mid-run returns partial with InterruptedError entries."""
    from hw_genie import runner

    runner.reset_cancel()
    try:

        class _FakeFuture:
            def __init__(self, acc, *, done, result=None):
                self._acc = acc
                self._done = done
                self._result = result
                self.cancel_called = False
                self._cancelled = False

            def done(self):
                return self._done

            def cancelled(self):
                return self._cancelled

            def cancel(self):
                self.cancel_called = True
                if not self._done:
                    self._cancelled = True
                    return True
                return False

            def result(self):
                assert self._result is not None
                return self._result

        fut_a = _FakeFuture("a", done=True, result=("a", "ok-a", None))
        fut_b = _FakeFuture("b", done=False, result=("b", "ok-b", None))

        class _FakePool:
            def submit(self, fn, acc, routine):
                return {"a": fut_a, "b": fut_b}[acc]

            def shutdown(self, wait=True, cancel_futures=False):
                return None

        monkeypatch.setattr(runner, "ThreadPoolExecutor", lambda max_workers=None: _FakePool())
        monkeypatch.setattr(
            runner,
            "as_completed",
            lambda futures: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        results = runner.run_all_accounts(lambda c, a: None, accounts=["a", "b"], max_parallel=2)
        assert list(results) == ["a", "b"]
        assert results["a"] == ("ok-a", None)
        assert results["b"][0] is None
        assert isinstance(results["b"][1], InterruptedError)
    finally:
        runner.reset_cancel()


def test_run_all_accounts_cancels_pending_futures(monkeypatch):
    """Pending futures are cancelled when the wait loop is interrupted."""
    from hw_genie import runner

    runner.reset_cancel()
    try:

        class _FakeFuture:
            def __init__(self, acc, *, done, result=None):
                self._acc = acc
                self._done = done
                self._result = result
                self.cancel_called = False
                self._cancelled = False

            def done(self):
                return self._done

            def cancelled(self):
                return self._cancelled

            def cancel(self):
                self.cancel_called = True
                if not self._done:
                    self._cancelled = True
                    return True
                return False

            def result(self):
                assert self._result is not None
                return self._result

        fut_a = _FakeFuture("a", done=True, result=("a", "ok-a", None))
        fut_b = _FakeFuture("b", done=False, result=None)
        fut_b._result = ("b", None, None)

        class _FakePool:
            def submit(self, fn, acc, routine):
                return {"a": fut_a, "b": fut_b}[acc]

            def shutdown(self, wait=True, cancel_futures=False):
                return None

        monkeypatch.setattr(runner, "ThreadPoolExecutor", lambda max_workers=None: _FakePool())
        monkeypatch.setattr(
            runner,
            "as_completed",
            lambda futures: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        runner.run_all_accounts(lambda c, a: None, accounts=["a", "b"], max_parallel=2)
        assert fut_b.cancel_called is True
        assert fut_a.cancel_called is False
    finally:
        runner.reset_cancel()


def test_cmd_multi_sigint_first_cooperative_second_forces(monkeypatch):
    """1st Ctrl+C is cooperative (KI); 2nd forces SystemExit(130), restoring handler."""
    import signal

    import pytest

    from hw_genie import main, runner

    runner.reset_cancel()
    try:
        installed_handlers: list = []
        restore_calls: list = []
        orig = signal.SIG_DFL
        monkeypatch.setattr(signal, "getsignal", lambda sig: orig)

        def fake_signal(sig, handler):
            if callable(handler):
                installed_handlers.append(handler)
            else:
                restore_calls.append(handler)
                installed_handlers.append(handler)
            return None

        monkeypatch.setattr(signal, "signal", fake_signal)
        monkeypatch.setattr("hw_genie.main.run_all_accounts", lambda *a, **k: {})
        monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)

        args = type("A", (), {"mode": "daily", "accounts": ["a"], "parallel": 1, "debug": False})()
        main.cmd_multi(args)
        assert runner.is_cancelled() is False
        assert installed_handlers, "custom SIGINT handler must be installed"
        handler = installed_handlers[0]
        assert callable(handler)

        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
        assert runner.is_cancelled() is True

        with pytest.raises(SystemExit) as exc:
            handler(signal.SIGINT, None)
        assert exc.value.code == 130
        assert restore_calls, "2nd press must restore the original handler"
        assert restore_calls[0] is orig
    finally:
        runner.reset_cancel()


def test_cmd_multi_finally_restores_dfl_when_no_orig(monkeypatch):
    """Non-main-thread / no-signal edge: never leave the custom handler installed."""
    import signal

    from hw_genie import main, runner

    runner.reset_cancel()
    try:
        calls: list = []

        def fake_getsignal(sig):
            raise ValueError("getsignal: no handler in non-main thread")

        def fake_signal(sig, handler):
            calls.append(handler)
            return None

        monkeypatch.setattr(signal, "getsignal", fake_getsignal)
        monkeypatch.setattr(signal, "signal", fake_signal)
        monkeypatch.setattr("hw_genie.main.run_all_accounts", lambda *a, **k: {})
        monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)

        args = type("A", (), {"mode": "daily", "accounts": ["a"], "parallel": 1, "debug": False})()
        main.cmd_multi(args)
        assert calls, "finally must attempt a restore even without orig"
        assert calls[-1] is signal.SIG_DFL
        assert not any(callable(c) and c not in (signal.SIG_DFL, signal.SIG_IGN) for c in calls[-1:]), \
            "last restore must not be the custom handler"
    finally:
        runner.reset_cancel()


def test_cmd_multi_signal_install_failure_still_runs(monkeypatch):
    """signal.signal raising (non-main thread) must not break the run."""
    import signal

    from hw_genie import main, runner

    runner.reset_cancel()
    try:
        monkeypatch.setattr(signal, "getsignal", lambda sig: signal.SIG_DFL)

        def boom_signal(sig, handler):
            raise ValueError("signal only works in main thread")

        monkeypatch.setattr(signal, "signal", boom_signal)
        monkeypatch.setattr("hw_genie.main.run_all_accounts", lambda *a, **k: {})
        monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)

        args = type("A", (), {"mode": "daily", "accounts": ["a"], "parallel": 1, "debug": False})()
        main.cmd_multi(args)
    finally:
        runner.reset_cancel()


def test_cmd_multi_interrupted_log_failure_still_exits_130(monkeypatch, capsys):
    """2nd Ctrl+C during interrupted-path DB write cannot mask exit 130."""
    import pytest

    from hw_genie import main, runner

    runner.reset_cancel()
    try:
        def fake_run(routine, accounts=None, max_parallel=None):
            runner.request_cancel()
            return {"a": (None, InterruptedError("interrupted by user"))}

        def log_boom(**kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
        monkeypatch.setattr("hw_genie.core.run_log.record_run_log", log_boom)

        args = type("A", (), {"mode": "toe", "accounts": ["a"], "parallel": 1, "debug": False,
                              "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
                              "auth_server_url": "http://127.0.0.1:8765", "max_attempts": None})()
        with pytest.raises(SystemExit) as exc:
            main.cmd_multi(args)
        assert exc.value.code == 130
        err = capsys.readouterr().err
        assert "run log" in err
    finally:
        runner.reset_cancel()


def test_summarize_toe_interrupted_error_counts_ok(capsys):
    """(None, InterruptedError) entries count as COMPLETE (ok), not failed."""
    from hw_genie.runner import summarize_toe

    assert summarize_toe([("a", (None, InterruptedError("interrupted by user")))]) == 0
    out = capsys.readouterr().out
    assert "1 account(s) completed, ❌ 0 failed." in out
    assert "a" in out


def test_summarize_toe_interrupted_flag_counts_ok(capsys):
    """A tier summary with interrupted=True (marker only) counts as ok."""
    from hw_genie.runner import summarize_toe

    summary = {
        "rival_results": [{"rivalId": "-1", "win": True}],
        "errors": [{"stage": "interrupted", "message": "interrupted by user"}],
        "completed_tier": False,
        "daily_reward": None,
        "final_tier": 7,
        "remaining_rivals": 2,
        "interrupted": True,
    }
    assert summarize_toe([("a", (summary, None))]) == 0
    out = capsys.readouterr().out
    assert "1 account(s) completed, ❌ 0 failed." in out


def test_summarize_toe_interrupted_with_real_errors_still_fails(capsys):
    """interrupted=True with real bridge/API errors still counts as failed."""
    from hw_genie.runner import summarize_toe

    summary = {
        "rival_results": [{"rivalId": "-1", "win": True}],
        "errors": [
            {"stage": "interrupted", "message": "interrupted by user"},
            {"stage": "rivals", "message": "bridge dead"},
        ],
        "completed_tier": False,
        "daily_reward": None,
        "final_tier": 7,
        "remaining_rivals": 2,
        "interrupted": True,
    }
    assert summarize_toe([("a", (summary, None))]) == 1
    out = capsys.readouterr().out
    assert "Failed (1)" in out
    assert "1 error(s)" in out


def test_run_log_toe_interrupted_error_counts_ok():
    """_build_run_log_summary('toe', ...) marks InterruptedError entries ok=True."""
    from hw_genie.main import _build_run_log_summary, _run_log_account_failure

    assert _run_log_account_failure("toe", None, InterruptedError("interrupted by user")) is None
    entries, summary = _build_run_log_summary("toe", {"a": (None, InterruptedError("interrupted by user"))})
    assert entries == [{"account": "a", "ok": True, "error": None}]
    assert summary is None


def test_run_log_toe_interrupted_flag_counts_ok():
    """An interrupted tier dict without real errors counts as ok in run_logs."""
    from hw_genie.main import _build_run_log_summary, _run_log_account_failure

    summary = {
        "rival_results": [{"rivalId": "-1", "win": True}],
        "errors": [{"stage": "interrupted", "message": "interrupted by user"}],
        "completed_tier": False,
        "remaining_rivals": 2,
        "interrupted": True,
    }
    assert _run_log_account_failure("toe", summary, None) is None
    entries, err_summary = _build_run_log_summary("toe", {"a": (summary, None)})
    assert entries == [{"account": "a", "ok": True, "error": None}]
    assert err_summary is None


def test_run_log_toe_interrupted_with_real_errors_still_fails():
    """An interrupted tier dict with real errors still fails in run_logs."""
    from hw_genie.main import _build_run_log_summary, _run_log_account_failure

    summary = {
        "rival_results": [{"rivalId": "-1", "win": True}],
        "errors": [
            {"stage": "interrupted", "message": "interrupted by user"},
            {"stage": "rivals", "message": "bridge dead"},
        ],
        "completed_tier": False,
        "remaining_rivals": 2,
        "interrupted": True,
    }
    reason = _run_log_account_failure("toe", summary, None)
    assert reason is not None and "1 toe error(s)" in reason
    entries, err_summary = _build_run_log_summary("toe", {"a": (summary, None)})
    assert entries[0]["ok"] is False
    assert err_summary is not None and "a" in err_summary


def test_cmd_multi_toe_pure_interrupt_exits_zero(monkeypatch):
    """Cooperative cancel with no real failures is a clean complete (exit 0)."""
    from hw_genie import main, runner

    runner.reset_cancel()
    try:
        records = {}

        def fake_run(routine, accounts=None, max_parallel=None):
            runner.request_cancel()
            return {"a": (None, InterruptedError("interrupted by user"))}

        def fake_record(**kwargs):
            records.update(kwargs)

        monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
        monkeypatch.setattr("hw_genie.core.run_log.record_run_log", fake_record)

        args = type("A", (), {"mode": "toe", "accounts": ["a"], "parallel": 1, "debug": False,
                              "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
                              "auth_server_url": "http://127.0.0.1:8765", "max_attempts": None})()
        main.cmd_multi(args)  # must not raise SystemExit
        assert records["status"] == "ok"
        assert records["exit_code"] == 0
        assert records["accounts"] == [{"account": "a", "ok": True, "error": None}]
        assert records["error_summary"] is None
    finally:
        runner.reset_cancel()


def test_cmd_multi_toe_interrupt_with_real_failures_exits_1(monkeypatch):
    """Cancel with real failures alongside still exits 1 (not 130)."""
    import pytest

    from hw_genie import main, runner

    runner.reset_cancel()
    try:
        def fake_run(routine, accounts=None, max_parallel=None):
            runner.request_cancel()
            return {
                "a": (None, InterruptedError("interrupted by user")),
                "b": (None, RuntimeError("boom")),
            }

        monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
        monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)

        args = type("A", (), {"mode": "toe", "accounts": ["a", "b"], "parallel": 1, "debug": False,
                              "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
                              "auth_server_url": "http://127.0.0.1:8765", "max_attempts": None})()
        with pytest.raises(SystemExit) as exc:
            main.cmd_multi(args)
        assert exc.value.code == 1
    finally:
        runner.reset_cancel()


def test_run_all_accounts_systemexit_propagates(monkeypatch):
    """2nd-press SystemExit during result fetch must not be stored as a result."""
    import pytest

    from hw_genie import runner

    runner.reset_cancel()
    try:
        class _FakeFuture:
            def done(self):
                return True

            def cancelled(self):
                return False

            def cancel(self):
                return False

            def result(self):
                raise SystemExit(130)

        class _FakePool:
            def submit(self, fn, acc, routine):
                return _FakeFuture()

            def shutdown(self, wait=True, cancel_futures=False):
                return None

        monkeypatch.setattr(runner, "ThreadPoolExecutor", lambda max_workers=None: _FakePool())
        monkeypatch.setattr(
            runner,
            "as_completed",
            lambda futures: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(SystemExit) as exc:
            runner.run_all_accounts(lambda c, a: None, accounts=["a"], max_parallel=1)
        assert exc.value.code == 130
    finally:
        runner.reset_cancel()


# --- Compact ToE progress output (quiet|line|verbose) ---


def test_toe_progress_parser_defaults_to_line(mocker):
    """toe attack/run and multi toe default --progress to line; choices parse."""
    import sys

    from hw_genie import main

    mocker.patch("hw_genie.core.database.init_db")
    mocker.patch("hw_genie.core.database.install_token_masking_filter")
    mocker.patch("hw_genie.main.setup_logging")
    mock_attack = mocker.patch("hw_genie.main.cmd_toe_attack")
    mock_run = mocker.patch("hw_genie.main.cmd_toe_run")
    mock_multi = mocker.patch("hw_genie.main.cmd_multi")

    sys.argv = ["hw-genie", "toe", "attack", "-a", "TestUser"]
    main.main()
    assert mock_attack.call_args.args[0].progress == "line"

    sys.argv = ["hw-genie", "toe", "attack", "-a", "TestUser", "--progress", "quiet"]
    main.main()
    assert mock_attack.call_args.args[0].progress == "quiet"

    sys.argv = ["hw-genie", "toe", "run", "-a", "TestUser"]
    main.main()
    assert mock_run.call_args.args[0].progress == "line"

    sys.argv = ["hw-genie", "toe", "run", "-a", "TestUser", "--progress", "verbose"]
    main.main()
    assert mock_run.call_args.args[0].progress == "verbose"

    sys.argv = ["hw-genie", "multi", "toe"]
    main.main()
    assert mock_multi.call_args.args[0].progress == "line"

    sys.argv = ["hw-genie", "multi", "toe", "--progress", "quiet"]
    main.main()
    assert mock_multi.call_args.args[0].progress == "quiet"


def test_cmd_toe_attack_forwards_progress(mocker):
    """toe attack threads --progress through to run_titan_arena."""
    from hw_genie.main import cmd_toe_attack

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mocker.patch("hw_genie.main.HWClient")
    mock_run = mocker.patch("hw_genie.commands.titan_arena.run_titan_arena")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=MagicMock())
    args = MagicMock()
    args.account = "TestUser"
    args.rival = None
    args.titans = None
    args.dry_run = False
    args.estimate_only = False
    args.engine = "estimate"
    args.auth_server_url = "http://127.0.0.1:8765"
    args.progress = "quiet"
    cmd_toe_attack(args)
    assert mock_run.call_args.kwargs.get("progress") == "quiet"


def test_cmd_toe_run_forwards_progress(mocker):
    """toe run threads --progress through to run_titan_arena_tier."""
    from hw_genie.main import cmd_toe_run

    mocker.patch("hw_genie.main._ensure_session", return_value={"x-auth-token": "t"})
    mocker.patch("hw_genie.main.HWClient")
    mocker.patch("hw_genie.main.resolve_account", return_value="TestUser")
    mock_run = mocker.patch("hw_genie.commands.titan_arena.run_titan_arena_tier")
    mocker.patch("hw_genie.battle.engine.get_default_engine", return_value=MagicMock())
    args = MagicMock()
    args.account = "TestUser"
    args.titans = None
    args.threshold = 250
    args.stop_on_loss = False
    args.engine = "estimate"
    args.auth_server_url = "http://127.0.0.1:8765"
    args.seeds = 2
    args.max_attempts = None
    args.progress = "line"
    cmd_toe_run(args)
    assert mock_run.call_args.kwargs.get("progress") == "line"


def test_cmd_multi_toe_forwards_progress(monkeypatch):
    """multi toe threads --progress through to toe_routine."""
    from hw_genie import main

    captured = {}

    def fake_toe_routine(engine="hybrid", seeds_per_team=2, threshold=250, auth_server_url="http://127.0.0.1:8765", max_total_attempts=None, progress="line", **kw):
        captured["progress"] = progress
        return lambda c, a: ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)

    monkeypatch.setattr("hw_genie.runner.toe_routine", fake_toe_routine)
    monkeypatch.setattr(
        "hw_genie.main.run_all_accounts",
        lambda routine, accounts=None, max_parallel=None: {"a": ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)},
    )
    args = type(
        "A",
        (),
        {"mode": "toe", "accounts": ["a"], "parallel": 1, "debug": False,
         "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
         "auth_server_url": "http://127.0.0.1:8765", "max_attempts": None, "progress": "quiet"},
    )()
    main.cmd_multi(args)
    assert captured["progress"] == "quiet"


def test_toe_routine_line_mode_shares_dashboard(monkeypatch):
    """line mode shares one dashboard; quiet/verbose use none."""
    from hw_genie import runner

    class FakeClient:
        headers = {"x-auth-user-id": "99"}

    monkeypatch.setattr(
        "hw_genie.commands.titan_arena.run_titan_arena_tier",
        lambda client, **kw: {"ok": True, "progress": kw.get("progress"), "has_dashboard": kw.get("dashboard") is not None},
    )
    line_run = runner.toe_routine(engine="estimate", progress="line")
    assert isinstance(line_run.dashboard, runner.ToeProgressDashboard)
    res = line_run(FakeClient(), "Alice")
    assert res["progress"] == "line" and res["has_dashboard"] is True

    quiet_run = runner.toe_routine(engine="estimate", progress="quiet")
    assert getattr(quiet_run, "dashboard", None) is None


def test_dashboard_lock_ordering_two_accounts(capsys):
    """Two workers updating concurrently keep intact per-account lines."""
    import threading

    from hw_genie.runner import ToeProgressDashboard

    dash = ToeProgressDashboard(throttle_secs=0)
    assert hasattr(dash, "_lock")
    barrier = threading.Barrier(2)

    def worker(account):
        barrier.wait()
        for i in range(20):
            dash.update(account, f"rival -{i} [{i}/20] wins 0 pace 1s/attempt")

    threads = [threading.Thread(target=worker, args=(acc,)) for acc in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out = capsys.readouterr().out
    assert "\r" not in out
    alpha_lines = [line for line in out.splitlines() if line.startswith("[alpha]")]
    beta_lines = [line for line in out.splitlines() if line.startswith("[beta]")]
    assert len(alpha_lines) == 20 and len(beta_lines) == 20
    # No interleaved fragments: every line belongs wholly to one account.
    for line in out.splitlines():
        assert not (line.startswith("[alpha]") and "[beta]" in line)
        assert not (line.startswith("[beta]") and "[alpha]" in line)


def test_dashboard_updates_do_not_reorder_results(monkeypatch):
    """Dashboard printing never affects API parallelism or result ordering."""
    from hw_genie import runner

    runner.reset_cancel()
    try:
        monkeypatch.setattr(
            "hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": acc}
        )
        monkeypatch.setattr("hw_genie.runner.HWClient", lambda h: MagicMock())
        dash = runner.ToeProgressDashboard(throttle_secs=0)

        def routine(client, account):
            dash.update(account, "rival -1 [1/2] wins 0 pace 1s/attempt")
            return f"ok:{account}"

        results = runner.run_all_accounts(routine, accounts=["zulu", "alpha", "mike"], max_parallel=2)
        assert list(results) == ["zulu", "alpha", "mike"]
        assert results["alpha"] == ("ok:alpha", None)
    finally:
        runner.reset_cancel()


def test_sanitize_progress_log_collapses_carriage_returns():
    """Stored logs keep final visible text with no bare \\r."""
    from hw_genie.runner import sanitize_progress_log

    assert sanitize_progress_log(None) is None
    assert sanitize_progress_log("plain line\n") == "plain line\n"
    assert "\r" not in sanitize_progress_log("old\rnew line\n")
    assert sanitize_progress_log("old\rnew line\n") == "new line\n"
    assert sanitize_progress_log("\r[alpha] rival -1 [2/4] wins 1") == "[alpha] rival -1 [2/4] wins 1"


def test_cmd_multi_stored_log_contains_no_cr(monkeypatch):
    """multi run-log text is sanitized even when workers emit \\r updates."""
    from hw_genie import main

    records = {}

    def fake_record(**kwargs):
        records.update(kwargs)
        return 1

    def fake_run(routine, accounts=None, max_parallel=None):
        print("before\rafter line")
        return {"a": ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", fake_record)

    args = type(
        "A",
        (),
        {"mode": "toe", "accounts": ["a"], "parallel": 1, "debug": False,
         "dry_run": False, "engine": "hybrid", "seeds": 2, "threshold": 250,
         "auth_server_url": "http://127.0.0.1:8765", "max_attempts": None, "progress": "line"},
    )()
    main.cmd_multi(args)
    assert "\r" not in (records.get("log_text") or "")
    assert "after line" in (records.get("log_text") or "")


def test_toe_routine_threads_bank_best_loss(monkeypatch):
    """toe_routine forwards bank_best_loss to run_titan_arena_tier (default True)."""
    from hw_genie import runner

    seen = {}

    class FakeClient:
        headers = {"x-auth-user-id": "99"}

    def fake_tier(client, titans=None, engine=None, attack_score_threshold=250, seeds_per_team=2, max_total_attempts=None, **kw):
        seen["bank"] = kw.get("bank_best_loss")
        return {"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}

    monkeypatch.setattr("hw_genie.commands.titan_arena.run_titan_arena_tier", fake_tier)
    runner.toe_routine(engine="estimate")(FakeClient(), "Alice")
    assert seen["bank"] is True
    runner.toe_routine(engine="estimate", bank_best_loss=False)(FakeClient(), "Alice")
    assert seen["bank"] is False


def test_toe_routine_defaults_seeds_10_and_end_on_loss(monkeypatch):
    """toe_routine defaults to 10 seeds with loss banking enabled."""
    from hw_genie import runner

    seen = {}

    class FakeClient:
        headers = {"x-auth-user-id": "99"}

    def fake_tier(client, titans=None, engine=None, attack_score_threshold=250, seeds_per_team=2, max_total_attempts=None, **kw):
        seen["seeds"] = seeds_per_team
        seen["bank"] = kw.get("end_on_loss")
        return {"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}

    monkeypatch.setattr("hw_genie.commands.titan_arena.run_titan_arena_tier", fake_tier)
    runner.toe_routine(engine="estimate")(FakeClient(), "Alice")
    assert seen == {"seeds": 10, "bank": True}
    runner.toe_routine(engine="estimate", seeds_per_team=3, end_on_loss=False)(FakeClient(), "Alice")
    assert seen == {"seeds": 3, "bank": False}


def test_cmd_multi_toe_threads_no_end_on_loss(monkeypatch):
    """multi toe --no-end-on-loss reaches toe_routine (default banking on)."""
    from hw_genie import main

    captured = {}

    def fake_toe_routine(engine="hybrid", seeds_per_team=10, threshold=250, auth_server_url="http://127.0.0.1:8765", max_total_attempts=None, progress="line", **kw):
        captured["seeds"] = seeds_per_team
        captured["bank"] = kw.get("end_on_loss")
        return lambda c, a: ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)

    monkeypatch.setattr("hw_genie.runner.toe_routine", fake_toe_routine)
    monkeypatch.setattr(
        "hw_genie.main.run_all_accounts",
        lambda routine, accounts=None, max_parallel=None: {"a": ({"rival_results": [], "errors": [], "completed_tier": False, "daily_reward": None}, None)},
    )

    def make_args(no_end):
        return type(
            "A",
            (),
            {"mode": "toe", "accounts": ["a"], "parallel": 1, "debug": False,
             "dry_run": False, "engine": "hybrid", "seeds": 10, "threshold": 250,
             "auth_server_url": "http://127.0.0.1:8765", "max_attempts": None,
             "no_bank_best_loss": False, "no_end_on_loss": no_end},
        )()

    main.cmd_multi(make_args(False))
    assert captured == {"seeds": 10, "bank": True}
    main.cmd_multi(make_args(True))
    assert captured == {"seeds": 10, "bank": False}
