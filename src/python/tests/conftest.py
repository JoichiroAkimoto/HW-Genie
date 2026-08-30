import os

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from hw_genie.core.database import Base
from hw_genie.core.client import HWClient, PlayerStatus

TURSO_KEYS_TO_SKIP = (
    "TURSO_SYNC_URL",
    "TURSO_AUTH_TOKEN",
    "TURSO_SYNC_INTERVAL",
    "TURSO_WRITE_REMOTE",
    "TURSO_READ_REMOTE",
    "TURSO_SYNC_ON_CONNECT",
)


@pytest.fixture(autouse=True)
def setup_db(tmp_path_factory):
    """
    テストごとに一時ファイル DB を初期化し、プロダクション DB への影響を遮断する。

    インメモリ SQLite（``sqlite:///:memory:``）は接続（セッション）ごとに別 DB が
    作られるため、読み取りセッションと書き込みセッションが別接続を持つと
    「書いた値が読めない」・並列テストで ``no such table`` になる。一時ファイル
    ベースにすることで全接続が同じ DB を共有し、本番（ローカルレプリカファイル）
    と同じ挙動になる。
    """
    # .env 由来の TURSO_* 設定がテストに漏れないよう一時的に除去する。
    # （build_database_config は os.environ を直接読むため、ローカルファイル
    #   モードでないとインメモリ差し替えと本物のリモートエンジンが分断される）
    saved = {k: os.environ.pop(k) for k in TURSO_KEYS_TO_SKIP if k in os.environ}
    db_file = tmp_path_factory.mktemp("testdb") / "test.db"
    try:
        test_engine = create_engine(f"sqlite:///{db_file}")
        test_SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

        def _get_test_engine():
            return test_engine

        def _get_test_session_local():
            return test_SessionLocal

        # 遅延初期化された engine / SessionLocal をテスト用に差し替える。
        # database と repository 双方の getter を差し替えることで、
        # 本番DBへの影響を完全に遮断する。
        with (
            patch("hw_genie.core.database.get_engine", _get_test_engine),
            patch("hw_genie.core.database.get_session_local", _get_test_session_local),
            patch("hw_genie.core.database.engine", test_engine),
            patch("hw_genie.core.database.SessionLocal", test_SessionLocal),
        ):
            with patch("hw_genie.core.repository.get_session_local", _get_test_session_local):
                Base.metadata.create_all(test_engine)
                yield
                Base.metadata.drop_all(test_engine)
                # モジュールレベルのエンジンキャッシュをクリアし、本番DBや
                # 前のテストのエンジンが残らないようにする。
                import hw_genie.core.database as _db

                for _attr in ("_engine", "_SessionLocal", "_write_engine", "_WriteSessionLocal"):
                    setattr(_db, _attr, None)
    finally:
        # 除去した TURSO_* 環境変数を元に戻す
        os.environ.update(saved)


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
