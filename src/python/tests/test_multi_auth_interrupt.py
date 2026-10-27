"""multi ToE の認証中断分離 (preflight / HWAuthError / 3-bucket / error_summary)."""

import pytest

from hw_genie import runner
from hw_genie.core.client import HWAuthError


@pytest.fixture(autouse=True)
def _restore_bridge_warning_state():
    """プロセスグローバルな警告状態をテスト前後で保存・復元する。"""
    import hw_genie.commands.titan_arena as ta_mod

    snap_hint = ta_mod._BRIDGE_USERSCRIPT_HINT_SHOWN
    snap_streak = ta_mod._INVALID_BATTLE_STREAK
    snap_warned = ta_mod._INVALID_BATTLE_WARNED
    snap_streaks = dict(ta_mod._INVALID_BATTLE_STREAKS)
    snap_warned_accounts = set(ta_mod._INVALID_BATTLE_WARNED_ACCOUNTS)
    try:
        yield
    finally:
        ta_mod._BRIDGE_USERSCRIPT_HINT_SHOWN = snap_hint
        ta_mod._INVALID_BATTLE_STREAK = snap_streak
        ta_mod._INVALID_BATTLE_WARNED = snap_warned
        ta_mod._INVALID_BATTLE_STREAKS.clear()
        ta_mod._INVALID_BATTLE_STREAKS.update(snap_streaks)
        ta_mod._INVALID_BATTLE_WARNED_ACCOUNTS.clear()
        ta_mod._INVALID_BATTLE_WARNED_ACCOUNTS.update(snap_warned_accounts)


@pytest.fixture(autouse=True)
def _hermetic_bridge_probe(monkeypatch):
    """toe開始時プローブの実ソケット踏みを封じる（hermetic化）。

    成功スタブなので警告も出ず即時復帰する。プローブ挙動自体を検証する
    テスト（urlopen を明示 monkeypatch するもの）は後勝ちで上書きされる。
    """
    import contextlib
    import io
    import urllib.request

    def _ok(req, timeout=None):
        return contextlib.nullcontext(io.BytesIO(b"ok"))

    monkeypatch.setattr(urllib.request, "urlopen", _ok)


def _toe_args(*accounts):
    return type(
        "A",
        (),
        {
            "mode": "toe",
            "accounts": list(accounts),
            "parallel": 1,
            "debug": False,
            "dry_run": False,
            "engine": "hybrid",
            "seeds": 2,
            "threshold": 250,
            "auth_server_url": "http://127.0.0.1:8765",
            "max_attempts": None,
        },
    )()


def _idle_toe():
    return {
        "rival_results": [],
        "errors": [],
        "completed_tier": False,
        "daily_reward": None,
    }


def test_preflight_excludes_missing_without_workers(monkeypatch):
    """欠落アカウントはワーカー投入前に除外され、失敗エントリとして混ざる。"""
    from hw_genie import main

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers",
        lambda acc: {"x-auth-token": "t"} if acc == "Alice" else None,
    )
    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        captured["accounts"] = list(accounts or [])
        return {"Alice": (_idle_toe(), None)}

    records = {}
    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr(
        "hw_genie.core.run_log.record_run_log",
        lambda **k: records.update(k),
    )
    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(_toe_args("Alice", "Bob"))
    assert exc.value.code == 1
    assert captured["accounts"] == ["Alice"]
    assert records["status"] == "failed"
    assert "Session not found for account 'Bob'." in (records["error_summary"] or "")


def test_preflight_all_missing_skips_workers_and_logs_failed(monkeypatch):
    """全件欠落ならワーカー投入せず failed 記録＋exit 1。"""
    from hw_genie import main

    monkeypatch.setattr("hw_genie.runner.load_session_headers", lambda acc: None)
    called = []

    def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        called.append(1)
        return {}

    records = {}
    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr(
        "hw_genie.core.run_log.record_run_log",
        lambda **k: records.update(k),
    )
    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(_toe_args("Alice", "Bob"))
    assert exc.value.code == 1
    assert called == []
    assert records["status"] == "failed"
    assert records["exit_code"] == 1
    assert "Session not found for account 'Alice'." in (records["error_summary"] or "")
    assert "Session not found for account 'Bob'." in (records["error_summary"] or "")


