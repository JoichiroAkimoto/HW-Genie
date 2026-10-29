"""ギルド活動のグリフ強化（heroEnchantRune）自動化のテスト。

- ``guild_glyph_defaults`` の初期化・保存
- ``clanGetInfo.stat.activityForRuneAvailable`` による実行ゲート
- ``run_quest_execute`` への統合（dry-run / 実行 / 失敗 / 確認プロンプト）
"""

from unittest.mock import MagicMock

import pytest

from hw_genie.commands.quests import (
    GUILD_GLYPH_DEFAULTS_KEY,
    GUILD_GLYPH_LABEL,
    _guild_glyph_available,
    _guild_glyph_cycle_boundary,
    _guild_glyph_response,
    ensure_guild_glyph_defaults,
    run_quest_execute,
    set_guild_glyph_defaults,
)
from hw_genie.core.client import ApiAction, HWClient, ResponseStatus
from hw_genie.core.session_manager import SessionManager


# 実測値（2026-10-30 02:00 UTC = 次のサーバーリセット時刻）
SERVER_RESET_TIME = 1793325600


# --- ヘルパー ---


def _ok_response(detail: dict) -> MagicMock:
    res = MagicMock()
    res.status = ResponseStatus.SUCCESS
    res.error_name = None
    res.detail = detail
    return res


def _error_response(error: str) -> MagicMock:
    res = MagicMock()
    res.status = ResponseStatus.ERROR
    res.error_name = error
    res.detail = {}
    return res


def _register(account: str = "Alex") -> None:
    SessionManager.save(account, {"player": {"id": account, "name": account}, "headers": {"x-auth-token": "test"}})


def _stored(account: str = "Alex") -> dict:
    """保存済みの guild_glyph_defaults を読む（公開 getter は持たない）。"""
    return SessionManager.load(account).get(GUILD_GLYPH_DEFAULTS_KEY) or {}


def _force_config(account: str = "Alex", **values: object) -> None:
    """検証を迂回して設定を直接書き込む（手編集 DB の再現用）。"""
    SessionManager.repo.update_config_merged(
        account,
        GUILD_GLYPH_DEFAULTS_KEY,
        lambda existing: {**(existing if isinstance(existing, dict) else {}), **values},
    )


def _enable(account: str = "Alex", **overrides: object) -> None:
    _register(account)
    set_guild_glyph_defaults(account, "enabled", True)
    for key, value in overrides.items():
        set_guild_glyph_defaults(account, key, value)


def _clan_response(
    flag: bool | None,
    reset_time: int | None = SERVER_RESET_TIME,
) -> MagicMock:
    """実 API 形状（``result.response``）の clanGetInfo レスポンスを作る。"""
    stat = {} if flag is None else {"activityForRuneAvailable": flag}
    response: dict = {"stat": stat}
    if reset_time is not None:
        response["serverResetTime"] = reset_time
    return _ok_response({"response": response})


def _make_client(
    quests: list[dict] | None = None,
    flag: bool | None = True,
    account: str = "Alex",
    reset_time: int | None = SERVER_RESET_TIME,
) -> HWClient:
    _register(account)
    client = HWClient(headers={"x-auth-token": "test"})
    client.quest_get_all = MagicMock(return_value=_ok_response({"response": quests or []}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))
    client.call = MagicMock(return_value=_ok_response({"response": {}}))
    client.clan_get_info = MagicMock(return_value=_clan_response(flag, reset_time))
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    return client


# --- 設定の初期化・保存 ---


def test_ensure_seeds_disabled_with_recipe_defaults():
    _register()
    defaults = ensure_guild_glyph_defaults("Alex")

    assert defaults["enabled"] is False
    assert defaults["heroId"] == 46
    assert defaults["tier"] == 2
    assert defaults["items"] == {"consumable": {"1": 1}}
    assert defaults["note"] == ApiAction.HERO_ENCHANT_RUNE.value
    assert _stored("Alex") == defaults


def test_ensure_preserves_existing_values():
    _enable("Alex", heroId=61, tier=4, note="custom memo")
    defaults = ensure_guild_glyph_defaults("Alex")

    assert defaults["enabled"] is True
    assert defaults["heroId"] == 61
    assert defaults["tier"] == 4
    assert defaults["note"] == "custom memo"


