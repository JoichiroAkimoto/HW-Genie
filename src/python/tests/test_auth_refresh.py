"""auth --list --fresh (refresh_all_accounts) のテスト。"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hw_genie.core import auth as auth_module
from hw_genie.core.session_manager import SessionManager
from hw_genie.main import cmd_auth


def _save_accounts():
    """テスト用に 2 アカウントを DB へ保存する。"""
    SessionManager.save("Joe", {"player": {"id": "j1", "name": "Joe"}})
    SessionManager.save("VitaminD", {"player": {"id": "v1", "name": "VitaminD"}})


def _success_info(name: str) -> dict:
    return {
        "headers": {"x-auth-token": "t"},
        "status": "success",
        "player": auth_module.PlayerStatus(
            name=name, level=130, gold=100, gems=10, energy=5, arena_rank=1, grand_rank=2
        ),
    }


def test_refresh_all_accounts_success():
    """全アカウントが成功した場合は error が None になる。"""
    with patch.object(
        auth_module, "load_session_headers", return_value={"x-auth-token": "t"}
    ), patch.object(
        auth_module, "get_user_info", side_effect=lambda headers: _success_info("Joe")
    ) as mock_fetch, patch.object(auth_module, "save_session") as mock_save:
        results = auth_module.refresh_all_accounts(["Joe"])

    assert results == [("Joe", None)]
    mock_fetch.assert_called_once()
    # 対象アカウントのみ 1 回の保存（"default" や実名への余分な書き込みなし）
    assert mock_save.call_count == 1
    assert mock_save.call_args[0][1] == "Joe"


def test_refresh_all_accounts_default_alias_preserves_player_name():
    """alias=default（単一アカウント運用）では実名エイリアスも維持する。"""
    info = _success_info("Joe")
    with patch.object(
        auth_module, "load_session_headers", return_value={"x-auth-token": "t"}
    ), patch.object(auth_module, "get_user_info", return_value=info) as mock_fetch, patch.object(
        auth_module, "save_session"
    ) as mock_save:
        results = auth_module.refresh_all_accounts(["default"])

    assert results == [("default", None)]
    mock_fetch.assert_called_once()
    assert mock_save.call_count == 2
    assert mock_save.call_args_list[0][0] == (info, "default")
    assert mock_save.call_args_list[1][0] == (info, "Joe")


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
        auth_module, "load_session_headers", return_value={"x-auth-token": "t"}
    ), patch.object(
        auth_module, "get_user_info",
        return_value={"status": "error", "message": "Auth failed"},
    ), patch.object(auth_module, "save_session") as mock_save:
        results = auth_module.refresh_all_accounts(["Joe"])

    assert results == [("Joe", "Joe: Auth failed")]
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


def test_refresh_all_accounts_empty():
    """アカウント 0 件は空リストを返す。"""
    assert auth_module.refresh_all_accounts([]) == []


def test_cmd_auth_fresh_requires_list(capsys):
    """--fresh 単体（--list なし）はエラーメッセージとともに終了する。"""
    args = SimpleNamespace(fresh=True, list=False, list_names=False, account=None)
    with pytest.raises(SystemExit) as exc_info:
        cmd_auth(args)
    assert exc_info.value.code == 1
    assert "--fresh requires --list" in capsys.readouterr().err


def test_cmd_auth_fresh_with_account_refreshes_only_that_account(capsys):
    """--list --fresh -a Joe は Joe のみ最新化して表示する。"""
    _save_accounts()
    with patch(
        "hw_genie.core.auth.refresh_all_accounts",
        return_value=[("Joe", None)],
    ) as mock_refresh:
        args = SimpleNamespace(fresh=True, list=True, list_names=False, account="Joe")
        cmd_auth(args)

    mock_refresh.assert_called_once_with(["Joe"])
    out = capsys.readouterr()
    assert "Joe" in out.out
    assert "VitaminD" in out.out


def test_cmd_auth_fresh_failure_shows_warning_and_old_values(capsys):
    """取得失敗時は stderr に警告し、DB の旧値で一覧表示を続行する。"""
    _save_accounts()
    with patch(
        "hw_genie.core.auth.refresh_all_accounts",
        return_value=[("Joe", "Joe: Auth failed"), ("VitaminD", None)],
    ):
        args = SimpleNamespace(fresh=True, list=True, list_names=False, account=None)
        cmd_auth(args)

    captured = capsys.readouterr()
    assert "Could not refresh 1 account(s)" in captured.err
    assert "Joe: Auth failed" in captured.err
    # 一覧は表示される（失敗アカウントも旧値のまま）
    assert "Name" in captured.out
    assert "VitaminD" in captured.out


def test_cmd_auth_fresh_with_list_names_is_rejected(capsys):
    """--list-names との併用は --list を要求するエラーで終了する。"""
    args = SimpleNamespace(fresh=True, list=False, list_names=True, account=None)
    with pytest.raises(SystemExit) as exc_info:
        cmd_auth(args)
    assert exc_info.value.code == 1
    assert "--fresh requires --list" in capsys.readouterr().err


def test_cmd_auth_list_without_fresh_skips_refresh(capsys):
    """--fresh なしの --list は refresh_all_accounts を呼ばない。"""
    _save_accounts()
    with patch(
        "hw_genie.core.auth.refresh_all_accounts",
    ) as mock_refresh:
        args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
        cmd_auth(args)

    mock_refresh.assert_not_called()
    assert "VitaminD" in capsys.readouterr().out


def test_cmd_auth_fresh_reflects_new_values_in_table(capsys):
    """--fresh の取得結果が DB を経由して一覧表に反映される（配線の検証）。"""

    def fake_refresh(accounts):
        # refresh_all_accounts の実挙動を模し、実 DB へ書き込む
        for acc in accounts:
            SessionManager.save(
                acc,
                {"player": {"id": "j1" if acc == "Joe" else "v1",
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
