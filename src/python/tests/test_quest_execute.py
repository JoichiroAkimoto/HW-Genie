"""``run_quest_execute``（デイリークエスト自動実行）のテスト。

- モッククライアントを使い、ネットワークに依存しない。
- quest_defaults の読み書きは conftest のインメモリ DB（SessionManager）を使う。
- 実行可否は quest_defaults[quest_id]["enabled"] で制御される（初期状態は無効）。
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


def _register(account: str) -> None:
    SessionManager.save(account, {"player": {"id": account, "name": account}, "headers": {"x-auth-token": "test"}})


def _enable(account: str, quest_id: int) -> None:
    _register(account)
    set_quest_defaults(account, quest_id, "enabled", True)


def _make_client(raw_quests: list[dict], account: str = "Alex") -> HWClient:
    """アカウント登録＋quest_get_all モック済みクライアントを作る。"""
    _register(account)
    client = HWClient(headers={"x-auth-token": "test"})
    res = _ok_response({"response": raw_quests})
    client.quest_get_all = MagicMock(return_value=res)
    client.quest_farm = MagicMock(return_value=_ok_response({}))
    client.call = MagicMock(return_value=_ok_response({"response": {}}))
    return client


def _shop_inventory(slots: dict) -> MagicMock:
    """shopGetAll のレスポンスを作る。slots は ``{"18": {"reward": ..., "cost": ...}}``。"""
    return _ok_response({"response": {"13": {"slots": slots}}})


def _active(qid: int) -> dict:
    return {"id": qid, "state": 1, "progress": 0, "reward": {}, "createTime": 0, "farmCount": 0}


# --- テスト ---


def test_execute_runs_steps_and_claims(capsys):
    """enabled=true のクエストが操作→state=2 応答→questFarm で受領される。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)

    def _op(action: ApiAction, args: dict):
        if action == ApiAction.HERO_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10024, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
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


