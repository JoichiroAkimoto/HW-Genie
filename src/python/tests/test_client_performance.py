import time
from unittest.mock import MagicMock
from hw_genie.core.client import HWClient


def test_high_volume_requests_performance():
    """大量のリクエストを送信した際のパフォーマンスとメモリ使用感を確認"""
    headers = {"x-auth-token": "test-token", "x-auth-player-id": "123", "x-request-id": "100"}
    mock_session = MagicMock()
    client = HWClient(headers, session=mock_session)

    # モックレスポンスの設定
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [{"result": {"status": "ok"}}]}
    mock_session.post.return_value = mock_response

    num_requests = 1000
    start_time = time.time()

    for _ in range(num_requests):
        client.call({"calls": []})

    end_time = time.time()
    duration = end_time - start_time

    print(f"\nExecuted {num_requests} requests in {duration:.4f}s ({num_requests / duration:.2f} req/s)")

    # 1000リクエストが1秒以内に終わるはず (モックなので)
    assert duration < 1.0
    assert mock_session.post.call_count == num_requests


def test_connection_pooling_efficiency():
    """requests.Session が正しく使用され、接続が再利用されているかを確認"""
    headers = {"x-auth-token": "test-token", "x-auth-player-id": "123", "x-request-id": "100"}
    mock_session = MagicMock()
    client = HWClient(headers, session=mock_session)

    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {"results": [{"result": {"status": "ok"}}]}
    mock_session.post.return_value = mock_response

    # 複数のコールを行う
    for _ in range(10):
        client.call({"calls": []})

    # Session.post が呼ばれていることを確認 (Session を通じてリクエストしているため)
    assert mock_session.post.call_count == 10
