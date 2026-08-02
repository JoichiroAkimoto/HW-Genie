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


def test_refresh_all_accounts_default_alias_renames_to_player_name():
    """alias=default でも対象は実名へリネームされる（旧 default エイリアスの解消）。

    player_id は UNIQUE 制約のため、実名で保存すると同じ行の alias が
    実名に更新され "default" が消滅する。
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
    assert mock_save.call_args[0] == (info, "Alice")  # 実名で保存され default は解消


def test_refresh_one_default_renames_alias_to_player_name():
    """実 DB で _refresh_one("default") が alias を実名へリネームする（回帰防止）。

    旧 "default" エイリアスは実名保存により player_id の UNIQUE 制約で
    同じ行の alias が実名に更新される。
    """
    SessionManager.save("default", {"player": {"id": "d1", "name": "Old", "level": 100}})
    assert "default" in SessionManager.list_accounts()

    with patch.object(
        auth_module, "load_session_headers", return_value={"x-auth-token": "your-token"}
    ), patch.object(auth_module, "get_user_info", return_value=_success_info("Alice", "d1")):
        err = auth_module._refresh_one("default")

    assert err is None
    accounts = SessionManager.list_accounts()
    assert "default" not in accounts  # default エイリアスは解消される
    assert "Alice" in accounts  # 実名エイリアスになる
    assert SessionManager.load("Alice")["player"]["name"] == "Alice"  # 値は更新される


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


def test_update_session_with_headers_none_alias_uses_real_name():
    """account_alias=None（-a なし / auth server 経由）でもクラッシュせず実名で保存される。"""
    info = _success_info("Alice", "a1")
    with patch.object(auth_module, "get_user_info", return_value=info) as mock_fetch, patch.object(
        auth_module, "save_session"
    ) as mock_save:
        result = auth_module.update_session_with_headers({"x-auth-token": "t"}, None)

    assert result["status"] == "success"
    # 実名 1 回のみ保存（None エイリアスでは追加保存しない）
    mock_fetch.assert_called_once()
    assert mock_save.call_count == 1
    assert mock_save.call_args[0] == (info, "Alice")


def test_update_session_with_headers_explicit_alias_saves_both_or_alias():
    """-a で別名を指定すると実名と別名の両方を保存する。"""
    info = _success_info("Alice", "a1")
    with patch.object(auth_module, "get_user_info", return_value=info), patch.object(
        auth_module, "save_session"
    ) as mock_save:
        auth_module.update_session_with_headers({"x-auth-token": "t"}, "sub1")

    saved_accounts = [call.args[1] for call in mock_save.call_args_list]
    assert saved_accounts == ["Alice", "sub1"]


def test_update_session_with_headers_default_alias_does_not_save_default():
    """account_alias="default"（旧エイリアス）は実名のみ保存され、default 行は作られない。"""
    info = _success_info("Alice", "a1")
    with patch.object(auth_module, "get_user_info", return_value=info), patch.object(
        auth_module, "save_session"
    ) as mock_save:
        auth_module.update_session_with_headers({"x-auth-token": "t"}, "default")

    saved_accounts = [call.args[1] for call in mock_save.call_args_list]
    assert saved_accounts == ["Alice"]  # default は保存されない


def test_cmd_auth_multi_account_no_arg_raises_ambiguity(capsys):
    """複数アカウント登録済みで -a なしの情報表示は AccountAmbiguityError になる。"""
    from hw_genie.core.client import AccountAmbiguityError

    _save_accounts()
    args = SimpleNamespace(
        account=None, curl=None, update=None, memo=None, info=True,
        list=False, list_names=False, fresh=False,
    )
    with pytest.raises(AccountAmbiguityError):
        cmd_auth(args)


def test_cmd_auth_curl_first_registration_no_account_arg(capsys):
    """DB 空の状態で --curl（-a なし）を実行すると、実名で登録できる（初回登録がブロックされない）。"""
    args = SimpleNamespace(
        account=None, curl='curl -H "x-auth-token: t" https://example.com',
        update=None, memo=None, info=False, list=False, list_names=False, fresh=False,
    )
    with patch("hw_genie.main.extract_headers_from_curl", return_value={"x-auth-token": "t"}), patch(
        "hw_genie.core.auth.get_user_info", return_value=_success_info("NewPlayer", "np1")
    ):
        cmd_auth(args)

    # 実名で DB に保存される（default エイリアスは作られない）
    accounts = SessionManager.list_accounts()
    assert "NewPlayer" in accounts
    assert "default" not in accounts
    out = capsys.readouterr().out
    assert "Successfully updated session for NewPlayer" in out


def test_cmd_auth_curl_with_explicit_alias(capsys):
    """--curl に -a を付けると、実名と別名の両方ではなく別名で保存される。"""
    args = SimpleNamespace(
        account="sub1", curl='curl -H "x-auth-token: t" https://example.com',
        update=None, memo=None, info=False, list=False, list_names=False, fresh=False,
    )
    with patch("hw_genie.main.extract_headers_from_curl", return_value={"x-auth-token": "t"}), patch(
        "hw_genie.core.auth.get_user_info", return_value=_success_info("NewPlayer", "np1")
    ):
        cmd_auth(args)

    # player_id の UNIQUE 制約により別名 1 行だけになる（実名行は作られない）
    accounts = SessionManager.list_accounts()
    assert "sub1" in accounts
    assert "NewPlayer" not in accounts
    assert "default" not in accounts


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


def test_cmd_auth_list_never_truncates_long_memo(capsys, monkeypatch):
    """幅が広い端末では長い Memo が「...」なしで全文表示される。"""
    # 列幅は HWGENIE_TZ に依存するため、.env 有無に関わらず決定的にする
    monkeypatch.setenv("HWGENIE_TZ", "Asia/Tokyo")
    monkeypatch.setenv("COLUMNS", "120")
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice"}, "memo": "x" * 50})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)

    out = capsys.readouterr().out
    # 全文が欠落なく表示される（50 文字すべて）
    assert out.replace(" ", "").count("x") == 50
    # どの行も Memo 列幅（ヘッダーから導出）を超えない＝折り返し境界が正しい
    header = out.splitlines()[1]
    memo_col_width = len(header) - header.rindex(" | ") - 3
    assert "x" * (memo_col_width + 1) not in out
    assert "..." not in out


def test_cmd_auth_list_continuation_rows_blank_fixed_columns(capsys, monkeypatch):
    """継続行は固定列が空白埋めになり、アカウント名は1行目のみ。"""
    monkeypatch.setenv("COLUMNS", "120")
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice"}, "memo": "first line\nsecond line\nthird line"})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)

    out = capsys.readouterr().out
    for fragment in ("first line", "second line", "third line"):
        assert fragment in out
    assert out.count("Alice") == 1
    # 継続行は Name 列が空（"Alice" の5幅ぶん）で始まる
    assert "\n" + " " * 5 + " |" in out


def test_cmd_auth_list_wraps_memo_on_narrow_terminal(capsys, monkeypatch):
    """狭い端末では Memo が複数行に折り返され、内容が欠落しない。"""
    monkeypatch.setenv("COLUMNS", "60")
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice"}, "memo": "ABC DEF GHI JKL MNO"})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)

    out = capsys.readouterr().out
    for fragment in ("ABC DEF", "GHI JKL", "MNO"):
        assert fragment in out
    # メモ全体は 19 幅あるため、列幅 ≤10 なら必ず折り返しが発生する
    # （ヘッダーから導出し、列幅が広がって空回りするのを防ぐ）
    hdr = out.splitlines()[1]
    memo_col = len(hdr) - hdr.rindex(" | ") - 3
    assert memo_col <= 10
    assert "..." not in out


def test_cmd_auth_list_memo_width_floors_at_ten(capsys, monkeypatch):
    """Memo 列は最小 10 幅でクランプされ、それより狭い端末でも同じ描画になる。"""
    # 列幅は HWGENIE_TZ に依存するため、.env 有無に関わらず決定的にする
    monkeypatch.setenv("HWGENIE_TZ", "Asia/Tokyo")
    memo = "x" * 50
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice"}, "memo": memo})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)

    # 固定列合計はヘッダーだけで 45 幅を超えるため、COLUMNS=40/60 はどちらも
    # フロア 10 に張り付く（ヘッダー改名等で固定列が多少変わっても成立する堅牢な値）
    monkeypatch.setenv("COLUMNS", "40")
    cmd_auth(args)
    out_40 = capsys.readouterr().out

    monkeypatch.setenv("COLUMNS", "60")  # フロア 10 に張り付く
    cmd_auth(args)
    out_60 = capsys.readouterr().out

    assert out_40 == out_60
    assert out_60.replace(" ", "").count("x") == 50


def test_cmd_auth_list_columns_are_content_driven(capsys, monkeypatch):
    """固定列幅は最長セルに合わせて調整され、名前は省略されない。"""
    monkeypatch.setenv("HWGENIE_TZ", "Asia/Tokyo")
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)

    SessionManager.save("Al", {"player": {"id": "a1", "name": "Al"}})
    cmd_auth(args)
    short = capsys.readouterr().out
    # Name 列 = max(ヘッダー "Name"=4, "Al"=2) = 4 → 最初の「 | 」は 4 文字目
    assert short.splitlines()[1].index(" | ") == 4

    SessionManager.save(
        "AQuiteLongAccountName",
        {"player": {"id": "a2", "name": "AQuiteLongAccountName"}},
    )
    cmd_auth(args)
    long = capsys.readouterr().out
    # 長い名前は省略されず全文表示され、Name 列が内容に合わせて伸びる
    assert "AQuiteLongAccountName" in long
    assert long.splitlines()[1].index(" | ") >= 20


def test_cmd_auth_list_plain_when_not_tty(capsys):
    """非 TTY（パイプ・ログ）では ANSI コードが出力されない。"""
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice", "level": 130, "energy": 5000, "arena_rank": 1}})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)
    assert "\033[" not in capsys.readouterr().out


def test_cmd_auth_list_colors_when_supported(capsys, monkeypatch):
    """TTY ではヘッダー・名前・順位・エネルギー超過が意味別に色付けされる。"""
    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    monkeypatch.setenv("HWGENIE_TZ", "Asia/Tokyo")
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice", "level": 130, "energy": 5000, "arena_rank": 1, "grand_rank": 5}})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)

    out = capsys.readouterr().out
    assert "\033[1;36m" in out  # ヘッダー太字+シアン
    assert "\033[1m" in out  # 名前太字
    assert "\033[33m" in out  # Arena 1位 = 金
    assert "\033[32m" in out  # GA 5位 = 緑
    assert "\033[31m" in out  # Energy 5000 > 上限 190 = 赤


def test_cmd_auth_list_rule_width_matches_plain_header(capsys, monkeypatch):
    """罫線はプレーンなヘッダー幅と一致する（ANSI コードを幅に数えない）。"""
    import re

    from hw_genie.core.utils import display_width

    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice", "level": 130, "energy": 39, "arena_rank": 2}})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)

    lines = capsys.readouterr().out.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if "\033[1;36m" in line)
    rule_line = lines[header_idx + 1]
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    assert display_width(ansi.sub("", lines[header_idx])) == display_width(ansi.sub("", rule_line))


def test_cmd_auth_list_continuation_same_color_as_first_line(capsys, monkeypatch):
    """継続行は 1 行目と同じ色（非ゼブラ行では dim にならない）。"""
    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    monkeypatch.setenv("COLUMNS", "120")
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice"}, "memo": "first line\nsecond line"})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)

    lines = capsys.readouterr().out.splitlines()
    continuation = next(line for line in lines if "second line" in line)
    assert "\033[2m" not in continuation


def test_cmd_auth_list_zebra_dims_even_rows(capsys, monkeypatch):
    """偶数番目のアカウント行は全体が dim され、行の区別がつく。"""
    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice", "level": 130, "energy": 39, "arena_rank": 2}})
    SessionManager.save("Bob", {"player": {"id": "b1", "name": "Bob", "level": 130, "energy": 39, "arena_rank": 3}})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)

    lines = capsys.readouterr().out.splitlines()
    first = next(line for line in lines if "Alice" in line)
    second = next(line for line in lines if "Bob" in line)
    assert first.startswith("\033[1m") and not first.startswith("\033[1;2m")
    assert second.startswith("\033[1;2m")


def test_cmd_auth_list_energy_not_red_below_cap(capsys, monkeypatch):
    """上限以下の Energy は赤にならない（低スタミナは無色）。"""
    monkeypatch.setattr("hw_genie.core.utils.supports_color", lambda stream=None: True)
    SessionManager.save("Alice", {"player": {"id": "a1", "name": "Alice", "level": 130, "energy": 39, "arena_rank": 2}})
    args = SimpleNamespace(fresh=False, list=True, list_names=False, account=None)
    cmd_auth(args)

    out = capsys.readouterr().out
    assert "\033[31m" not in out
    assert "\033[32m" in out  # Arena 2位 = 緑 は付く