def test_execute_not_enabled_quest_skipped(capsys):
    """enabled 未設定（初期状態 false）のクエストはスキップされ起動しない。"""
    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "not enabled in quest_defaults" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_execute_step_failure_reported(capsys):
    """ステップ失敗はアカウント×クエスト×ステップで報告される。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
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
    _enable("Alex", 10024)
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
    _enable("Alex", 10024)
    _enable("Alex", 10028)
    _enable("Alex", 10030)
    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "[dry-run]" in out
    assert "heroArtifactLevelUp" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_dry_run_hides_unregistered_claimable(capsys):
    """QUEST_OPERATIONS 未登録の受領待ち（バトルパス等）は dry-run に表示されない。"""
    battlepass = {"id": 2609007076, "state": 2, "progress": 1, "reward": {}, "createTime": 0, "farmCount": 0}
    client = _make_client([battlepass, _active(10024)])
    _enable("Alex", 10024)
    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", dry_run=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "2609007076" not in out
    assert "10024" in out


def test_confirm_prompt_skips_when_declined(monkeypatch, capsys):
    """confirm=False のとき y 以外でステップをスキップし失敗報告する。"""
    client = _make_client([_active(10024)])
    _enable("Alex", 10024)
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
    _enable("Alex", 10024)
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
    _enable("Alex", 10024)
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
    _enable("Alex", 10028)
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


def test_shop_buy_reward_resolved_from_inventory(capsys):
    """shopBuy の reward/cost が実在庫（shopGetAll）の slot から動的解決される。

    実在庫が slot 22 → fragment 2003 の場合、コード既定の 2001 ではなく
    2003 が送信される（slot を変えるアカウントでも在庫に追従できる）。
    """
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    # 実在庫: slot 22 には fragment 2003 が売られている（既定レシピは slot 18 / 2001）
    client.call.return_value = _shop_inventory(
        {
            "10": {"reward": {"fragmentTitanArtifact": {"1017": 1}}, "cost": {"coin": {"18": 12}}},
            "22": {"reward": {"fragmentTitanArtifact": {"2003": 1}}, "cost": {"coin": {"18": 12}}},
        }
    )
    set_quest_defaults("Alex", 10028, "slot", 22)

    sent_args = []

    def _op(action, args):
        if action == ApiAction.SHOP_BUY:
            sent_args.append(dict(args))
            return _ok_response({"quests": [{"id": 10028, "state": 1}]})
        if action == ApiAction.TITAN_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10028, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert len(sent_args) == 1
    # slot 22 → 実在庫の fragment 2003 / cost に追従
    assert sent_args[0]["slot"] == 22
    assert sent_args[0]["reward"] == {"fragmentTitanArtifact": {"2003": 1}}
    assert sent_args[0]["cost"] == {"coin": {"18": 12}}


def test_shop_buy_slot_not_in_inventory_fails(capsys):
    """指定 slot が実在庫に存在しない場合は実行せず失敗報告される。

    在庫は取得できたが slot が無い場合、固定 reward で shopBuy を送信すると
    必ず NotAvailable になるため、ステップ実行前に abort する。
    """
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call.return_value = _shop_inventory(
        {"10": {"reward": {"fragmentTitanArtifact": {"1017": 1}}, "cost": {"coin": {"18": 12}}}}
    )
    set_quest_defaults("Alex", 10028, "slot", 99)  # 在庫に存在しない slot

    client.quest_operation = MagicMock()

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert len(failed) == 1
    f = failed[0]
    assert f["quest_id"] == 10028
    assert f["step"] == "shopBuy"
    assert "slot 99 not in shop 13 inventory" in f["error"]
    assert "cannot execute" in out
    client.quest_operation.assert_not_called()
    client.quest_farm.assert_not_called()


def test_shop_buy_auth_error_reraises(capsys):
    """shopGetAll で認証エラー（HWAuthError）は握りつぶさず再送出される。"""
    import pytest

    from hw_genie.core.client import HWAuthError

    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call = MagicMock(side_effect=HWAuthError("auth failed"))

    client.quest_operation = MagicMock()

    with pytest.raises(HWAuthError):
        run_quest_execute(client, account_alias="Alex", confirm=True)
    client.quest_operation.assert_not_called()


def test_shop_buy_inventory_fetch_failure_keeps_default(capsys, caplog):
    """shopGetAll 失敗（認証以外）は既定 reward のまま動作（取得失敗を警告で知らせる）。"""
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call = MagicMock(return_value=_error_response("NetworkError"))

    sent_args = []

    def _op(action, payload):
        if action == ApiAction.SHOP_BUY:
            sent_args.append(dict(payload))
            return _ok_response({"quests": [{"id": 10028, "state": 1}]})
        if action == ApiAction.TITAN_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10028, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert len(sent_args) == 1
    assert sent_args[0]["reward"] == {"fragmentTitanArtifact": {"2001": 1}}
    assert "shopGetAll failed" in caplog.text


def test_shop_buy_inventory_cached_per_shop(capsys):
    """同一 shop の shopBuy が複数ステップあっても shopGetAll は1回だけ発行される。"""
    client = _make_client([_active(10028)])
    _enable("Alex", 10028)
    client.call.return_value = _shop_inventory(
        {"18": {"reward": {"fragmentTitanArtifact": {"2001": 1}}, "cost": {"coin": {"18": 12}}}}
    )

    sent_args = []

    def _op(action, args):
        if action == ApiAction.SHOP_BUY:
            sent_args.append(dict(args))
            return _ok_response({"quests": [{"id": 10028, "state": 1}]})
        if action == ApiAction.TITAN_ARTIFACT_LEVEL_UP:
            return _ok_response({"quests": [{"id": 10028, "state": 2}]})
        return _ok_response({})

    client.quest_operation = MagicMock(side_effect=_op)
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert failed == []
    assert client.call.call_count == 1
    assert client.call.call_args.args[0]["calls"][0]["name"] == ApiAction.SHOP_GET_ALL


def test_reached_claimable_with_string_state():
    """レスポンスの state が文字列 '2' でも claim 判定される（型安全）。"""
    from hw_genie.commands.quests import _quest_reached_claimable

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
    _enable("Alex", 10024)
    client.quest_operation = MagicMock(return_value=_ok_response({}))
    client.quest_farm = MagicMock(return_value=_ok_response({}))

    succeeded, failed = run_quest_execute(client, account_alias="Alex", confirm=True)
    out = capsys.readouterr().out

    assert succeeded == []
    assert failed == []
    assert "claim not detected" in out


def test_defaults_unknown_key_warns(capsys, caplog):
    """quest_defaults の未知キーは適用されず警告ログが出る。"""
    _enable("Alex", 10024)
    set_quest_defaults("Alex", 10024, "unknownArg", 1)

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock()

    run_quest_execute(client, account_alias="Alex", dry_run=True)
    capsys.readouterr().out

    assert "does not match any arg" in caplog.text
    assert "unknownArg" in caplog.text


def test_set_default_parses_string_value():
    """set_quest_defaults は CLI 由来の文字列を bool/int に解釈する。"""
    _register("Alex")
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
    _enable("Alex", 10023)
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
    _enable("Alex", 10028)
    set_quest_defaults("Alex", 10028, "titanId", 999)

    client = _make_client([_active(10028)])
    client.quest_operation = MagicMock()

    run_quest_execute(client, account_alias="Alex", dry_run=True)

    # titanId は titanArtifactLevelUp ステップの args に存在するため警告しない
    assert "does not match any arg" not in caplog.text


# --- ensure_quest_defaults ---


def test_ensure_quest_defaults_seeds_disabled_for_all():
    """初回は QUEST_OPERATIONS 全キーが enabled=false で投入される。"""
    from hw_genie.commands.quests import QUEST_OPERATIONS, ensure_quest_defaults

    _register("Alex")
    defaults = ensure_quest_defaults("Alex")

    assert set(defaults) == set(QUEST_OPERATIONS)
    for qid, conf in defaults.items():
        assert conf.get("enabled") is False


def test_ensure_quest_defaults_seeds_operation_args():
    """初期投入時に操作ステップのデフォルト引数も埋まる。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    defaults = ensure_quest_defaults("Alex")

    assert defaults[10024]["heroId"] == 61
    assert defaults[10024]["slotId"] == 1
    assert defaults[10028]["titanId"] == 4012
    assert defaults[10028]["slotId"] == 1
    assert defaults[10028]["shopId"] == 13
    assert defaults[10030]["heroId"] == 59
    assert defaults[10030]["skinId"] == 313
    assert defaults[10023]["heroId"] == 38


