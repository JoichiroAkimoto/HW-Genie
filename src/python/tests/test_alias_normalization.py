"""エイリアス正規化（strip / case-insensitive フォールバック）のテスト。

run_logs failed（id 54/55/59）で発生した「``champion`` / ``Champion␣`` のような
入力が登録エイリアス ``Champion`` と一致せず ``Account not found`` になる」問題の
回帰防止。各レイヤー（resolve_account / repository / session_manager /
Account.update_from_dict）での正規化を検証する。
"""

import pytest

from hw_genie.core.client import resolve_account
from hw_genie.core.database import Account
from hw_genie.core.session_manager import SessionManager


def _register(alias: str, player_id: str) -> None:
    SessionManager.repo.save_data(
        alias,
        {"headers": {"x-auth-token": "t"}, "player": {"id": player_id, "name": alias}},
    )


# --- resolve_account ---


def test_resolve_account_case_insensitive_fallback():
    """小文字入力は登録済み正規エイリアスに解決される。"""
    _register("Champion", "c1")
    assert resolve_account("champion") == "Champion"


def test_resolve_account_whitespace_fallback():
    """前後空白付き入力も正規エイリアスに解決される。"""
    _register("Champion", "c1")
    assert resolve_account("Champion ") == "Champion"
    assert resolve_account(" champion\t") == "Champion"


def test_resolve_account_exact_match_preferred():
    """完全一致があればそのまま返す。"""
    _register("Champion", "c1")
    _register("champion", "c2")
    assert resolve_account("Champion") == "Champion"


def test_resolve_account_unknown_returns_stripped_input():
    """未登録ならトリム済み入力をそのまま返す（従来挙動）。"""
    _register("Champion", "c1")
    assert resolve_account("Nobody") == "Nobody"


def test_resolve_account_none_single_account():
    """None 指定＋単一アカウントなら自動選択（従来挙動）。"""
    _register("Solo", "s1")
    assert resolve_account(None) == "Solo"


# --- repository (update_config_merged / get_data) ---


def test_update_config_merged_with_lowercase_alias():
    """update_config_merged が小文字エイリアスで正規行に書けること。"""
    _register("Champion", "c1")

    def merge(existing):
        return {"enabled": True}

    stored = SessionManager.repo.update_config_merged("champion", "quest_defaults", merge)
    assert stored == {"enabled": True}

    # 正規エイリアス Champion の行に保存されていること
    data = SessionManager.repo.get_data("Champion")
    assert data["quest_defaults"] == {"enabled": True}


def test_update_config_merged_with_padded_alias():
    """末尾空白付きエイリアスでも正規行に書けること。"""
    _register("The Best", "b1")

    stored = SessionManager.repo.update_config_merged("The Best ", "quest_guild_defaults", lambda existing: {"enabled": False})
    assert stored == {"enabled": False}
    data = SessionManager.repo.get_data("The Best")
    assert data["quest_guild_defaults"] == {"enabled": False}


def test_get_data_with_casing_variant():
    """get_data も大文字小文字違いで読めること。"""
    _register("Alex", "a1")
    data = SessionManager.repo.get_data("alex")
    assert data["player"]["name"] == "Alex"


def test_update_config_merged_unknown_alias_raises():
    """未登録エイリアスは従来どおり ValueError。"""
    with pytest.raises(ValueError):
        SessionManager.repo.update_config_merged("Ghost", "quest_defaults", lambda existing: {})


# --- session_manager ---


def test_session_save_resolves_canonical_alias():
    """save は既存正規エイリアス側に上書きする（新規行を作らない）。"""
    _register("Champion", "c1")

    SessionManager.save(
        "champion",
        {"headers": {"x-auth-token": "new"}, "player": {"id": "c1", "name": "Champion"}},
    )

    assert SessionManager.list_accounts() == ["Champion"]
    data = SessionManager.load("CHAMPION")
    assert data["headers"]["x-auth-token"] == "new"


def test_update_config_tx_keeps_existing_cased_alias():
    """_update_config_tx 経由の小文字保存でも正規行の alias がリネームされないこと。

    ``repo.save_data("champion", ...)`` は正規行 ``Champion`` に解決されるが、
    その際に入力値 ``champion`` で alias をリネームしてしまうとエイリアス揺れが
    再発する（run_logs failed id 54/55/59 の回帰防止）。
    """
    _register("Champion", "c1")

    SessionManager.repo.save_data(
        "champion",
        {"headers": {"x-auth-token": "updated"}, "player": {"id": "c1", "name": "Champion"}},
    )

    assert SessionManager.list_accounts() == ["Champion"]
    # ヘッダー更新は正規行へ反映されていること
    data = SessionManager.repo.get_data("Champion")
    assert data["headers"]["x-auth-token"] == "updated"


def test_session_save_exact_match_preferred_over_case_fallback():
    """``Champion`` と ``champion`` が別アカウントのとき save("champion") は
    champion 行へ書かれること（完全一致優先）。"""
    _register("Champion", "c_upper")
    _register("champion", "c_lower")

    # 両行の headers を初期化しておき、片側だけ更新する
    SessionManager.repo.update_config("Champion", {"headers": {"x-auth-token": "upper-old"}})
    SessionManager.repo.update_config("champion", {"headers": {"x-auth-token": "lower-old"}})

    SessionManager.save(
        "champion",
        {"headers": {"x-auth-token": "lower-new"}, "player": {"id": "c_lower", "name": "champion"}},
    )

    # 完全一致の champion 行だけが更新される
    lower = SessionManager.load("champion")
    assert lower["headers"]["x-auth-token"] == "lower-new"
    # Champion 行（case-insensitive のフォールバック先）は無傷
    upper = SessionManager.load("Champion")
    assert upper["headers"]["x-auth-token"] == "upper-old"
    # 2 行とも残っていること
    assert sorted(SessionManager.list_accounts()) == ["Champion", "champion"]


def test_resolve_account_blank_input_raises():
    """空白のみの入力は None 指定と同じく AccountNotFoundError。"""
    from hw_genie.core.client import AccountNotFoundError

    with pytest.raises(AccountNotFoundError):
        resolve_account("   ")
    with pytest.raises(AccountNotFoundError):
        resolve_account("\t\n ")


# --- Account.update_from_dict (player_name の strip) ---


def test_update_from_dict_strips_player_name():
    account = Account(player_id="p1", alias="X")
    account.update_from_dict({"name": "TestUser "})
    assert account.player_name == "TestUser"


def test_update_from_dict_keeps_non_string_name():
    account = Account(player_id="p1", alias="X")
    account.update_from_dict({"name": 123})
    assert account.player_name == 123
