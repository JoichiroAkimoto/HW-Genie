"""``run_quest_execute``（デイリークエスト自動実行）のテスト。

- モッククライアントを使い、ネットワークに依存しない。
- quest_defaults の読み書きは conftest のインメモリ DB（SessionManager）を使う。
"""

from unittest.mock import MagicMock

from hw_genie.commands.quests import (
    get_quest_defaults,
    run_quest_execute,
    set_quest_defaults,
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


def _make_client(raw_quests: list[dict]) -> HWClient:
    """quest_get_all をモックしたクライアントを作る（操作応答は別途差し替える）。"""
    client = HWClient(headers={"x-auth-token": "test"})
    res = _ok_response({"response": raw_quests})
    client.quest_get_all = MagicMock(return_value=res)
    client.quest_farm = MagicMock(return_value=_ok_response({}))
    return client


def _active(qid: int) -> dict:
    return {"id": qid, "state": 1, "progress": 0, "reward": {}, "createTime": 0, "farmCount": 0}


# --- テスト ---


def test_execute_runs_steps_and_claims(capsys):
    """操作応答で対象クエストが state=2 になると questFarm で報酬受領される。"""
    client = _make_client([_active(10024)])

    def _op(action: ApiAction, args: dict):
        if action == ApiAction.HERO_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10024, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="VitaminD", confirm=True)
    out = capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 10024
    assert "Reward claimed" in out
    client.quest_farm.assert_called_once_with(10024)


def test_execute_unregistered_quest_not_run(capsys):
    """QUEST_OPERATIONS 未登録のクエストは実行されない。"""
    client = _make_client([_active(10004)])
    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_execute_disabled_quest_skipped(capsys):
    """enabled:false のクエスト（10007）はスキップされ起動しない。"""
    client = _make_client([_active(10007)])
    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "disabled in QUEST_OPERATIONS" in out
    client.quest_operation.assert_not_called()


def test_execute_step_failure_reported(capsys):
    """ステップ失敗はアカウント×クエスト×ステップで報告される。"""
    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock(return_value=_error_response("notEnoughStamina"))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    f = failed[0]
    assert f["account"] == "Alex"
    assert f["quest_id"] == 10024
    assert f["step"] == "heroArtifactLevelUp"
    assert "notEnoughStamina" in f["error"]


def test_execute_claim_failure_captured(capsys):
    """操作成功でも questFarm 失敗は失敗リストに入る。"""
    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock(
        return_value=_ok_response({"quests": [{"id": 10024, "state": 2}]})
    )
    client.quest_farm = MagicMock(return_value=_error_response("AlreadyFarmed"))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "questFarm"
    assert "reward claim failed" in out


def test_dry_run_does_not_invoke_operations(capsys):
    """dry_run はプラン表示のみで操作・受領は実行しない。"""
    client = _make_client([_active(10024), _active(10028), _active(10030)])
    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "[dry-run]" in out
    assert "heroArtifactLevelUp" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_confirm_prompt_skips_when_declined(monkeypatch, capsys):
    """confirm=False のとき y 以外でステップをスキップし失敗報告する。"""
    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()
    monkeypatch.setattr("builtins.input", lambda _: "n")

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=False)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "heroArtifactLevelUp"
    assert "skipped by user" in failed[0]["error"]
    client.quest_operation.assert_not_called()


def test_confirm_prompt_eof_reported(monkeypatch, capsys):
    """stdin が閉じている（非TTY）場合も EOFError で落ちず失敗報告される。"""
    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()

    def _raise_eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise_eof)

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=False)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "heroArtifactLevelUp"
    assert "use --yes" in failed[0]["error"]
    assert "--yes" in out
    client.quest_operation.assert_not_called()


def test_account_default_override_applied_in_plan(capsys):
    """quest_defaults の引数上書きが dry-run のプランに反映される。"""
    SessionManager.save("Alex", {"player": {"id": "alex_id", "name": "Alex"}})
    set_quest_defaults("Alex", 10024, "heroId", 999)

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "heroId" in out
    assert "999" in out
    assert get_quest_defaults("Alex")[10024]["heroId"] == 999