def test_ensure_seeds_missing_items_only():
    _register()
    set_guild_glyph_defaults("Alex", "items", {"consumable": {"1": 3}})
    defaults = ensure_guild_glyph_defaults("Alex")

    assert defaults["items"] == {"consumable": {"1": 3}}
    # 他の既定値は引き続き補完される
    assert defaults["heroId"] == 46
    assert defaults["tier"] == 2


def test_recipe_defaults_not_shared_between_accounts():
    _register("Alex")
    _register("Bob")
    alex = ensure_guild_glyph_defaults("Alex")
    alex["items"]["consumable"]["1"] = 99

    bob = ensure_guild_glyph_defaults("Bob")
    assert bob["items"] == {"consumable": {"1": 1}}


def test_set_default_parses_json_items():
    _register()
    stored = set_guild_glyph_defaults("Alex", "items", '{"consumable": {"1": 2}}')

    assert stored == {"consumable": {"1": 2}}
    assert _stored("Alex")["items"] == {"consumable": {"1": 2}}


# --- 実行ゲート ---


def test_disabled_does_not_call_clan_get_info(capsys):
    client = _make_client(flag=True)

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.clan_get_info.assert_not_called()
    client.quest_operation.assert_not_called()
    assert succeeded == []
    assert failures == []
    assert "disabled" not in capsys.readouterr().out


def test_flag_false_skips_enchant(capsys):
    _enable()
    client = _make_client(flag=False)

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.clan_get_info.assert_called_once()
    client.quest_operation.assert_not_called()
    assert succeeded == []
    assert failures == []
    assert "already earned today" in capsys.readouterr().out


def test_flag_true_executes_enchant_once(capsys):
    _enable()
    client = _make_client(flag=True)

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_called_once_with(
        ApiAction.HERO_ENCHANT_RUNE,
        {"heroId": 46, "tier": 2, "items": {"consumable": {"1": 1}}},
    )
    assert failures == []
    assert succeeded == [{"account": "Alex", "quest_id": None, "quest_name": GUILD_GLYPH_LABEL}]
    assert "700 guild activity points" in capsys.readouterr().out


def test_custom_args_are_used():
    _enable(heroId=61, tier=4, items={"consumable": {"1": 2}})
    client = _make_client(flag=True)

    run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_called_once_with(
        ApiAction.HERO_ENCHANT_RUNE,
        {"heroId": 61, "tier": 4, "items": {"consumable": {"1": 2}}},
    )


def test_note_shown_in_output(capsys):
    _enable(note="Martha Magic Defense +10")
    client = _make_client(flag=True)

    run_quest_execute(client, account_alias="Alex", confirm=True)

    assert "Martha Magic Defense +10" in capsys.readouterr().out


# --- 失敗・判定不能 ---


def test_enchant_failure_reported(capsys):
    _enable()
    client = _make_client(flag=True)
    client.quest_operation = MagicMock(return_value=_error_response("NotEnough"))

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    assert succeeded == []
    assert len(failures) == 1
    assert failures[0]["quest_name"] == GUILD_GLYPH_LABEL
    assert failures[0]["step"] == ApiAction.HERO_ENCHANT_RUNE.value
    assert failures[0]["error"] == "NotEnough"
    assert "NotEnough" in capsys.readouterr().out


def test_clan_get_info_failure_reported(capsys):
    _enable()
    client = _make_client(flag=True)
    client.clan_get_info = MagicMock(return_value=_error_response("auth"))

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert succeeded == []
    assert failures[0]["step"] == "clanGetInfo"
    assert failures[0]["error"] == "auth"
    assert "clanGetInfo failed" in capsys.readouterr().out


def test_missing_flag_not_executed(capsys):
    _enable()
    client = _make_client(flag=None)

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert succeeded == []
    assert failures[0]["step"] == "clanGetInfo"
    assert "activityForRuneAvailable" in failures[0]["error"]
    assert "activityForRuneAvailable" in capsys.readouterr().out