def test_preflight_docstring_notes_expiry_needs_api():
    """期限切れは API まで判定不能である旨が docstring に明記される。"""
    from hw_genie.main import _preflight_multi_accounts

    doc = _preflight_multi_accounts.__doc__ or ""
    assert "HWAuthError" in doc or "API" in doc
    assert "x-auth" in doc


def test_run_for_account_rewrites_auth_error(monkeypatch):
    """HWAuthError は先に捕捉され、アカウント＋1行＋再同期導線に整形される。"""
    from unittest.mock import MagicMock

    from hw_genie.runner import run_for_account

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"}
    )
    monkeypatch.setattr("hw_genie.runner.HWClient", lambda h: MagicMock())

    def boom(client, account):
        raise HWAuthError("Session expired or invalid signature.\nsecond line")

    acc, res, err = run_for_account("Alice", boom)
    assert acc == "Alice" and res is None
    assert isinstance(err, HWAuthError)
    assert str(err).startswith("Session expired for 'Alice': Session expired or invalid signature.")
    assert "second line" not in str(err)
    assert "Re-sync via auth-server/curl, then retry" in str(err)


def test_summarize_toe_three_buckets_and_return_unchanged(capsys):
    """ok/interrupted/failed を分離表示し、戻り値は len(failed) のまま。"""
    from hw_genie.runner import summarize_toe

    ok_summary = {
        "rival_results": [{"win": True}],
        "errors": [],
        "completed_tier": True,
        "daily_reward": "claimed",
    }
    interrupted_summary = {
        "rival_results": [{"rivalId": "-1", "win": True}],
        "errors": [{"stage": "interrupted", "message": "interrupted by user"}],
        "completed_tier": False,
        "daily_reward": None,
        "interrupted": True,
    }
    results = [
        ("Alice", (ok_summary, None)),
        ("Bob", (None, InterruptedError("interrupted by user"))),
        ("Carol", (interrupted_summary, None)),
        ("Dave", (None, RuntimeError("boom"))),
    ]
    assert summarize_toe(results) == 1
    out = capsys.readouterr().out
    assert "✅ 1 account(s) completed, ⏸ 2 interrupted, ❌ 1 failed" in out
    assert "⏸ Interrupted (2): Bob, Carol" in out
    assert "❌ Failed (1): Dave" in out
    assert "interrupted" in out


def test_cancel_error_summary_excludes_interrupt(monkeypatch):
    """中断時は実失敗のみで error_summary を作り、中断を合成しない。"""
    from hw_genie import main

    runner.reset_cancel()
    try:
        records = {}
        monkeypatch.setattr(
            "hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"}
        )

        def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
            runner.request_cancel()
            return {
                "Alice": (None, InterruptedError("interrupted by user")),
                "Bob": (None, RuntimeError("boom")),
            }

        monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
        monkeypatch.setattr(
            "hw_genie.core.run_log.record_run_log", lambda **k: records.update(k)
        )
        with pytest.raises(SystemExit) as exc:
            main.cmd_multi(_toe_args("Alice", "Bob"))
        assert exc.value.code == 1
        assert records["error_summary"] == "1 account(s) failed: Bob (boom)"
        assert "interrupt" not in (records["error_summary"] or "").lower()
        by_account = {e["account"]: e for e in records["accounts"]}
        assert by_account["Alice"] == {
            "account": "Alice",
            "ok": True,
            "error": None,
            "interrupted": True,
        }
        assert by_account["Bob"]["interrupted"] is False
    finally:
        runner.reset_cancel()


def test_bridge_unreachable_warns_without_abort(monkeypatch, capsys):
    """hybrid の開始時プローブは警告のみで、中断しない。"""

    def _refused(req, timeout=None):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr("hw_genie.runner.urllib.request.urlopen", _refused)
    run = runner.toe_routine(engine="hybrid", auth_server_url="http://127.0.0.1:9")
    out = capsys.readouterr().out
    assert "auth-server unreachable" in out
    assert callable(run)
    capsys.readouterr()
    runner.toe_routine(engine="estimate")
    assert "auth-server unreachable" not in capsys.readouterr().out


