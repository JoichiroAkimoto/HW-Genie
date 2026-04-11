import pytest
from unittest.mock import MagicMock
from hw_genie.core.client import HWAuthError
from hw_genie.commands.item_raid import run_item_raid


def test_item_raid_max_iterations(mock_client, mock_sleep):
    """指定された最大回数でループが停止することを検証"""
    client, mock_call = mock_client

    # 常に成功を返す
    res_success = MagicMock()
    res_success.is_success = True
    
    # 3回分
    mock_call.side_effect = [res_success, res_success, res_success]

    # 最大 3 回で実行
    run_item_raid(client, {"calls": []}, max_iterations=3)

    # 検証: 3 回呼ばれるはず
    assert mock_call.call_count == 3


def test_item_raid_stops_on_stamina_error(mock_client, mock_sleep):
    """スタミナ不足時にループを抜けることを検証"""
    client, mock_call = mock_client

    # 1. 成功
    res_success = MagicMock()
    res_success.is_success = True

    # 2. スタミナ不足
    res_error = MagicMock()
    res_error.is_success = False
    res_error.error_name = "notEnoughStamina"
    
    mock_call.side_effect = [res_success, res_error]

    run_item_raid(client, {"calls": []})

    # 検証: 2 回で止まる
    assert mock_call.call_count == 2


def test_item_raid_auth_error_abort(mock_client, mock_sleep):
    """実行中に認証エラーが発生した場合、例外が投げられることを検証"""
    client, mock_call = mock_client
    
    # 1. 成功
    res_success = MagicMock()
    res_success.is_success = True
    
    # 2. 認証エラー
    mock_call.side_effect = [res_success, HWAuthError("Session expired")]

    with pytest.raises(HWAuthError):
        run_item_raid(client, {"calls": []})

    # 認証エラーで抜けるため2回で止まる
    assert mock_call.call_count == 2