def test_non_bool_flag_not_executed():
    _enable()
    client = _make_client(flag="true")  # 型不正（bool 以外）

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert failures[0]["step"] == "clanGetInfo"


def test_invalid_items_in_db_not_executed(capsys):
    _enable()
    _force_config(items="oops")  # 手編集 DB の再現（setter は検証で拒否する）
    client = _make_client(flag=True)

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert failures[0]["step"] == ApiAction.HERO_ENCHANT_RUNE.value
    assert "items" in failures[0]["error"]
    assert "cannot execute" in capsys.readouterr().out


def test_invalid_hero_id_in_db_not_executed():
    _enable()
    _force_config(heroId="abc")
    client = _make_client(flag=True)

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert "heroId" in failures[0]["error"]


def test_non_bool_enabled_in_db_not_executed():
    """手編集で enabled が真値文字列になっていても実行しない（安全側）。"""
    _enable()
    _force_config(enabled="true")
    client = _make_client(flag=True)

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.clan_get_info.assert_not_called()
    client.quest_operation.assert_not_called()
    assert failures == []


def test_non_dict_clan_detail_not_executed():
    _enable()
    client = _make_client(flag=True)
    client.clan_get_info = MagicMock(return_value=_ok_response([]))

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert failures[0]["step"] == "clanGetInfo"
    assert "unexpected clanGetInfo response format" in failures[0]["error"]


# --- 設定の検証 ---


def test_set_rejects_unknown_key():
    _register()
    with pytest.raises(ValueError, match="unknown guild_glyph_defaults key"):
        set_guild_glyph_defaults("Alex", "heroid", 61)


def test_set_rejects_non_bool_enabled():
    _register()
    with pytest.raises(ValueError, match="must be true or false"):
        set_guild_glyph_defaults("Alex", "enabled", "no")


def test_set_rejects_non_int_hero_id():
    _register()
    with pytest.raises(ValueError, match="must be an integer"):
        set_guild_glyph_defaults("Alex", "heroId", "abc")


def test_set_rejects_empty_items():
    _register()
    with pytest.raises(ValueError, match="items"):
        set_guild_glyph_defaults("Alex", "items", {})


def test_set_rejects_non_string_note():
    _register()
    with pytest.raises(ValueError, match="note"):
        set_guild_glyph_defaults("Alex", "note", 123)


# --- サイクル内の再実行防止（last_run_at） ---


def test_last_run_at_blocks_rerun_even_if_flag_true(capsys):
    _enable()
    _force_config(last_run_at=SERVER_RESET_TIME - 86400 + 60)  # 今サイクル内
    client = _make_client(flag=True)

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert succeeded == []
    assert failures == []
    assert "already ran this cycle" in capsys.readouterr().out


def test_last_run_at_previous_cycle_allows_run():
    _enable()
    _force_config(last_run_at=SERVER_RESET_TIME - 86400 - 1)  # 前サイクル
    client = _make_client(flag=True)

    run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_called_once()


def test_last_run_at_guard_disabled_without_reset_time():
    _enable()
    _force_config(last_run_at=SERVER_RESET_TIME + 10_000)
    client = _make_client(flag=True, reset_time=None)

    run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_called_once()


def test_success_stores_last_run_at():
    _enable()
    client = _make_client(flag=True)

    run_quest_execute(client, account_alias="Alex", confirm=True)

    assert isinstance(_stored("Alex").get("last_run_at"), int)


def test_failure_does_not_store_last_run_at():
    _enable()
    client = _make_client(flag=True)
    client.quest_operation = MagicMock(return_value=_error_response("NotEnough"))

    run_quest_execute(client, account_alias="Alex", confirm=True)

    assert _stored("Alex").get("last_run_at") is None


# --- 実 API 形状（result.response）の取り回し ---


def test_clan_get_info_unwraps_real_response_envelope():
    """call() が返す ``{"response": {...}}`` から stat/serverResetTime を読む。"""
    client = HWClient(headers={"x-auth-token": "test"})
    http = MagicMock()
    http.status_code = 200
    http.raise_for_status = MagicMock()
    http.json.return_value = {
        "results": [
            {
                "ident": "body",
                "result": {
                    "response": {
                        "stat": {"activityForRuneAvailable": True},
                        "serverResetTime": SERVER_RESET_TIME,
                    }
                },
            }
        ]
    }
    client.session.post = MagicMock(return_value=http)

    response = _guild_glyph_response(client.clan_get_info())

    assert response is not None
    assert _guild_glyph_available(response) is True
    assert _guild_glyph_cycle_boundary(response) == SERVER_RESET_TIME - 86400


