from unittest.mock import MagicMock

# スクリプトのディレクトリをパスに追加

from hw_genie.commands.item_raid import run_item_raid


def test_item_raid_max_iterations(mock_client, mock_sleep):
    """指定された最大回数でループが停止することを検証"""
    client, mock_call = mock_client

    # 常に成功を返す
    res_success = MagicMock()
    res_success.is_success = True
    mock_call.return_value = res_success

    # 最大 3 回で実行
    run_item_raid({"x-request-id": "100"}, {"calls": []}, max_iterations=3)

    # 検証: 3 回実行されていること
    assert mock_call.call_count == 3


def test_item_raid_stops_on_stamina_error(mock_client, mock_sleep):
    """スタミナ不足時にループを抜けることを検証"""
    client, mock_call = mock_client

    mock_responses = []

    # 1. 成功
    res_success = MagicMock()
    res_success.is_success = True
    mock_responses.append(res_success)

    # 2. スタミナ不足
    res_error = MagicMock()
    res_error.is_success = False
    res_error.error_name = "notEnoughStamina"
    mock_responses.append(res_error)

    mock_call.side_effect = mock_responses

    run_item_raid({"x-request-id": "100"}, {"calls": []})

    # 検証: 2 回で止まっていること
    assert mock_call.call_count == 2
