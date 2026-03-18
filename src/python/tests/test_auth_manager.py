from unittest.mock import MagicMock, patch
import pytest
from datetime import datetime
import os
import json


# スクリプトのディレクトリをパスに追加

from . import dummy_responses as dummy
from hw_genie.core.auth import get_user_info, load_session, save_session


@patch("requests.post")
def test_get_user_info_success(mock_post):
    """API レスポンスからユーザー情報を正しく抽出できることを検証"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = dummy.USER_INFO_SUCCESS
    mock_post.return_value = mock_response

    headers = {"x-request-id": "100"}
    info = get_user_info(headers)

    assert info["status"] == "success"
    assert info["player"]["name"] == "TestPlayer"
    assert info["player"]["level"] == 120
    assert info["player"]["energy"] == 150
    assert info["player"]["energy_max"] == 180  # 120 + 60
    assert "last_updated" in info
    # Check if last_updated is a valid ISO format
    try:
        datetime.fromisoformat(info["last_updated"])
    except ValueError:
        pytest.fail("last_updated is not a valid ISO format")


@patch("os.path.exists")
@patch("builtins.open")
def test_session_save_load(mock_open, mock_exists):
    """セッションの保存と読み込みを検証"""
    test_data = {"headers": {"token": "test"}, "player": {"name": "test"}}

    # 1. 保存テスト
    save_session(test_data, account="test_acc")
    # 正しいパスで開かれたか確認
    # (scripts/../session.test_acc.json になるはず)
    mock_open.assert_called()
    call_args = mock_open.call_args[0][0]
    assert "session.test_acc.json" in call_args
    assert "scripts" not in os.path.basename(os.path.dirname(call_args))  # Should be at skill root

    # 2. 読み込みテスト
    mock_exists.return_value = True
    # MagicMock for file content
    mock_file = MagicMock()
    mock_file.__enter__.return_value = mock_file
    mock_file.read.return_value = json.dumps(test_data)
    mock_open.return_value = mock_file

    loaded = load_session(account="test_acc")
    assert loaded == test_data
