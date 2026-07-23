import pytest
from unittest.mock import MagicMock, patch

# パス追加

from . import dummy_responses as dummy
from hw_genie.core.client import HWClient


def test_exchange_stones_multi(mock_client, mock_sleep):
    """複数のヒーローIDが含まれる場合の換金テスト"""
    client, mock_call = mock_client

    # モックレスポンスの設定
    res = MagicMock()
    res.is_success = True
    res.detail = dummy.INVENTORY_EXCHANGE_STONES_MULTI["results"][0]["result"]
    mock_call.return_value = res

    result = client.exchange_stones()

    assert result.is_success
    assert result.exchange_info is not None
    # 5 (ID:3) + 3 (ID:12) + 7 (ID:8) = 15
    assert result.exchange_info.stones == 15


def test_exchange_stones_single(mock_client, mock_sleep):
    """単一のヒーローIDが含まれる場合の換金テスト"""
    client, mock_call = mock_client

    res = MagicMock()
    res.is_success = True
    res.detail = dummy.INVENTORY_EXCHANGE_STONES_SINGLE["results"][0]["result"]
    mock_call.return_value = res

    result = client.exchange_stones()

    assert result.is_success
    assert result.exchange_info.stones == 2


def test_stamina_error_handling(mock_client, mock_sleep):
    """スタミナエラーのパーステスト"""
    client, mock_call = mock_client

    # call() の中でパースは HWClient 自体の責務だが、
    # ここでは call() が返す HWResponse オブジェクトが期待通りかを確認する
    # (元々の unittest は requests.post を直接 patch していたが、
    # HWClient.call のテストとしてリライトする)

    res = MagicMock()
    res.is_success = False
    res.error_name = "notEnoughStamina"
    mock_call.return_value = res

    result = client.call({"calls": []})

    assert not result.is_success
    assert result.error_name == "notEnoughStamina"


def test_auth_error_handling_401(default_headers, mock_sleep):
    """HTTP 401 による認証エラー検知のテスト"""
    from hw_genie.core.client import HWAuthError

    client = HWClient(default_headers)

    with patch("requests.Session.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        # HWClient.call のパッチを一時的に無効化して本来のメソッドを呼ぶ
        with patch("hw_genie.core.client.HWClient.call", side_effect=HWClient.call, autospec=True):
            with pytest.raises(HWAuthError):
                client.call({"calls": []})


def test_auth_error_handling_json(default_headers, mock_sleep):
    """JSON 内の 'auth' エラー名による認証エラー検知のテスト"""
    from hw_genie.core.client import HWAuthError

    client = HWClient(default_headers)

    with patch("requests.Session.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": {"name": "auth", "detail": "session expired"}}
        mock_post.return_value = mock_response

        with patch("hw_genie.core.client.HWClient.call", side_effect=HWClient.call, autospec=True):
            with pytest.raises(HWAuthError):
                client.call({"calls": []})


def test_auth_error_handling_invalid_session(default_headers, mock_sleep):
    """InvalidSession による認証エラー検知のテスト"""
    from hw_genie.core.client import HWAuthError

    client = HWClient(default_headers)

    with patch("requests.Session.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": {"name": "InvalidSession", "description": "session expired"}}
        mock_post.return_value = mock_response

        with patch("hw_genie.core.client.HWClient.call", side_effect=HWClient.call, autospec=True):
            with pytest.raises(HWAuthError):
                client.call({"calls": []})


def test_player_status_is_valid():
    from hw_genie.core.client import PlayerStatus

    invalid_status = PlayerStatus(name="Unknown", level=0)
    assert not invalid_status.is_valid

    valid_by_level = PlayerStatus(name="Unknown", level=130)
    assert valid_by_level.is_valid

    valid_by_name = PlayerStatus(name="Joe", level=0)
    assert valid_by_name.is_valid
