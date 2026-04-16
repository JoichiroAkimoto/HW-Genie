import pytest
from unittest.mock import MagicMock, call
from hw_genie.commands.hero_raid import run_hero_raid
from hw_genie.core.client import ApiAction, HWAuthError
from . import dummy_responses as dummy


def test_raid_stops_on_limit_and_exchanges(mock_client, mock_sleep):
    """上限到達時に停止し、換金が実行されることを検証"""
    client, mock_call = mock_client
    mock_responses = []

    # 0. Checking mission status (mission_get_all)
    res_status_all = MagicMock()
    res_status_all.is_success = True
    res_status_all.detail = {"response": []}
    mock_responses.append(res_status_all)

    # 1. レイド成功
    res_success = MagicMock()
    res_success.status = "success"
    res_success.is_success = True
    res_success.detail = dummy.MISSION_RAID_SUCCESS["results"][0]["result"]
    mock_responses.append(res_success)

    # 2. 上限到達
    res_limit = MagicMock()
    res_limit.status = "error"
    res_limit.is_success = False
    res_limit.error_name = "limitReached"
    res_limit.detail = dummy.MISSION_RAID_LIMIT_REACHED["results"][0]["error"]
    mock_responses.append(res_limit)

    # 3. 換金 (exchange_stones)
    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_MULTI["results"][0]["result"]
    mock_responses.append(res_exchange)

    mock_call.side_effect = mock_responses

    # 実行 (ミッションID 1, 5 を対象)
    results, recovery_count, ex_info = run_hero_raid(client, [1, 5], times=3)

    # 検証
    assert len(results) == 2
    assert results[0].status == "success"
    assert results[1].status == "limit_reached"
    assert ex_info is not None
    assert ex_info.stones == 15

    # 呼び出しシーケンスの検証
    assert mock_call.call_count == 4
    mock_call.assert_has_calls(
        [
            call({"calls": [{"name": ApiAction.MISSION_GET_ALL, "args": {}, "ident": "body"}]}),
            call({"calls": [{"name": ApiAction.MISSION_RAID, "args": {"id": 1, "times": 3}, "context": {"actionTs": 0}, "ident": "body"}]}),
            call({"calls": [{"name": ApiAction.MISSION_RAID, "args": {"id": 5, "times": 3}, "context": {"actionTs": 0}, "ident": "body"}]}),
            call({"calls": [{"name": ApiAction.INVENTORY_EXCHANGE_STONES, "args": {}, "ident": "exchange_stones"}]}),
        ],
        any_order=False,
    )


def test_hero_raid_auth_error_abort(mock_client, mock_sleep):
    """実行中に認証エラーが発生した場合、例外が投げられることを検証"""
    client, mock_call = mock_client

    # 0. missionGetAll (正常)
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {"response": []}

    # 1. レイド認証エラー (AuthError を発生させる)
    mock_call.side_effect = [res_all, HWAuthError("Session expired")]

    with pytest.raises(HWAuthError):
        run_hero_raid(client, [1, 5], times=3)

    assert mock_call.call_count == 2


def test_hero_raid_empty_mission_ids(mock_client, mock_sleep):
    """mission_idsが空（またはNone）の場合、デフォルトリストが適用されることを検証"""
    client, mock_call = mock_client

    # 0. missionGetAll
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {"response": []}

    # 換金用のモック
    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]

    # デフォルトリストの数だけレイド成功レスポンスを返す
    # DEFAULT_HERO_MISSION_IDS = [1, 5, 10, ...] (54 elements)
    from hw_genie.commands.hero_raid import DEFAULT_HERO_MISSION_IDS

    res_success = MagicMock()
    res_success.status = "success"
    res_success.is_success = True
    res_success.detail = dummy.MISSION_RAID_SUCCESS["results"][0]["result"]

    # responses = [status_all] + [success] * len(DEFAULT_HERO_MISSION_IDS) + [exchange]
    mock_responses = [res_all] + [res_success] * len(DEFAULT_HERO_MISSION_IDS) + [res_exchange]
    mock_call.side_effect = mock_responses

    results, recovery_count, ex_info = run_hero_raid(client, [], times=3)

    assert len(results) == len(DEFAULT_HERO_MISSION_IDS)
    # API呼び出しは missionGetAll(1) + レイド(len(DEFAULT)) + 換金(1)
    assert mock_call.call_count == 1 + len(DEFAULT_HERO_MISSION_IDS) + 1


