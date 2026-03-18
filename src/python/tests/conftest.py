from unittest.mock import patch

import pytest


@pytest.fixture
def mock_sleep():
    """time.sleep を無効化する fixture"""
    with patch("time.sleep", return_value=None):
        yield


@pytest.fixture
def default_headers():
    """標準的なテスト用ヘッダーを提供"""
    return {"x-auth-token": "test-token", "x-auth-player-id": "123", "x-request-id": "100"}


@pytest.fixture
def mock_client(default_headers):
    """HWClient.call をモック化する fixture"""
    # インスタンスではなくクラスのメソッドをパッチする
    with patch("hw_genie.core.client.HWClient.call") as mock_call:
        from hw_genie.core.client import HWClient

        client = HWClient(default_headers)
        yield client, mock_call
