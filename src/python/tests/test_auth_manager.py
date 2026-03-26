from unittest.mock import MagicMock, patch
import pytest
from datetime import datetime
import os
import json


# スクリプトのディレクトリをパスに追加

from . import dummy_responses as dummy
from hw_genie.core.auth import (
    get_user_info,
    load_session,
    save_session,
    extract_headers_from_curl,
    extract_payload_from_curl,
)


def test_extract_headers_from_curl():
    """curl コマンドから x-auth- ヘッダーを正しく抽出できることを検証"""
    curl_cmd = (
        "curl 'https://api.example.com/' "
        "-H 'Accept: */*' "
        "-H 'X-Auth-Token: ps-abc-123' "
        "-H 'x-auth-player-id: 61405392' "
        "-H 'X-Auth-Session-Key;'"
    )
    headers = extract_headers_from_curl(curl_cmd)

    assert headers["x-auth-token"] == "ps-abc-123"
    assert headers["x-auth-player-id"] == "61405392"
    assert headers["x-auth-session-key"] == ""
    assert "accept" not in headers


def test_extract_payload_from_curl_multiple_valid():
    """ノイズ(stashClient)は除去し、複数の有効な命令は維持することを検証"""
    curl_cmd = (
        "curl '...' --data-raw '{\"calls\":["
        "{\"name\":\"stashClient\",\"args\":{}},"
        "{\"name\":\"missionRaid\",\"args\":{\"id\":123}},"
        "{\"name\":\"userGetInfo\",\"args\":{}}"
        "]}'"
    )
    payload = extract_payload_from_curl(curl_cmd)

    assert payload is not None
    assert len(payload["calls"]) == 2
    names = [c["name"] for c in payload["calls"]]
    assert "missionRaid" in names
    assert "userGetInfo" in names
    assert "stashClient" not in names


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
    # assert info["player"]["level"] == 120
    assert info["player"]["energy"] == 150
    # assert info["player"]["energy_max"] == 180  # 120 + 60
    assert info["player"]["arena_rank"] == 42
    assert info["player"]["grand_rank"] == 15
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
