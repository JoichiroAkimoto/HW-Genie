import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from hw_genie.core.database import Base
from hw_genie.core.client import HWClient, PlayerStatus

@pytest.fixture(autouse=True)
def setup_db():
    """
    テストごとにインメモリDBを初期化し、プロダクションDBへの影響を遮断する。
    """
    test_engine = create_engine("sqlite:///:memory:")
    test_SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

    def _get_test_engine():
        return test_engine

    def _get_test_session_local():
        return test_SessionLocal

    # 遅延初期化された engine / SessionLocal をテスト用に差し替える。
    # database と repository 双方の getter を差し替えることで、
    # 本番DBへの影響を完全に遮断する。
    with patch("hw_genie.core.database.get_engine", _get_test_engine), \
         patch("hw_genie.core.database.get_session_local", _get_test_session_local), \
         patch("hw_genie.core.database.engine", test_engine), \
         patch("hw_genie.core.database.SessionLocal", test_SessionLocal):
        with patch("hw_genie.core.repository.get_session_local", _get_test_session_local):
            Base.metadata.create_all(test_engine)
            yield
            Base.metadata.drop_all(test_engine)

@pytest.fixture
def mock_client():
    # 本物の HWClient インスタンスを作成し、call と fetch_player_status をモックする
    client = HWClient(headers={"x-auth-token": "test"})
    mock_call = MagicMock()
    client.call = mock_call
    
    # fetch_player_status もモック化（ネットワーク通信を防ぐため）
    status = PlayerStatus(name="TestUser", level=130, gold=1000, gems=100, energy=100, arena_rank=1, grand_rank=1)
    client.fetch_player_status = MagicMock(return_value=status)
    
    return client, mock_call

@pytest.fixture
def mock_sleep(mocker):
    return mocker.patch("time.sleep", return_value=None)

@pytest.fixture
def default_headers():
    return {"x-auth-session-id": "test", "x-auth-token": "test", "x-request-id": "100"}