def test_claimable_quest_claimed_without_operation(capsys):
    """state=2（受領待ち）のクエストは操作せず直接 questFarm で受領する。"""
    quest = {"id": 10024, "state": 2, "progress": 1, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([quest])
    client.quest_operation = MagicMock()
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 10024
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_called_once_with(10024)
    assert "already claimable" in out or "Reward claimed" in out


def test_claimable_claim_failure_reported(capsys):
    """state=2 クエストの quest claim 失敗は失敗リストに入る。"""
    quest = {"id": 10030, "state": 2, "progress": 1, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([quest])
    client.quest_operation = MagicMock()
    client.quest_farm = MagicMock(return_value=_error_response("AlreadyFarmed"))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["quest_id"] == 10030
    assert failed[0]["step"] == "questFarm"


def test_multistep_claim_after_second_step(capsys):
    """10028 の2ステップで、2番目のステップ応答後に claim 判定される。"""
    client = _make_client([_active(10028)])
    calls = []

    def _op(action, args):
        calls.append(action)
        if action == ApiAction.TITAN_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10028, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 10028
    # 全2ステップ実行された
    assert calls == [ApiAction.SHOP_BUY, ApiAction.TITAN_ARTIFACT_LEVEL_UP]
    assert "Reward claimed" in out


def test_reached_claimable_with_string_state():
    """レスポンスの state が文字列 '2' でも claim 判定される（型安全）。"""
    from hw_genie.commands.quests import _quest_reached_claimable

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()
    resp = _ok_response({"quests": [{"id": "10024", "state": "2"}]})
    assert _quest_reached_claimable(resp, 10024) is True

    # 別クエスト・別 state では False
    resp2 = _ok_response({"quests": [{"id": "10024", "state": "1"}]})
    assert _quest_reached_claimable(resp2, 10024) is False
    resp3 = _ok_response({"quests": [{"id": "10023", "state": "2"}]})
    assert _quest_reached_claimable(resp3, 10024) is False


def test_fetch_failure_reported(capsys):
    """questGetAll 自体が失敗すると fetch 失敗として報告される。"""
    client = _make_client([])
    client.quest_get_all = MagicMock(return_value=_error_response("InvalidSession"))
    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    assert failed[0]["step"] == "fetch"
    assert failed[0]["error"] == "InvalidSession"
    assert "Failed to fetch quests" in out


def test_claim_not_detected_after_all_steps(capsys):
    """全ステップ成功しても対応クエストが応答に出ない場合は注記される。"""
    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "claim not detected" in out


def test_defaults_unknown_key_warns(capsys, caplog):
    """quest_defaults の未知キーは適用されず警告ログが出る。"""
    SessionManager.save("Alex", {"player": {"id": "alex_id", "name": "Alex"}})
    set_quest_defaults("Alex", 10024, "unknownArg", 1)

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()

    run_quest_execute(client, account_alias="Alex", dry_run=True)
    capsys.readouterr().out

    assert "does not match any arg" in caplog.text
    assert "unknownArg" in caplog.text


def test_set_default_parses_string_value():
    """set_quest_defaults は CLI 由来の文字列を bool/int に解釈する。"""
    SessionManager.save("Alex", {"player": {"id": "alex_id", "name": "Alex"}})
    stored_true = set_quest_defaults("Alex", 10007, "free", "true")
    stored_id = set_quest_defaults("Alex", 10024, "heroId", "61")

    defaults = get_quest_defaults("Alex")
    assert defaults[10007]["free"] is True
    assert defaults[10024]["heroId"] == 61
    # 保存値（解釈後）が返る
    assert stored_true is True
    assert stored_id == 61


def test_progress_reached_target_claimed_without_operation(capsys):
    """state=1 でも progress>=target のクエストは操作せず直接受領する。"""
    quest = {"id": 10024, "state": 1, "progress": 1, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([quest])
    client.quest_operation = MagicMock()
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert len(succeeded) == 1
    assert succeeded[0]["quest_id"] == 10024
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_called_once_with(10024)


def test_unknown_target_quest_not_auto_claimable(capsys):
    """target 不明（10023）は progress>=target 判定に掛からず操作対象のまま。"""
    quest = {"id": 10023, "state": 1, "progress": 0, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([quest])
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert succeeded == []
    # 操作は実行された（target 判定に掛からないため）
    client.quest_operation.assert_called()


def test_multistep_defaults_no_false_warning(caplog):
    """マルチステップのどこかのステップに一致するキーは警告されない。"""
    SessionManager.save("Alex", {"player": {"id": "alex_id", "name": "Alex"}})
    set_quest_defaults("Alex", 10028, "titanId", 999)

    client = _make_client([_active(10028)])
    client.quest_operation = MagicMock()

    run_quest_execute(client, account_alias="Alex", dry_run=True)

    # titanId は titanArtifactLevelUp ステップの args に存在するため警告しない
    assert "does not match any arg" not in caplog.text


def test_cmd_quests_execute_failure_exits_nonzero():
    """main.cmd_quests は execute 失敗時に exit code 1 で終了する。"""

    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    SessionManager.save("Alex", {"player": {"id": "alex_id", "name": "Alex"}, "headers": {"x-auth-token": "t"}})

    class _Args:
        account = None
        set_default = None
        execute = True
        dry_run = False
        yes = True

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            quests_mod,
            "run_quest_execute",
            lambda *a, **k: ([], [{"step": "fetch", "error": "boom"}]),
        )
        with pytest.raises(SystemExit) as exc_info:
            main_mod.cmd_quests(_Args())
    assert exc_info.value.code == 1


def test_cmd_quests_execute_success_exits_zero():
    """main.cmd_quests は execute 成功時に exit code 0 で終了する。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    SessionManager.save("Bob", {"player": {"id": "bob_id", "name": "Bob"}, "headers": {"x-auth-token": "t"}})

    class _Args:
        account = "Bob"
        set_default = None
        execute = True
        dry_run = False
        yes = True

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            quests_mod,
            "run_quest_execute",
            lambda *a, **k: ([{"quest_id": 10024}], []),
        )
        assert main_mod.cmd_quests(_Args()) is None