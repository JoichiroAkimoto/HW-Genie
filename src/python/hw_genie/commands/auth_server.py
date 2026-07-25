"""Auth server module for automatic authentication header capture."""

import os
import secrets
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from hw_genie.core.auth import update_session_with_headers
from hw_genie.core.database import init_db


# TODO(security): Future enhancements
# 1. Add configurable nonce TTL (currently nonce is single-use with no timeout)
# 2. Add token-based authentication as an alternative to nonce
#    - Generate a persistent API key that userscript includes in every request
#    - Token passed via environment variable or config file
# 3. Make CORS origins configurable via config file / environment variable
#    - Current: hardcoded origins below
#    - Future: HW_GENIE_AUTH_ALLOWED_ORIGINS env var or config file
# 4. Consider HTTPS/TLS option for non-localhost deployments


def _get_allowed_origins() -> list[str]:
    """Get allowed CORS origins from env var or defaults."""
    env_origins = os.environ.get("HW_GENIE_AUTH_ALLOWED_ORIGINS", "")
    if env_origins:
        return [o.strip() for o in env_origins.split(",") if o.strip()]
    return [
        "https://www.hero-wars.com",
        "https://heroes-wb.nextersglobal.com",
    ]


ALLOWED_ORIGINS = _get_allowed_origins()
REQUIRED_HEADERS = [
    "x-auth-application-id",
    "x-auth-network-ident",
    "x-auth-session-id",
    "x-auth-signature",
    "x-auth-token",
    "x-auth-user-id",
]


class AuthRequest(BaseModel):
    nonce: str
    headers: dict[str, str]
    account: Optional[str] = "default"


class NonceResponse(BaseModel):
    nonce: str
    allowed_origins: list[str]


class AuthSuccessResponse(BaseModel):
    status: str
    player: dict


def validate_auth_headers(headers: dict[str, str]) -> bool:
    """Check that all required x-auth-* headers are present."""
    return all(key in headers for key in REQUIRED_HEADERS)


class AuthServer:
    """Manages nonce generation and validation for auth requests."""

    def __init__(self):
        self.current_nonce: str | None = None
        self._generate_nonce()

    def _generate_nonce(self) -> None:
        self.current_nonce = secrets.token_hex(16)

    def generate_nonce(self) -> str:
        """Generate and return a new nonce."""
        self._generate_nonce()
        return self.current_nonce

    def validate_nonce(self, nonce: str) -> bool:
        """Validate and consume a nonce. Returns True if valid."""
        if self.current_nonce and secrets.compare_digest(nonce, self.current_nonce):
            self.current_nonce = None  # Consume nonce
            return True
        return False


# Global server instance for nonce management
_auth_server = AuthServer()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    init_db()  # Ensure tables exist before handling requests
    app = FastAPI(title="HW-Genie Auth Server")

    # CORS middleware - restrict to Hero Wars origins only
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/nonce")
    def get_nonce():
        nonce = _auth_server.generate_nonce()
        return NonceResponse(nonce=nonce, allowed_origins=ALLOWED_ORIGINS)

    @app.post("/auth", response_model=AuthSuccessResponse)
    def post_auth(request: AuthRequest):
        # Validate nonce
        if not _auth_server.validate_nonce(request.nonce):
            raise HTTPException(status_code=401, detail="Invalid or expired nonce")

        # Validate headers
        if not validate_auth_headers(request.headers):
            raise HTTPException(status_code=400, detail=f"Missing required headers. Required: {REQUIRED_HEADERS}")

        # Update session
        account = request.account or "default"
        result = update_session_with_headers(request.headers, account)

        if result["status"] == "success":
            player_data = result["player"]
            if hasattr(player_data, "to_dict"):
                player_data = player_data.to_dict()
            return AuthSuccessResponse(status="success", player=player_data)
        else:
            raise HTTPException(status_code=500, detail=result.get("message", "Failed to update session"))

    return app


def run_server(host: str = "127.0.0.1", port: int = 8765, once: bool = False) -> None:
    """Run the auth server.

    Args:
        host: Host to bind to (default: 127.0.0.1)
        port: Port to bind to (default: 8765)
        once: If True, exit after first successful auth capture
    """
    # Ensure DB tables are created before starting the server
    init_db()

    import uvicorn

    if once:
        print(f"Auth server starting in single-capture mode on http://{host}:{port}")
    else:
        print(f"Auth server starting on http://{host}:{port}")
        print(f"Allowed origins: {', '.join(ALLOWED_ORIGINS)}")
        print("Press Ctrl+C to stop")

    reload_enabled = os.environ.get("HW_GENIE_AUTH_RELOAD", "").lower() in ("1", "true", "yes")

    if reload_enabled:
        uvicorn.run(
            "hw_genie.commands.auth_server:create_app",
            host=host,
            port=port,
            log_level="info",
            reload=True,
            factory=True,
        )
    else:
        uvicorn.run(create_app(), host=host, port=port, log_level="info")
