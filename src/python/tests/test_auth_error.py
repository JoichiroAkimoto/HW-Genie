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

def test_call_auth_error_http_401(client_with_mock_session):
    """HTTP 401 認証エラー時の挙動を検証"""
    client, mock_session = client_with_mock_session

    # HTTP 401 を返す
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_session.post.return_value = mock_response

    # 実行
    res = client.call({"calls": [{"name": "test"}]})
    
    # 認証エラー判定を確認
    assert res.status == ResponseStatus.AUTH_ERROR
    assert res.error_name == "auth"

def test_call_auth_error_json_body(client_with_mock_session):
    """レスポンスボディ内の認証エラーを検証"""
    client, mock_session = client_with_mock_session

    # ステータスコード200だがボディにエラーがある
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"error": {"name": "auth"}}
    mock_session.post.return_value = mock_response

    # 実行
    res = client.call({"calls": [{"name": "test"}]})
    
    # 認証エラー判定を確認
    assert res.status == ResponseStatus.AUTH_ERROR
    assert res.error_name == "auth"