def test_ensure_quest_defaults_backfills_missing_args():
    """既に初期化済みのアカウントには不足キーのみ補完される（既存値は保持）。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    set_quest_defaults("Alex", 10024, "enabled", True)
    set_quest_defaults("Alex", 10024, "heroId", 777)

    defaults = ensure_quest_defaults("Alex")
    assert defaults[10024]["enabled"] is True
    assert defaults[10024]["heroId"] == 777  # 既存値は上書きしない
    assert defaults[10024]["slotId"] == 1    # 不足キーは補完
    assert defaults[10028]["enabled"] is False


def test_ensure_quest_defaults_idempotent():
    """2回目以降は書き込み不要で同じ結果が返る。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    ensure_quest_defaults("Alex")
    again = ensure_quest_defaults("Alex")
    assert again == get_quest_defaults("Alex")


def test_ensure_quest_defaults_seeds_note():
    """note に操作ステップの RPC 名が連結されて補完される。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    defaults = ensure_quest_defaults("Alex")

    assert defaults[10024]["note"] == "heroArtifactLevelUp"
    assert defaults[10028]["note"] == "shopBuy → titanArtifactLevelUp"
    assert defaults[10023]["note"] == "heroTitanGiftLevelUp → heroTitanGiftLevelUp → heroTitanGiftDrop"
    assert defaults[10007]["note"] == "gacha_open"


def test_ensure_quest_defaults_skips_dict_args():
    """dict/list 型のデフォルト引数（10028 の cost/reward 等）はバックフィルしない。

    行編集（ウィザード/--set-default）で JSON→文字列に型崩れするのを防ぐため、
    スカラー引数のみ固定値化する。
    """
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    defaults = ensure_quest_defaults("Alex")

    assert defaults[10028]["titanId"] == 4012
    assert defaults[10028]["shopId"] == 13
    assert "cost" not in defaults[10028]
    assert "reward" not in defaults[10028]


def test_parse_float_value_json_dict():
    """set-default の値文字列は dict/list を JSON 解釈で復元できる（型崩れ防止）。"""
    from hw_genie.commands.quests import _parse_float_value

    assert _parse_float_value('{"coin": {"18": 12}}') == {"coin": {"18": 12}}
    assert _parse_float_value("[1, 2, 3]") == [1, 2, 3]
    assert _parse_float_value("true") is True
    assert _parse_float_value("123") == 123
    assert _parse_float_value("1.5") == 1.5
    assert _parse_float_value("hello") == "hello"
    assert _parse_float_value("123abc") == "123abc"


def test_set_default_stores_dict_value():
    """--set-default で dict 引数（10028 の cost 等）を JSON 文字列から復元保存する。"""
    from hw_genie.commands.quests import _parse_float_value, set_quest_defaults

    _register("Alex")
    set_quest_defaults("Alex", 10028, "cost", _parse_float_value('{"coin": {"18": 12}}'))

    assert get_quest_defaults("Alex")[10028]["cost"] == {"coin": {"18": 12}}


def test_ensure_quest_defaults_preserves_existing_note():
    """既存の note は上書きされない（他のキーと同じセマンティクス）。"""
    from hw_genie.commands.quests import ensure_quest_defaults

    _register("Alex")
    set_quest_defaults("Alex", 10024, "note", "my memo")
    defaults = ensure_quest_defaults("Alex")
    assert defaults[10024]["note"] == "my memo"


def test_note_ignored_in_operation_args(caplog, capsys):
    """note は操作引数の適用・未知キー警告の対象外。"""
    _register("Alex")
    set_quest_defaults("Alex", 10024, "note", "custom memo")
    set_quest_defaults("Alex", 10024, "enabled", True)

    client = _make_client([_active(10024)])
    client.quest_operation = MagicMock(return_value=_ok_response({}))

    run_quest_execute(client, account_alias="Alex", confirm=True)
    capsys.readouterr().out

    assert "does not match any arg" not in caplog.text
    # 操作は実際に実行され、その args に note が混入していない
    assert client.quest_operation.call_args_list
    for call in client.quest_operation.call_args_list:
        args = call.args[1]
        assert "note" not in args


# --- main.cmd_quests（exit code / 表示） ---


def test_cmd_quests_execute_failure_exits_nonzero():
    """main.cmd_quests は execute 失敗時に exit code 1 で終了する。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    _register("Alex")

    class _Args:
        account = "Alex"
        set_default = None
        init_defaults = False
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

    _register("Bob")

    class _Args:
        account = "Bob"
        set_default = None
        init_defaults = False
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


