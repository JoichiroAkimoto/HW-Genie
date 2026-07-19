from unittest.mock import MagicMock

import pytest

from hw_genie.runner import (
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


def test_summarize_counts_failures(caplog):
    results = [
        ("alpha", (None, None)),
        ("beta", (None, ValueError("x"))),
    ]
    with caplog.at_level("WARNING"):
        failed = summarize(results)
    assert failed == 1
    assert "beta" in caplog.text


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
    fake_client = object()

    def fake_daily(client, item_payload=None, account_alias=None):
        calls["client"] = client
        calls["account"] = account_alias
        return "ok"

    monkeypatch.setattr("hw_genie.commands.daily_raid.run_daily_raid", fake_daily)
    assert runner.daily_routine(fake_client, "acc") == "ok"
    assert calls["client"] is fake_client
    assert calls["account"] == "acc"


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

