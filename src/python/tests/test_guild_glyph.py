"""ギルド活動のグリフ強化（heroEnchantRune）自動化のテスト。

- ``guild_glyph_defaults`` の初期化・保存
- ``clanGetInfo.stat.activityForRuneAvailable`` による実行ゲート
- ``run_quest_execute`` への統合（dry-run / 実行 / 失敗 / 確認プロンプト）
"""

from unittest.mock import MagicMock

from hw_genie.commands.quests import (
    GUILD_GLYPH_LABEL,
    ensure_guild_glyph_defaults,
    get_guild_glyph_defaults,
    run_quest_execute,
    set_guild_glyph_defaults,
)
from hw_genie.core.client import ApiAction, HWClient, ResponseStatus
from hw_genie.core.session_manager import SessionManager


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


def _enable(account: str = "Alex", **overrides: object) -> None:
    _register(account)
    set_guild_glyph_defaults(account, "enabled", True)
    for key, value in overrides.items():
        set_guild_glyph_defaults(account, key, value)


def _clan_response(flag: bool | None) -> MagicMock:
    stat = {} if flag is None else {"activityForRuneAvailable": flag}
    return _ok_response({"stat": stat})


def _make_client(
    quests: list[dict] | None = None,
    flag: bool | None = True,
    account: str = "Alex",
) -> HWClient:
    _register(account)
    client = HWClient(headers={"x-auth-token": "test"})
    client.quest_get_all = MagicMock(return_value=_ok_response({"response": quests or []}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))
    client.call = MagicMock(return_value=_ok_response({"response": {}}))
    client.clan_get_info = MagicMock(return_value=_clan_response(flag))
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
    assert get_guild_glyph_defaults("Alex") == defaults


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
    assert get_guild_glyph_defaults("Alex")["items"] == {"consumable": {"1": 2}}


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


def test_invalid_items_not_executed(capsys):
    _enable()
    set_guild_glyph_defaults("Alex", "items", "oops")
    client = _make_client(flag=True)

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert failures[0]["step"] == ApiAction.HERO_ENCHANT_RUNE.value
    assert "items" in failures[0]["error"]
    assert "cannot execute" in capsys.readouterr().out


def test_invalid_hero_id_not_executed():
    _enable()
    set_guild_glyph_defaults("Alex", "heroId", "abc")
    client = _make_client(flag=True)

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert "heroId" in failures[0]["error"]


def test_non_dict_clan_detail_not_executed():
    _enable()
    client = _make_client(flag=True)
    client.clan_get_info = MagicMock(return_value=_ok_response([]))

    _, failures, _ = run_quest_execute(client, account_alias="Alex", confirm=True)

    client.quest_operation.assert_not_called()
    assert failures[0]["step"] == "clanGetInfo"


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