def test_clan_get_info_real_envelope_drives_execution():
    """実形状のレスポンスで heroEnchantRune まで到達する（BLOCKER 回帰防止）。"""
    _enable()
    client = _make_client(flag=True)
    client.clan_get_info = MagicMock(return_value=_clan_response(True))

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_called_once()
    assert failures == []
    assert len(succeeded) == 1


# --- CLI（--set-default glyph / --init-defaults） ---


def test_cmd_quests_set_default_glyph_rejects_bad_value(capsys):
    import hw_genie.main as main_mod

    _register()

    class _Args:
        account = "Alex"
        edit_defaults = False
        init_defaults = False
        set_default = ("glyph", "enabled", "no")
        execute = False
        dry_run = False
        yes = False

    with pytest.raises(SystemExit) as exc_info:
        main_mod.cmd_quests(_Args())

    assert exc_info.value.code == 1
    assert "must be true or false" in capsys.readouterr().out


def test_cmd_quests_set_default_glyph_stores_value(capsys):
    import hw_genie.main as main_mod

    _register()

    class _Args:
        account = "Alex"
        edit_defaults = False
        init_defaults = False
        set_default = ("glyph", "enabled", "true")
        execute = False
        dry_run = False
        yes = False

    main_mod.cmd_quests(_Args())

    assert _stored("Alex")["enabled"] is True
    assert "Registered guild_glyph_defaults[enabled]" in capsys.readouterr().out


def test_cmd_quests_init_defaults_seeds_glyph(capsys):
    import hw_genie.main as main_mod

    _register()

    class _Args:
        account = "Alex"
        edit_defaults = False
        init_defaults = True
        set_default = None
        execute = False
        dry_run = False
        yes = False

    main_mod.cmd_quests(_Args())

    assert _stored("Alex")["enabled"] is False
    assert "guild glyph activity enabled=False" in capsys.readouterr().out


# --- dry-run ---


def test_dry_run_plans_without_executing(capsys):
    _enable(note="Martha Magic Defense +10")
    client = _make_client(flag=True)

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", dry_run=True, confirm=False)

    client.clan_get_info.assert_called_once()
    client.quest_operation.assert_not_called()
    assert succeeded == []
    assert failures == []
    out = capsys.readouterr().out
    assert "heroEnchantRune" in out
    assert "Martha Magic Defense +10" in out


def test_dry_run_flag_false_reports_skip(capsys):
    _enable()
    client = _make_client(flag=False)

    run_quest_execute(client, account_alias="Alex", dry_run=True, confirm=False)

    client.quest_operation.assert_not_called()
    assert "already earned today" in capsys.readouterr().out


def test_dry_run_disabled_does_not_call_clan_get_info(capsys):
    client = _make_client(flag=True)

    run_quest_execute(client, account_alias="Alex", dry_run=True, confirm=False)

    client.clan_get_info.assert_not_called()
    assert "disabled" in capsys.readouterr().out


# --- 確認プロンプト ---


def test_confirm_eof_reported(monkeypatch, capsys):
    _enable()
    client = _make_client(flag=True)

    def _eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=False)

    client.quest_operation.assert_not_called()
    assert failures[0]["error"] == "no interactive input (use --yes)"
    assert "no interactive input" in capsys.readouterr().out


def test_confirm_declined_reported(monkeypatch, capsys):
    _enable()
    client = _make_client(flag=True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=False)

    client.quest_operation.assert_not_called()
    assert failures[0]["error"] == "skipped by user"
    assert "user declined" in capsys.readouterr().out


def test_confirm_accepted_runs(monkeypatch):
    _enable()
    client = _make_client(flag=True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    succeeded, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=False)

    client.quest_operation.assert_called_once()
    assert failures == []
    assert len(succeeded) == 1