def test_probe_warning_type_centered_no_query(monkeypatch, capsys):
    """プローブ警告は例外型中心で、クエリを含めない。"""

    def _refused(req, timeout=None):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr("hw_genie.runner.urllib.request.urlopen", _refused)
    run = runner.toe_routine(
        engine="hybrid", auth_server_url="http://127.0.0.1:9/health?token=secret"
    )
    assert callable(run)
    out = capsys.readouterr().out
    assert "auth-server unreachable" in out
    assert "token=secret" not in out
    # type-centered: ConnectionRefusedError など型名が出る（全文ではない）
    assert "Error" in out


def test_probe_url_strips_query_fragment(monkeypatch):
    """query/fragment付きURLでも送信URLは破綻せず秘密を含めない。"""
    captured = {}

    def fake_urlopen(req, timeout=None):
        try:
            captured["url"] = req.get_full_url()
        except Exception:
            captured["url"] = str(req)
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr("hw_genie.runner.urllib.request.urlopen", fake_urlopen)
    runner._warn_if_bridge_unreachable(
        "http://127.0.0.1:9/base?token=secret#frag"
    )
    url = captured["url"]
    assert url == "http://127.0.0.1:9/base/health"
    assert "?" not in url and "#" not in url
    assert "token" not in url and "secret" not in url


def test_probe_url_health_suffix_not_doubled(monkeypatch):
    """末尾/health＋queryは重複付与せずqueryのみ落とす。"""
    captured = {}

    def fake_urlopen(req, timeout=None):
        try:
            captured["url"] = req.get_full_url()
        except Exception:
            captured["url"] = str(req)
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr("hw_genie.runner.urllib.request.urlopen", fake_urlopen)
    runner._warn_if_bridge_unreachable("http://127.0.0.1:9/health?token=secret")
    assert captured["url"] == "http://127.0.0.1:9/health"


def test_run_for_account_key_insufficient_is_missing(monkeypatch):
    """x-auth-*を欠く保存済みヘッダはpreflightと同一メッセージでmissing扱い。"""
    from hw_genie.runner import run_for_account

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers",
        lambda acc: {"content-type": "application/json"},
    )
    called = []

    def routine(client, account):
        called.append(account)
        return "x"

    acc, res, err = run_for_account("Alice", routine)
    assert acc == "Alice" and res is None
    assert isinstance(err, RuntimeError)
    assert str(err) == "Session not found for account 'Alice'."
    assert called == []


def test_multi_headers_ready_boundaries():
    """空dict・非dict・鍵不足の境界を明示する。"""
    from hw_genie.main import _multi_headers_ready

    assert _multi_headers_ready(None) is False
    assert _multi_headers_ready({}) is False
    assert _multi_headers_ready({"content-type": "application/json"}) is False
    assert _multi_headers_ready(["x-auth-token"]) is False
    assert _multi_headers_ready({"X-Auth-Token": "t"}) is True
    assert _multi_headers_ready({"x-auth-token": "t"}) is True


def test_preflight_key_insufficient_headers(monkeypatch):
    """x-auth-* を欠く保存済みヘッダは missing 扱いで prefailed になる。"""
    from hw_genie.main import _preflight_multi_accounts

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers",
        lambda acc: {"content-type": "application/json"} if acc == "Alice" else None,
    )
    ready, prefailed = _preflight_multi_accounts(["Alice", "Bob"])
    assert ready == []
    assert set(prefailed) == {"Alice", "Bob"}
    assert "Session not found for account 'Alice'." in str(prefailed["Alice"][1])


def test_cmd_multi_empty_accounts_records_ok(monkeypatch, capsys):
    """空accountsはワーカー投入なしで ok/0 件記録（明示ガード）。"""
    from hw_genie import main

    monkeypatch.setattr(
        "hw_genie.main._preflight_multi_accounts", lambda accounts: ([], {})
    )
    called = []

    def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        called.append(list(accounts or []))
        return {}

    records = {}
    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr(
        "hw_genie.core.run_log.record_run_log", lambda **k: records.update(k)
    )
    args = _toe_args()
    args.accounts = []
    monkeypatch.setattr(
        "hw_genie.runner.SessionManager.list_accounts", lambda: []
    )
    main.cmd_multi(args)  # exit しない = ok
    assert called == [[]] or called == []
    assert records["status"] == "ok"
    assert records["exit_code"] == 0
    assert records["accounts"] == []
    assert records["error_summary"] is None


