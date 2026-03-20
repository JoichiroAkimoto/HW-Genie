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
    """HWClient.call と mission_get_all をモック化する fixture"""
    # インスタンスではなくクラスのメソッドをパッチする
    with patch("hw_genie.core.client.HWClient.call") as mock_call, \
         patch("hw_genie.core.client.HWClient.mission_get_all") as mock_mission_get_all:
        from hw_genie.core.client import HWClient
        from unittest.mock import MagicMock

        client = HWClient(default_headers)
        
        # デフォルトで全ミッションが未実行(実行可能)である状態を返す
        res = MagicMock()
        res.is_success = True
        res.detail = {"response": []}
        mock_mission_get_all.return_value = res
        
        yield client, mock_call
