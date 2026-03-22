import pytest
from unittest.mock import MagicMock
from hw_genie.core.client import HWAuthError
from . import dummy_responses as dummy
from hw_genie.commands.hero_raid import run_hero_raid


def test_raid_stops_on_limit_and_exchanges(mock_client, mock_sleep):
    """上限到達時に停止し、換金が実行されることを検証"""
    client, mock_call = mock_client

    # モックの返り値を順に定義
    mock_responses = []

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

    # 3. 換金
    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_MULTI["results"][0]["result"]
    mock_responses.append(res_exchange)

    # 4. Status (fetch_player_status は 2 回 call を呼ぶ)
    res_status = MagicMock()
    res_status.is_success = True
    res_status.detail = {"response": {"level": 130, "gold": 1000, "starMoney": 100}}
    mock_responses.append(res_status)
    mock_responses.append(res_status)

    mock_call.side_effect = mock_responses

    # 実行 (ミッションID 1, 5 を対象)
    results, recovery_count, ex_info = run_hero_raid({"x-auth-token": "test", "x-request-id": "100"}, [1, 5], times=3)

    # 検証
    assert len(results) == 2
    assert results[0].status == "success"
    assert results[1].status == "limit_reached"

    # 換金数が正しく取得できているか
    assert ex_info is not None
    # dummy.INVENTORY_EXCHANGE_STONES_MULTI の合計は 15
    assert ex_info.stones == 15


def test_hero_raid_auth_error_abort(mock_client, mock_sleep):
    """実行中に認証エラーが発生した場合、例外が投げられることを検証"""
    client, mock_call = mock_client

    # 1. レイド認証エラー (AuthError を発生させる)
    res_auth = MagicMock()
    res_auth.status = "auth_error"
    res_auth.is_success = False
    
    # HWClient.call は認証エラー時に HWAuthError を投げるようになったため
    # mock_call の side_effect に例外を設定
    mock_call.side_effect = HWAuthError("Session expired")

    with pytest.raises(HWAuthError):
        run_hero_raid(client, [1, 5], times=3)

    assert mock_call.call_count == 1


def test_hero_raid_empty_mission_ids(mock_client, mock_sleep):
    """mission_idsが空の場合、APIコールが発生しないことを検証"""
    client, mock_call = mock_client
    
    # 換金用のモック
    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]

    # Status 用のモック
    res_status = MagicMock()
    res_status.is_success = True
    res_status.detail = {"response": {}}

    mock_responses = [res_exchange, res_status, res_status]
    mock_call.side_effect = mock_responses

    results, recovery_count, ex_info = run_hero_raid(client, [], times=3)

    assert len(results) == 0
    # API呼び出しは換金(1) + Status(2) = 3回
    assert mock_call.call_count == 3


def test_hero_raid_skips_already_done(mock_client, mock_sleep):
    """すでに triesSpent > 0 のミッションはスキップされることを検証"""
    client, mock_call = mock_client

    res_status = MagicMock()
    res_status.is_success = True
    res_status.detail = {"response": [{"id": 1, "triesSpent": 3}, {"id": 5, "triesSpent": 0}]}
    client.mission_get_all.return_value = res_status

    # レイド対象は 1 (スキップ), 5 (実行)
    res_success = MagicMock()
    res_success.status = "success"
    res_success.is_success = True
    mock_responses = [res_success]

    res_exchange = MagicMock()
    res_exchange.status = "success"
    res_exchange.is_success = True
    res_exchange.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]
    mock_responses.append(res_exchange)

    # Status
    res_user_status = MagicMock()
    res_user_status.is_success = True
    res_user_status.detail = {"response": {}}
    mock_responses.append(res_user_status)
    mock_responses.append(res_user_status)

    mock_call.side_effect = mock_responses

    results, recovery_count, ex_info = run_hero_raid(client, [1, 5], times=3)

    assert len(results) == 2
    assert results[0].id == 1
    assert results[0].status == "skipped"
    assert results[1].id == 5
    assert results[1].status == "success"
    assert mock_call.call_count == 4  # 1 raid + 1 exchange + 2 status