def test_cmd_multi_duplicate_accounts_deduped(monkeypatch):
    """重複入力は初出順に一意化され、二重実行されない（仕様）。"""
    from hw_genie import main

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"}
    )
    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        captured["accounts"] = list(accounts or [])
        return {acc: (_idle_toe(), None) for acc in (accounts or [])}

    records = {}
    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr(
        "hw_genie.core.run_log.record_run_log", lambda **k: records.update(k)
    )
    main.cmd_multi(_toe_args("Alice", "Alice", "Bob"))
    assert captured["accounts"] == ["Alice", "Bob"]
    assert records["status"] == "ok"


def test_toe_interrupt_with_real_errors_is_failure():
    """interrupt＋実エラーは失敗（中断マーカー自体は失敗にしない）。"""
    from hw_genie.main import _build_run_log_summary, _run_log_account_failure

    summary = {
        "rival_results": [],
        "errors": [
            {"stage": "interrupted", "message": "interrupted by user"},
            {"stage": "rivals", "message": "boom"},
        ],
        "interrupted": True,
    }
    assert _run_log_account_failure("toe", summary, None) == "1 toe error(s)"
    entries, err_summary = _build_run_log_summary("toe", {"Alice": (summary, None)})
    assert entries == [
        {"account": "Alice", "ok": False, "error": "1 toe error(s)", "interrupted": True}
    ]
    assert err_summary == "1 account(s) failed: Alice (1 toe error(s))"


def test_non_toe_interrupt_is_real_failure():
    """非toeのInterruptedErrorはtoe意味論で漏洩させず実失敗になる。"""
    from hw_genie.main import _build_run_log_summary, _run_log_account_failure

    assert (
        _run_log_account_failure("daily", None, InterruptedError("interrupted by user"))
        == "interrupted by user"
    )
    entries, err_summary = _build_run_log_summary(
        "daily", {"Alice": (None, InterruptedError("interrupted by user"))}
    )
    assert entries == [
        {
            "account": "Alice",
            "ok": False,
            "error": "interrupted by user",
            "interrupted": False,
        }
    ]
    assert err_summary == "1 account(s) failed: Alice (interrupted by user)"


def test_cmd_multi_quests_dry_run_with_missing(monkeypatch):
    """dry-run＋欠落: 欠落は失敗エントリとして計画に混ざり exit 1。"""
    from hw_genie import main

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers",
        lambda acc: {"x-auth-token": "t"} if acc == "Alice" else None,
    )
    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        captured["accounts"] = list(accounts or [])
        return {"Alice": (([], [], []), None)}

    records = {}
    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr(
        "hw_genie.core.run_log.record_run_log", lambda **k: records.update(k)
    )
    args = _toe_args("Alice", "Bob")
    args.mode = "quests"
    args.dry_run = True
    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(args)
    assert exc.value.code == 1
    assert captured["accounts"] == ["Alice"]
    assert "Session not found for account 'Bob'." in (records["error_summary"] or "")


