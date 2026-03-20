import pytest
from unittest.mock import MagicMock
from hw_genie.core.client import HWClient, ResponseStatus

@pytest.fixture
def client_with_mock_session():
    """requests.Sessionをモック化したHWClientを作成"""
    mock_session = MagicMock()
    headers = {"x-auth-token": "test-token", "x-auth-player-id": "123", "x-request-id": "100"}
    client = HWClient(headers, session=mock_session)
    return client, mock_session

def test_call_malformed_response(client_with_mock_session):
    """レスポンスが想定外の構造（キー不足など）の場合の挙動を検証"""
    client, mock_session = client_with_mock_session

    # 1. 'results'キーがない場合
    mock_session.post.return_value.status_code = 200
    mock_session.post.return_value.json.return_value = {"unexpected": "data"}
    
    res1 = client.call({"calls": [{"name": "test"}]})
    assert res1.status == ResponseStatus.UNEXPECTED
    assert res1.error_name == "empty_results"

    # 2. 'results'は空配列の場合
    mock_session.post.return_value.json.return_value = {"results": []}
    
    res2 = client.call({"calls": [{"name": "test"}]})
    assert res2.status == ResponseStatus.UNEXPECTED
    assert res2.error_name == "empty_results"

    # 3. 'results'はあるが'result'も'error'もない場合
    mock_session.post.return_value.json.return_value = {"results": [{}]}
    
    res3 = client.call({"calls": [{"name": "test"}]})
    assert res3.status == ResponseStatus.UNEXPECTED
    assert res3.error_name == "unknown_format"
