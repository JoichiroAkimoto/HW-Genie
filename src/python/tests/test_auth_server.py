"""Tests for the auth server module."""
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from hw_genie.commands.auth_server import create_app, AuthServer, run_server, validate_auth_headers


VALID_HEADERS = {
    "x-auth-application-id": "3",
    "x-auth-network-ident": "web",
    "x-auth-session-id": "abc",
    "x-auth-signature": "sig",
    "x-auth-token": "token",
    "x-auth-user-id": "67890",
    "x-request-id": "100",
}


class TestAuthServerNonce:
    """Test nonce generation and validation."""

    def test_generate_nonce_returns_string(self):
        server = AuthServer()
        nonce = server.generate_nonce()
        assert isinstance(nonce, str)
        assert len(nonce) == 32

    def test_nonce_is_unique(self):
        server = AuthServer()
        nonce1 = server.generate_nonce()
        nonce2 = server.generate_nonce()
        assert nonce1 != nonce2

    def test_validate_nonce_valid(self):
        server = AuthServer()
        server.current_nonce = "abc123"
        assert server.validate_nonce("abc123") is True

    def test_validate_nonce_invalid(self):
        server = AuthServer()
        server.current_nonce = "abc123"
        assert server.validate_nonce("wrong") is False

    def test_validate_nonce_consumed(self):
        server = AuthServer()
        server.current_nonce = "abc123"
        server.validate_nonce("abc123")
        assert server.current_nonce is None  # Nonce consumed


class TestAuthServerEndpoints:
    """Test FastAPI endpoints."""

    @pytest.fixture
    def client(self):
        app = create_app()
        return TestClient(app)

    def test_get_nonce_returns_nonce_and_origins(self, client):
        response = client.get("/nonce")
        assert response.status_code == 200
        data = response.json()
        assert "nonce" in data
        assert "allowed_origins" in data
        assert "https://www.hero-wars.com" in data["allowed_origins"]

    def test_get_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_post_auth_missing_nonce(self, client):
        response = client.post("/auth", json={"headers": {}})
        assert response.status_code == 422  # Validation error

    def test_post_auth_invalid_nonce(self, client):
        response = client.post("/auth", json={
            "nonce": "invalid",
            "headers": VALID_HEADERS
        })
        assert response.status_code == 401

    @patch("hw_genie.commands.auth_server.update_session_with_headers")
    def test_post_auth_success(self, mock_update, client):
        mock_update.return_value = {
            "status": "success",
            "player": {
                "name": "TestPlayer",
                "level": 100,
                "gold": 1000,
                "gems": 500,
                "energy": 120,
                "arena_rank": 10,
                "grand_rank": 5,
            }
        }

        nonce_resp = client.get("/nonce")
        nonce = nonce_resp.json()["nonce"]

        response = client.post("/auth", json={
            "nonce": nonce,
            "headers": VALID_HEADERS
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["player"]["name"] == "TestPlayer"
        # userscript は account を送らないため None が渡され、実名で保存される
        mock_update.assert_called_once_with(VALID_HEADERS, None)

    @patch("hw_genie.commands.auth_server.update_session_with_headers")
    def test_post_auth_api_error(self, mock_update, client):
        mock_update.return_value = {
            "status": "error",
            "message": "API connection failed"
        }

        nonce_resp = client.get("/nonce")
        nonce = nonce_resp.json()["nonce"]

        response = client.post("/auth", json={
            "nonce": nonce,
            "headers": VALID_HEADERS
        })

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data


class TestValidateHeaders:
    """Test header validation."""

    def test_valid_headers_pass(self):
        assert validate_auth_headers(VALID_HEADERS) is True

    def test_missing_required_header_fails(self):
        headers = {"x-auth-token": "test"}
        assert validate_auth_headers(headers) is False

    def test_empty_header_fails(self):
        assert validate_auth_headers({}) is False

    def test_headers_without_player_id_pass(self):
        """x-auth-player-id is optional and should not cause validation failure."""
        headers = {
            "x-auth-application-id": "3",
            "x-auth-network-ident": "web",
            "x-auth-session-id": "abc",
            "x-auth-signature": "sig",
            "x-auth-token": "token",
            "x-auth-user-id": "67890",
        }
        assert validate_auth_headers(headers) is True


class TestAuthServerStartup:
    @patch("uvicorn.run")
    @patch("hw_genie.commands.auth_server.init_db")
    def test_run_server_does_not_enable_reload_from_environment(self, mock_init_db, mock_uvicorn_run, monkeypatch):
        monkeypatch.setenv("HW_GENIE_AUTH_RELOAD", "true")

        run_server()

        args, kwargs = mock_uvicorn_run.call_args
        assert not isinstance(args[0], str)
        assert kwargs.get("reload") is None