def test_invalid_streak_per_account_interleave(monkeypatch, capsys):
    """streakはaccount毎に独立し、他者の成功でリセットされない。"""
    from unittest.mock import MagicMock

    import hw_genie.commands.titan_arena as ta_mod
    from hw_genie.battle.engine import BattleEstimate, JsBridgeBattleEngine
    from hw_genie.commands.titan_arena import run_titan_arena
    from hw_genie.core.client import HWResponse, ResponseStatus

    ta_mod._INVALID_BATTLE_STREAKS.clear()
    ta_mod._INVALID_BATTLE_WARNED_ACCOUNTS.clear()
    ta_mod._INVALID_BATTLE_STREAK = 0
    ta_mod._INVALID_BATTLE_WARNED = False

    battle = {"type": "titan_arena", "seed": 1, "attackers": {}, "defenders": [{}]}

    def invalid_call(payload):
        name = payload["calls"][0]["name"]
        if name == "titanArenaStartBattle":
            return HWResponse(
                status=ResponseStatus.SUCCESS,
                detail={"response": {"battle": battle}},
            )
        return HWResponse(
            status=ResponseStatus.SUCCESS,
            detail={"response": {"error": "Invalid battle", "serverVersion": 1}},
        )

    client = MagicMock()
    client.call.side_effect = invalid_call
    eng = JsBridgeBattleEngine(auth_server_url="http://127.0.0.1:9", user_id="1")
    verified_progress = [
        {"attackers": {"heroes": {"1": {"id": 1}}}, "defenders": {"heroes": {"2": {"id": 2}}}}
    ]
    monkeypatch.setattr(
        eng, "calc", lambda battle, on_progress=None: BattleEstimate(True, 3, verified_progress)
    )
    for _ in range(3):
        run_titan_arena(
            client, rival_id="-1", titans=[1, 2, 3, 4, 5], engine=eng,
            progress="quiet", account_label="Alice",
        )
    # Bob の成功（Invalid 以外）は Alice の streak をリセットしない
    ta_mod._track_invalid_battle({"status": "ok"}, eng, "Bob")
    out = capsys.readouterr().out
    assert out.count("bridge may be disconnected") == 1
    # Bob 単発 Invalid では警告しない（Bob 側 streak=1）
    run_titan_arena(
        client, rival_id="-1", titans=[1, 2, 3, 4, 5], engine=eng,
        progress="quiet", account_label="Bob",
    )
    assert capsys.readouterr().out.count("bridge may be disconnected") == 0
    # estimate_only / abandoned は自accountのstreakをリセットする
    ta_mod._INVALID_BATTLE_STREAKS["Carol"] = 2
    ta_mod._reset_invalid_battle("Carol")
    assert "Carol" not in ta_mod._INVALID_BATTLE_STREAKS


def test_userscript_hint_shown_once_and_invalid_streak_warns(monkeypatch, capsys):
    """初回 bridge 失敗で hint 1 回、hybrid＋Invalid 連発で未接続警告。"""
    from unittest.mock import MagicMock

    import hw_genie.commands.titan_arena as ta_mod
    from hw_genie.battle.engine import (
        BattleEstimate,
        BridgeError,
        JsBridgeBattleEngine,
    )
    from hw_genie.commands.titan_arena import run_titan_arena
    from hw_genie.core.client import HWResponse, ResponseStatus

    ta_mod._BRIDGE_USERSCRIPT_HINT_SHOWN = False
    ta_mod._INVALID_BATTLE_STREAK = 0
    ta_mod._INVALID_BATTLE_WARNED = False

    battle = {"type": "titan_arena", "seed": 1, "attackers": {}, "defenders": [{}]}

    def ok_call(payload):
        name = payload["calls"][0]["name"]
        if name == "titanArenaStartBattle":
            return HWResponse(
                status=ResponseStatus.SUCCESS,
                detail={"response": {"battle": battle}},
            )
        raise AssertionError(name)

    client = MagicMock()
    client.call.side_effect = ok_call

    class DeadBridge(JsBridgeBattleEngine):
        def calc(self, battle, on_progress=None):
            raise BridgeError("auth-server 404")

    eng = DeadBridge(auth_server_url="http://127.0.0.1:9", user_id="1")
    run_titan_arena(
        client, rival_id="-1", titans=[1, 2, 3, 4, 5], engine=eng, progress="quiet"
    )
    run_titan_arena(
        client, rival_id="-1", titans=[1, 2, 3, 4, 5], engine=eng, progress="quiet"
    )
    out = capsys.readouterr().out
    assert out.count("ensure `hw-genie auth-server` is running") == 1

    ta_mod._INVALID_BATTLE_STREAK = 0
    ta_mod._INVALID_BATTLE_WARNED = False

    def invalid_call(payload):
        name = payload["calls"][0]["name"]
        if name == "titanArenaStartBattle":
            return HWResponse(
                status=ResponseStatus.SUCCESS,
                detail={"response": {"battle": battle}},
            )
        return HWResponse(
            status=ResponseStatus.SUCCESS,
            detail={"response": {"error": "Invalid battle", "serverVersion": 1}},
        )

    client2 = MagicMock()
    client2.call.side_effect = invalid_call
    eng2 = JsBridgeBattleEngine(auth_server_url="http://127.0.0.1:9", user_id="1")
    verified_progress = [
        {"attackers": {"heroes": {"1": {"id": 1}}}, "defenders": {"heroes": {"2": {"id": 2}}}}
    ]
    monkeypatch.setattr(
        eng2, "calc", lambda battle, on_progress=None: BattleEstimate(True, 3, verified_progress)
    )
    for _ in range(4):
        run_titan_arena(
            client2,
            rival_id="-1",
            titans=[1, 2, 3, 4, 5],
            engine=eng2,
            progress="quiet",
        )
    out2 = capsys.readouterr().out
    assert "bridge may be disconnected" in out2
    assert out2.count("bridge may be disconnected") == 1


