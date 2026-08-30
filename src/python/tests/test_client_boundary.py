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


def test_build_mission_payload_boundary_values(client_with_mock_session):
    """ミッションレイド用ペイロードの引数（回数）の境界値を検証"""
    client, _ = client_with_mock_session

    # 1. 正常な回数指定
    payload_valid = client.build_mission_payload(mission_id=1, times=2)
    assert payload_valid["calls"][0]["args"]["times"] == 2

    # 2. 0回指定でエラーになること
    with pytest.raises(ValueError, match="times must be between 1 and 3"):
        client.build_mission_payload(mission_id=1, times=0)

    # 3. 負の数指定でエラーになること
    with pytest.raises(ValueError, match="times must be between 1 and 3"):
        client.build_mission_payload(mission_id=1, times=-1)

    # 4. 大量回数指定でエラーになること
    with pytest.raises(ValueError, match="times must be between 1 and 3"):
        client.build_mission_payload(mission_id=1, times=9999)
