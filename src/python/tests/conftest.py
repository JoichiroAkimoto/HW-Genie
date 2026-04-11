import pytest
from unittest.mock import MagicMock
from hw_genie.core.database import init_db, Base, engine
from hw_genie.core.client import HWClient, PlayerStatus

@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    yield
    Base.metadata.drop_all(engine)

@pytest.fixture
def mock_client():
    # 本物の HWClient インスタンスを作成し、call と fetch_player_status をモックする
    client = HWClient(headers={"x-auth-token": "test"})
    mock_call = MagicMock()
    client.call = mock_call
    
    # fetch_player_status もモック化（ネットワーク通信を防ぐため）
    status = PlayerStatus(name="TestUser", level=130, gold=1000, gems=100, energy=100, arena_rank="1", grand_rank="1")
    client.fetch_player_status = MagicMock(return_value=status)
    
    return client, mock_call

@pytest.fixture
def mock_sleep(mocker):
    return mocker.patch("time.sleep", return_value=None)

@pytest.fixture
def default_headers():
    return {"x-auth-session-id": "test", "x-auth-token": "test", "x-request-id": "100"}