def test_hero_raid_with_specific_ids(mock_client, mock_sleep):
    """特定のミッションIDを指定した場合、そのIDのみが処理されることを検証"""
    client, mock_call = mock_client

    # 0. missionGetAll
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {"response": []}

    # レイド成功
    res_success = MagicMock()
    res_success.status = "success"
    res_success.is_success = True
    res_success.detail = dummy.MISSION_RAID_SUCCESS["results"][0]["result"]

    # 換金
    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]

    # 2つのIDを指定するので、 responses = [status_all, success, success, exchange]
    mock_responses = [res_all, res_success, res_success, res_exchange]
    mock_call.side_effect = mock_responses

    results, recovery_count, ex_info = run_hero_raid(client, [10, 20], times=3)

    assert len(results) == 2
    assert mock_call.call_count == 4  # status_all + 2 raids + exchange


def test_hero_raid_skips_already_done(mock_client, mock_sleep):
    """すでに triesSpent > 0 のミッションはスキップされることを検証"""
    client, mock_call = mock_client

    # 0. mission_get_all (1は完了済み, 5は未完了)
    res_status_all = MagicMock()
    res_status_all.is_success = True
    res_status_all.detail = {"response": [{"id": 1, "triesSpent": 3}, {"id": 5, "triesSpent": 0}]}

    # 1. Mission 5 Success
    res_success = MagicMock()
    res_success.status = "success"
    res_success.is_success = True
    res_success.detail = dummy.MISSION_RAID_SUCCESS["results"][0]["result"]

    # 2. Exchange
    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]

    mock_responses = [res_status_all, res_success, res_exchange]
    mock_call.side_effect = mock_responses

    results, recovery_count, ex_info = run_hero_raid(client, [1, 5], times=3)

    assert len(results) == 2
    assert results[0].id == 1
    assert results[0].status == "skipped"
    assert results[1].id == 5
    assert results[1].status == "success"
    assert mock_call.call_count == 3  # getAll(1) + raid(1) + exchange(1)


def test_hero_raid_stamina_recovery_success(mock_client, mock_sleep):
    """スタミナ不足時に回復を行い、その後成功することを検証"""
    client, mock_call = mock_client

    # 0. mission_get_all
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {"response": []}

    # 1. スタミナ不足
    res_stamina_err = MagicMock()
    res_stamina_err.is_success = False
    res_stamina_err.error_name = "notEnoughStamina"
    res_stamina_err.detail = {}

    # 2. スタミナ回復成功
    res_recovery = MagicMock()
    res_recovery.is_success = True

    # 3. レイド成功
    res_success = MagicMock()
    res_success.is_success = True
    res_success.detail = dummy.MISSION_RAID_SUCCESS["results"][0]["result"]

    # 4. 換金
    res_exchange = MagicMock()
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]

    mock_call.side_effect = [res_all, res_stamina_err, res_recovery, res_success, res_exchange]

    results, recovery_count, ex_info = run_hero_raid(client, [1], times=3, allow_recovery=True)

    assert results[0].status == "success"
    assert recovery_count == 1
    assert mock_call.call_count == 5  # getAll + raid(fail) + recover + raid(success) + exchange


def test_hero_raid_stamina_recovery_failure(mock_client, mock_sleep):
    """スタミナ回復に失敗した場合、エラーとして停止することを検証"""
    client, mock_call = mock_client

    # 0. mission_get_all
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {"response": []}

    # 1. スタミナ不足
    res_stamina_err = MagicMock()
    res_stamina_err.is_success = False
    res_stamina_err.error_name = "notEnoughStamina"
    res_stamina_err.detail = {}

    # 2. スタミナ回復失敗
    res_recovery_fail = MagicMock()
    res_recovery_fail.is_success = False

    # 3. 換金 (エラー後も実行される)
    res_exchange = MagicMock()
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]

    mock_call.side_effect = [res_all, res_stamina_err, res_recovery_fail, res_exchange]

    results, recovery_count, ex_info = run_hero_raid(client, [1], times=3, allow_recovery=True)

    assert results[0].status == "stamina_error"
    assert recovery_count == 0
    assert mock_call.call_count == 4  # getAll + raid(fail) + recover(fail) + exchange


def test_hero_raid_invalid_mission_id(mock_client, mock_sleep):
    """無効なミッションIDでAPIがエラーを返した場合、適切に処理されることを検証"""
    client, mock_call = mock_client

    # 0. mission_get_all
    res_all = MagicMock()
    res_all.is_success = True
    res_all.detail = {"response": []}

    # 1. 無効なIDによるエラー
    res_invalid = MagicMock()
    res_invalid.is_success = False
    res_invalid.error_name = "invalidMissionId"
    res_invalid.detail = {"description": "Mission ID not found"}

    # 2. 換金
    res_exchange = MagicMock()
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]

    mock_call.side_effect = [res_all, res_invalid, res_exchange]

    results, recovery_count, ex_info = run_hero_raid(client, [999], times=3)

    assert results[0].status == "error"
    assert results[0].name == "invalidMissionId"
    assert mock_call.call_count == 3
