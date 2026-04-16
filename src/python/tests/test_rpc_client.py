import pytest
from unittest.mock import MagicMock
from hw_genie.core.client import HWClient


@pytest.fixture
def client_with_mock_session():
    """requests.Sessionをモック化したHWClientを作成"""
    mock_session = MagicMock()
    headers = {"x-auth-token": "test-token", "x-auth-player-id": "123", "x-request-id": "100"}
    client = HWClient(headers, session=mock_session)
    return client, mock_session


def test_call_with_multiple_results_partial_failure(client_with_mock_session):
    """
    複数コールを投げた時に、HWClientがどのように反応するか確認するテスト
    """
    client, mock_session = client_with_mock_session

    # サーバーが複数結果を返す（1つ成功、1つ失敗）
    res_data = {"results": [{"result": {"success": True}}, {"error": {"name": "someError"}}]}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = res_data
    mock_session.post.return_value = mock_response

    # 実行
    res = client.call({"calls": [{"name": "test"}, {"name": "test2"}]})

    # 新しい仕様: 複数結果がある場合は map が返る
    assert res.is_success is True
    assert res.detail == {"0": {"result": {"success": True}}, "1": {"error": {"name": "someError"}}}
