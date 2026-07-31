"""auth --list --fresh (refresh_all_accounts) のテスト。"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hw_genie.core import auth as auth_module
from hw_genie.core.session_manager import SessionManager
from hw_genie.main import cmd_auth


def _save_accounts():
    """テスト用に 2 アカウントを DB へ保存する。アカウント名は任意（実在しなくてよい）。"""
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice"}})
    SessionManager.save("Bob", {"player": {"id": "b1", "name": "Bob"}})


def _success_info(name: str, player_id: str = "p1") -> dict:
    return {
        "headers": {"x-auth-token": "your-token"},
        "status": "success",
        "player": auth_module.PlayerStatus(
            id=player_id, name=name, level=130, gold=100, gems=10, energy=5, arena_rank=1, grand_rank=2
        ),
    }


def test_refresh_all_accounts_success():
    """全アカウントが成功した場合は error が None になる。"""
    with patch.object(
        auth_module, "load_session_headers", return_value={"x-auth-token": "your-token"}
    ), patch.object(
        auth_module, "get_user_info", side_effect=lambda headers: _success_info("Alice")
    ) as mock_fetch, patch.object(auth_module, "save_session") as mock_save:
        results = auth_module.refresh_all_accounts(["Alice"])

    assert results == [("Alice", None)]
    mock_fetch.assert_called_once()
    # 対象アカウントのみ 1 回の保存（"default" や実名への余分な書き込みなし）
    assert mock_save.call_count == 1
    assert mock_save.call_args[0][1] == "Alice"


def test_refresh_all_accounts_default_alias_saves_only_default():
    """alias=default でも対象エイリアスへ 1 書き込みのみ（実名エイリアスは作らない）。

    player_id は UNIQUE 制約のため、同一プレイヤーを実名でも保存すると
    同じ行の alias がリネームされ "default" が消滅してしまう。
    """
    info = _success_info("Alice")
    with patch.object(
        auth_module, "load_session_headers", return_value={"x-auth-token": "your-token"}
    ), patch.object(auth_module, "get_user_info", return_value=info) as mock_fetch, patch.object(
        auth_module, "save_session"
    ) as mock_save:
        results = auth_module.refresh_all_accounts(["default"])

    assert results == [("default", None)]
    mock_fetch.assert_called_once()
    assert mock_save.call_count == 1
    assert mock_save.call_args[0] == (info, "default")


def test_refresh_one_default_keeps_alias_in_db():
    """実 DB で _refresh_one("default") が alias をリネームしない（回帰防止）。

    player_id が UNIQUE のため、実名への追加保存は "default" 行のリネームに
    なり、load_session_headers の既定エイリアスが消滅する。更新は対象
    エイリアスのみに行い、実名エイリアスは作られないことを検証する。
    （インメモリ DB はスレッドごとに別物になるため _refresh_one を直接呼ぶ）
    """
    SessionManager.save("default", {"player": {"id": "d1", "name": "Old", "level": 100}})
    assert "default" in SessionManager.list_accounts()

    with patch.object(
        auth_module, "load_session_headers", return_value={"x-auth-token": "your-token"}
    ), patch.object(auth_module, "get_user_info", return_value=_success_info("Alice", "d1")):
        err = auth_module._refresh_one("default")

    assert err is None
    accounts = SessionManager.list_accounts()
    assert "default" in accounts  # "default" エイリアスは保持される
    assert "Alice" not in accounts  # 実名エイリアスは作られない
    assert SessionManager.load("default")["player"]["name"] == "Alice"  # 値は更新される


def test_refresh_all_accounts_missing_session():
    """セッション未登録のアカウントはエラーメッセージになる。"""
    with patch.object(
        auth_module, "load_session_headers", return_value=None
    ), patch.object(auth_module, "get_user_info") as mock_fetch, patch.object(
        auth_module, "save_session"
    ) as mock_save:
        results = auth_module.refresh_all_accounts(["Ghost"])

    assert results == [("Ghost", "Session not found for account 'Ghost'.")]
    mock_fetch.assert_not_called()
    mock_save.assert_not_called()


def test_refresh_all_accounts_api_error():
    """API エラー（セッション失効等）はメッセージとして捕捉され、保存しない。"""
    with patch.object(
        auth_module, "load_session_headers", return_value={"x-auth-token": "your-token"}
    ), patch.object(
        auth_module, "get_user_info",
        return_value={"status": "error", "message": "Auth failed"},
    ), patch.object(auth_module, "save_session") as mock_save:
        results = auth_module.refresh_all_accounts(["Alice"])

    assert results == [("Alice", "Alice: Auth failed")]
    mock_save.assert_not_called()


def test_refresh_all_accounts_exception_is_isolated():
    """1 アカウントの例外が他アカウントの更新を妨げない。"""

    def fake_fetch(headers):
        if headers["x-auth-token"] == "Bad":
            raise RuntimeError("boom")
        return _success_info("OK")

    with patch.object(
        auth_module, "load_session_headers", side_effect=lambda acc: {"x-auth-token": acc}
    ), patch.object(auth_module, "get_user_info", side_effect=fake_fetch), patch.object(
        auth_module, "save_session"
    ) as mock_save:
        results = auth_module.refresh_all_accounts(["OK", "Bad"])

    by_account = dict(results)
    assert by_account["OK"] is None
    assert by_account["Bad"] == "boom"
    # OK は保存され、Bad は保存されない
    assert mock_save.call_count == 1
    assert mock_save.call_args[0][1] == "OK"


def test_refresh_all_accounts_empty_message_exception_reports_type():
    """空メッセージの例外は例外型名で失敗として報告される。"""
    with patch.object(
        auth_module, "load_session_headers", return_value={"x-auth-token": "your-token"}
    ), patch.object(auth_module, "get_user_info", side_effect=lambda h: (_ for _ in ()).throw(RuntimeError())), patch.object(
        auth_module, "save_session"
    ) as mock_save:
        results = auth_module.refresh_all_accounts(["Alice"])

    assert results == [("Alice", "RuntimeError")]
    mock_save.assert_not_called()


def test_refresh_all_accounts_empty():
    """アカウント 0 件は空リストを返す。"""
    assert auth_module.refresh_all_accounts([]) == []


def test_wal_writes_share_single_process_lock():
    """update_config と on-connect の sync は同一のリエントラントロックで直列化される。

    --fresh の並列更新は複数スレッドが同一ローカルレプリカの WAL に書き込む
    ため、ロックが共有されていないと wal_insert_begin failed が毎回発生する。
    書き込みパスは接続作成時の sync() を再入するため RLock である必要がある。
    """
    from hw_genie.core import database as db_module
    from hw_genie.core import repository as repo_module

    assert repo_module._wal_io_lock is db_module._wal_io_lock
    lock = repo_module._wal_io_lock
    lock.acquire()
    lock.acquire()  # リエントラント: 素の Lock だとここでデッドロック
    lock.release()
    lock.release()


def test_cmd_auth_fresh_requires_list(capsys):
    """--fresh 単体（--list なし）はエラーメッセージとともに終了する。"""
    args = SimpleNamespace(fresh=True, list=False, list_names=False, account=None)
    with pytest.raises(SystemExit) as exc_info:
        cmd_auth(args)
    assert exc_info.value.code == 1
    assert "--fresh requires --list" in capsys.readouterr().err


def test_cmd_auth_fresh_with_account_refreshes_only_that_account(capsys, monkeypatch):
    """--list --fresh -a Alice は Alice のみ最新化して表示する。"""
    _save_accounts()
    monkeypatch.delenv("HWDA_MAX_PARALLEL", raising=False)
    with patch(
        "hw_genie.core.auth.refresh_all_accounts",
        return_value=[("Alice", None)],
    ) as mock_refresh:
        args = SimpleNamespace(fresh=True, list=True, list_names=False, account="Alice")
        cmd_auth(args)

    mock_refresh.assert_called_once_with(["Alice"], max_parallel=1)
    out = capsys.readouterr()
    assert "Alice" in out.out
    assert "Bob" in out.out


def test_cmd_auth_fresh_failure_shows_warning_and_old_values(capsys):
    """取得失敗時は stderr に警告し、DB の旧値で一覧表示を続行する。"""
    _save_accounts()
    with patch(
        "hw_genie.core.auth.refresh_all_accounts",
        return_value=[("Alice", "Alice: Auth failed"), ("Bob", None)],
    ):
        args = SimpleNamespace(fresh=True, list=True, list_names=False, account=None)
        cmd_auth(args)

    captured = capsys.readouterr()
    assert "Could not refresh 1 account(s)" in captured.err
    assert "Alice: Auth failed" in captured.err
    # 一覧は表示される（失敗アカウントも旧値のまま）
    assert "Name" in captured.out
    assert "Bob" in captured.out


def test_cmd_auth_fresh_with_list_names_is_rejected(capsys):
    """--fresh と --list-names の併用はエラーメッセージとともに終了する。"""
    args = SimpleNamespace(fresh=True, list=False, list_names=True, account=None)
    with pytest.raises(SystemExit) as exc_info:
        cmd_auth(args)
    assert exc_info.value.code == 1
    assert "--fresh cannot be combined with --list-names" in capsys.readouterr().err


def test_cmd_auth_fresh_list_with_list_names_is_rejected(capsys):
    """--fresh --list --list-names の 3 併用も --fresh が黙って無視されずエラーになる。"""
    args = SimpleNamespace(fresh=True, list=True, list_names=True, account=None)
    with pytest.raises(SystemExit) as exc_info:
        cmd_auth(args)
    assert exc_info.value.code == 1
    assert "--fresh cannot be combined with --list-names" in capsys.readouterr().err


def test_cmd_auth_list_without_fresh_skips_refresh(capsys):
    """--fresh なしの --list は refresh_all_accounts を呼ばない。"""
    _save_accounts()
    with patch(
        "hw_genie.core.auth.refresh_all_accounts",
    ) as mock_refresh:
        args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
        cmd_auth(args)

    mock_refresh.assert_not_called()
    assert "Bob" in capsys.readouterr().out


def test_cmd_auth_fresh_reflects_new_values_in_table(capsys):
    """--fresh の取得結果が DB を経由して一覧表に反映される（配線の検証）。"""

    def fake_refresh(accounts, max_parallel=4):
        # refresh_all_accounts の実挙動を模し、実 DB へ書き込む
        for acc in accounts:
            SessionManager.save(
                acc,
                {"player": {"id": "a1" if acc == "Alice" else "b1",
                            "name": acc, "gold": 31415926}},
            )
        return [(acc, None) for acc in accounts]

    _save_accounts()
    with patch(
        "hw_genie.core.auth.refresh_all_accounts",
        side_effect=fake_refresh,
    ):
        args = SimpleNamespace(fresh=True, list=True, list_names=False, account=None)
        cmd_auth(args)

    out = capsys.readouterr().out
    assert "31.4M" in out
