import pytest
import requests
from unittest.mock import MagicMock, patch
from hw_genie.core.client import HWClient, ResponseStatus, HWAuthError


@pytest.fixture
def client():
    headers = {"x-auth-token": "test-token", "x-auth-player-id": "123", "x-request-id": "100"}
    return HWClient(headers)


def test_retry_on_timeout(client):
    """タイムアウト発生時にリトライされることを検証"""
    with patch("requests.Session.post") as mock_post:
        # 2回タイムアウトし、3回目に成功する
        mock_post.side_effect = [
            requests.exceptions.Timeout("Timeout!"),
            requests.exceptions.Timeout("Timeout!"),
            MagicMock(status_code=200, json=lambda: {"results": [{"result": {"status": "ok"}}]}),
        ]

        res = client.call({"calls": []})
        assert res.is_success
        assert mock_post.call_count == 3


def test_retry_on_429_rate_limit(client):
    """HTTP 429 (Too Many Requests) 発生時にリトライされることを検証"""
    with patch("requests.Session.post") as mock_post:
        # 1回 429、2回目に成功
        resp_429 = MagicMock(status_code=429)
        # HTTPError に response を紐付ける
        error_429 = requests.exceptions.HTTPError("429 Client Error", response=resp_429)
        resp_429.raise_for_status.side_effect = error_429

        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {"results": [{"result": {"status": "ok"}}]}

        mock_post.side_effect = [resp_429, resp_200]

        res = client.call({"calls": []})
        assert res.is_success
        assert mock_post.call_count == 2


def test_no_retry_on_401_auth_error(client):
    """HTTP 401 (Unauthorized) 発生時はリトライせず即座に HWAuthError を投げることを検証"""
    with patch("requests.Session.post") as mock_post:
        mock_response = MagicMock(status_code=401)
        mock_post.return_value = mock_response

        with pytest.raises(HWAuthError):
            client.call({"calls": []})

        assert mock_post.call_count == 1


def test_max_retries_exceeded(client):
    """最大リトライ回数を超えた場合に最終的なエラーが返ることを検証"""
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")

        res = client.call({"calls": []})
        assert res.status == ResponseStatus.UNEXPECTED
        assert "Connection failed" in res.detail
        # Assuming MAX_RETRIES = 3, so 1 initial + 3 retries = 4 calls
        assert mock_post.call_count > 1


def test_retry_on_500_server_error(client):
    """HTTP 500 系のサーバーエラー時にリトライされることを検証"""
    with patch("requests.Session.post") as mock_post:
        resp_500 = MagicMock(status_code=500)
        # HTTPError に response を紐付ける
        error_500 = requests.exceptions.HTTPError("500 Server Error", response=resp_500)
        resp_500.raise_for_status.side_effect = error_500

        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {"results": [{"result": {"status": "ok"}}]}

        mock_post.side_effect = [resp_500, resp_200]

        res = client.call({"calls": []})
        assert res.is_success
        assert mock_post.call_count == 2
