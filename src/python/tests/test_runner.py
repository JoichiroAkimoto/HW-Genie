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

    def fake_daily(client, item_payload=None, account_alias=None):
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

    def fake_daily(client, item_payload=None, account_alias=None):
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

    def spy_daily_raid(client, item_payload=None, account_alias=None):
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
        mock_run.assert_called_once_with(client, dry_run=False, account_alias="alpha", gold_buffs=True)