def test_cmd_multi_toe_all_missing_makes_no_probe(monkeypatch):
    """全欠落時はプローブ自体が起きない（2sブロッキングなし）。"""
    import urllib.request

    from hw_genie import main

    monkeypatch.setattr("hw_genie.runner.load_session_headers", lambda acc: None)
    calls = []

    def _must_not_probe(req, timeout=None):
        calls.append(req)
        raise AssertionError("must not probe")

    monkeypatch.setattr(urllib.request, "urlopen", _must_not_probe)
    monkeypatch.setattr(
        "hw_genie.main.run_all_accounts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no workers expected"))
    )
    records = {}
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: records.update(k))
    with pytest.raises(SystemExit) as exc:
        main.cmd_multi(_toe_args("Alice", "Bob"))
    assert exc.value.code == 1
    assert calls == []
    assert records["status"] == "failed"


def test_cmd_multi_toe_ready_probes_exactly_once(monkeypatch):
    """ready がある hybrid run はプローブ1回・警告1回・中断なし。"""
    import urllib.request

    from hw_genie import main

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"}
    )
    calls = []

    def _refused(req, timeout=None):
        calls.append(req)
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    monkeypatch.setattr(
        "hw_genie.runner.urllib.request.urlopen", _refused, raising=False
    )
    monkeypatch.setattr(
        "hw_genie.main.run_all_accounts",
        lambda routine, accounts=None, max_parallel=None, cooperative_wait=False: {
            acc: (_idle_toe(), None) for acc in (accounts or [])
        },
    )
    records = {}
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: records.update(k))
    main.cmd_multi(_toe_args("Alice"))  # exit しない = ok、中断なし
    assert len(calls) == 1
    assert records["status"] == "ok"


def test_toe_routine_probe_bridge_false_skips_probe(monkeypatch):
    """factory 抑止フラグでプローブが起きない。"""
    import urllib.request

    calls = []

    def _must_not_probe2(req, timeout=None):
        calls.append(req)
        raise AssertionError("must not probe")

    monkeypatch.setattr(urllib.request, "urlopen", _must_not_probe2)
    run = runner.toe_routine(engine="hybrid", auth_server_url="http://127.0.0.1:9", probe_bridge=False)
    assert callable(run)
    assert calls == []


def test_cmd_multi_toe_passes_cooperative_wait(monkeypatch):
    """toe モードは run_all_accounts に cooperative_wait=True を渡す。"""
    from hw_genie import main

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"}
    )
    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        captured["cooperative_wait"] = cooperative_wait
        return {acc: (_idle_toe(), None) for acc in (accounts or [])}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)
    main.cmd_multi(_toe_args("Alice"))
    assert captured["cooperative_wait"] is True


def test_cmd_multi_non_toe_omits_cooperative_wait(monkeypatch):
    """非 toe モードは cooperative_wait=False（既定）で実行する。"""
    from hw_genie import main

    monkeypatch.setattr(
        "hw_genie.runner.load_session_headers", lambda acc: {"x-auth-token": "t"}
    )
    captured = {}

    def fake_run(routine, accounts=None, max_parallel=None, cooperative_wait=False):
        captured["cooperative_wait"] = cooperative_wait
        return {}

    monkeypatch.setattr("hw_genie.main.run_all_accounts", fake_run)
    monkeypatch.setattr("hw_genie.core.run_log.record_run_log", lambda **k: None)
    args = type(
        "A",
        (),
        {"mode": "daily", "accounts": ["Alice"], "parallel": 1, "debug": False},
    )()
    main.cmd_multi(args)
    assert captured["cooperative_wait"] is False