def test_cmd_quests_edit_defaults_requires_registered_account(capsys):
    """--edit-defaults は未登録アカウントでは他オプション同様の文言で exit 1。"""
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    class _Args:
        account = "NoSuchAlias"
        edit_defaults = True

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            quests_mod,
            "edit_quest_defaults_interactive",
            lambda acct: (_ for _ in ()).throw(AssertionError("must not be called")),
        )
        with pytest.raises(SystemExit) as exc_info:
            main_mod.cmd_quests(_Args())
    assert exc_info.value.code == 1
    assert "Session not found for account 'NoSuchAlias'" in capsys.readouterr().out


def test_cmd_quests_edit_defaults_skips_session():
    """--edit-defaults は DB 編集のみなので認証セッション（_ensure_session）が不要。

    `_ensure_session` は**呼ばれたら失敗するモック**にして、edit 分岐が
    `_ensure_session` より前で return することを実効的に検証する。
    """
    import pytest

    import hw_genie.main as main_mod
    from hw_genie.commands import quests as quests_mod

    _register("Carol")

    class _Args:
        account = "Carol"
        edit_defaults = True

    called = {"edit": False}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            main_mod,
            "_ensure_session",
            lambda args: (_ for _ in ()).throw(AssertionError("_ensure_session must not be called")),
        )
        mp.setattr(quests_mod, "edit_quest_defaults_interactive", lambda acct: called.update(edit=True))
        assert main_mod.cmd_quests(_Args()) is None
    assert called["edit"] is True


# --- 対話的編集ウィザード（edit_quest_defaults_interactive） ---


def _make_input_sequence(*answers: str):
    """与えられた順に input() が返すイテレータを作る。"""
    it = iter(answers)
    return lambda _prompt: next(it)


def test_edit_defaults_wizard_enables_quest(monkeypatch, capsys):
    """クエスト番号→enabled 番号選択で有効化できる。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "1", "1", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert get_quest_defaults("Alex")[10007]["enabled"] is True
    assert "10007" in out
    assert "Soul Atrium" in out
    assert "enabled" in out


def test_edit_defaults_wizard_sets_override_value(monkeypatch, capsys):
    """クエスト番号→引数キー番号→値入力で上書きできる。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 3 番目が 10024（10007, 10023, 10024 の順）。キー一覧で 2 番目は heroId。
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("3", "2", "999", "q"))

    edit_quest_defaults_interactive("Alex")
    capsys.readouterr().out

    assert get_quest_defaults("Alex")[10024]["heroId"] == 999


def test_edit_defaults_wizard_displays_current_values(monkeypatch, capsys):
    """設定キーの現在値が一覧に表示される。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    set_quest_defaults("Alex", 10024, "enabled", True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("3", "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "10024" in out
    assert "heroArtifactLevelUp" in out
    assert "✅ enabled" in out
    assert "heroId" in out
    assert "61" in out


def test_edit_defaults_wizard_back_and_invalid(monkeypatch, capsys):
    """無効入力は再入力を促し、b で一覧に戻れる。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 1 回目は無効な 99 → 2 回目で 10007 選択 → キー選択で b → 一覧で q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("99", "1", "b", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "Invalid choice" in out
    assert "Bye." in out


def test_edit_defaults_wizard_value_input_bq_cancels(monkeypatch, capsys):
    """値入力で b/q はキャンセルとみなされ、設定値として保存されない。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 10024 選択 → heroId キー → b（キャンセル）→ キー一覧で q → 一覧で q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("3", "2", "b", "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "b/q: cancel" in out
    assert get_quest_defaults("Alex")[10024]["heroId"] == 61  # 変更されていない


def test_edit_defaults_wizard_rejects_dict_value(monkeypatch, capsys):
    """ウィザードの値入力で JSON（dict/list）は拒否され、--set-default を案内する。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 10024 選択 → heroId キー → JSON を入力（拒否される）→ q → 一覧で q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("3", "2", '{"a": 1}', "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "dict/list 値は --set-default で指定" in out
    assert get_quest_defaults("Alex")[10024]["heroId"] == 61  # 変更されていない


def test_edit_defaults_wizard_enabled_choice_cancel(monkeypatch, capsys):
    """enabled の true/false 選択中に b でキャンセルでき、DB は変更されない。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 10007 選択 → enabled キー → true/false 選択で b → キー一覧に戻る → q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "1", "b", "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert get_quest_defaults("Alex")[10007]["enabled"] is False
    assert "saved" not in out
    assert "Invalid choice" not in out
    assert "Bye." in out


def test_edit_defaults_wizard_enabled_choice_quit(monkeypatch, capsys):
    """enabled の true/false 選択中に q でキャンセルし、次の q で終了できる。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    # 10007 選択 → enabled キー → true/false 選択で q（キャンセル）→ キー一覧 → q
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "1", "q", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert get_quest_defaults("Alex")[10007]["enabled"] is False
    assert "Bye." in out


def test_edit_defaults_wizard_eof_raises_systemexit(monkeypatch, capsys):
    """非TTY（EOF）では SystemExit(1) で終了する。"""
    import pytest

    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))

    with pytest.raises(SystemExit) as exc_info:
        edit_quest_defaults_interactive("Alex")
    assert exc_info.value.code == 1
    assert "No interactive input available" in capsys.readouterr().err


def test_edit_defaults_wizard_tty_path(monkeypatch, capsys):
    """stdin+stdout とも TTY なら rich パスの選択フローが動き、クリア制御コードが出る。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "1", "1", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert get_quest_defaults("Alex")[10007]["enabled"] is True
    assert "Soul Atrium" in out
    assert "Bye." in out
    # 全画面リフレッシュ（画面クリア制御コード）が出力に含まれる
    assert "\x1b[2J" in out


def test_edit_defaults_wizard_stdout_redirect_no_clear_codes(monkeypatch, capsys):
    """stdin が TTY でも stdout が非TTY（リダイレクト）ならクリア制御コードを混入しない。"""
    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    assert "\x1b[2J" not in out
    assert "Bye." in out


def test_edit_defaults_wizard_tty_eof_raises_systemexit(monkeypatch, capsys):
    """TTY 判定でも EOF は SystemExit(1)（_prompt_input 共用）。"""
    import pytest

    from hw_genie.commands.quests import edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))

    with pytest.raises(SystemExit) as exc_info:
        edit_quest_defaults_interactive("Alex")
    assert exc_info.value.code == 1
    assert "No interactive input available" in capsys.readouterr().err


def test_edit_defaults_wizard_hides_note_from_keys(monkeypatch, capsys):
    """note はキー一覧に表示されず編集対象にならない（参照専用）。"""

    from rich.console import Console

    from hw_genie.commands.quests import _key_list_table, edit_quest_defaults_interactive

    _register("Alex")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", _make_input_sequence("1", "q"))

    edit_quest_defaults_interactive("Alex")
    out = capsys.readouterr().out

    # クエスト一覧には note（操作名）が表示される
    assert "gacha_open" in out
    # キー一覧テーブルには note 行が存在しない（enabled と ident/free/pack のみ）
    conf = get_quest_defaults("Alex")[10007]
    Console().print(_key_list_table(10007, conf))
    key_out = capsys.readouterr().out
    assert "note" not in key_out
    for key in ("enabled", "ident", "free", "pack"):
        assert key in key_out, f"{key} がキー一覧にない"
