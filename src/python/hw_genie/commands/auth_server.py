"""Auth server module for automatic authentication header capture.

Also hosts a tiny in-memory ToE job queue so the Python CLI can request a
``progress/result`` from the userscript's ``Game.BattleCalc`` over HTTP. The
userscript polls ``GET /toe/job`` and posts back via ``POST /toe/result``.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
import uuid
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException
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
REQUIRED_HEADER_KEYS = [
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
    account: Optional[str] = None


class NonceResponse(BaseModel):
    nonce: str
    allowed_origins: list[str]


class AuthSuccessResponse(BaseModel):
    status: str
    player: dict


# ---------------------------------------------------------------------------
# ToE job queue (in-memory). The Python CLI pushes a battle and waits for the
# userscript to compute the result via the in-page ``Game.BattleCalc``.
# ---------------------------------------------------------------------------


class ToeJobStore:
    """Thread-safe in-memory FIFO for ToE bridge jobs.

    For a real deployment this would be backed by the Turso DB; the in-memory
    implementation is enough to validate the round trip while the userscript
    is running in the same host.
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._ttl = ttl_seconds

    def add(self, account: str, battle: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "account": account,
                "battle": battle,
                "status": "pending",
                "result": None,
                "created_at": time.time(),
            }
        return job_id

    def get(self, job_id: str, account: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job or job.get("account") != account:
            return None
        return job

    def claim(self, account: str) -> Optional[dict[str, Any]]:
        """Return the oldest pending job for ``account`` and mark it as in-flight."""
        with self._lock:
            pending = sorted(
                (j for j in self._jobs.values() if j.get("account") == account and j.get("status") == "pending"),
                key=lambda j: j["created_at"],
            )
            if not pending:
                return None
            job = pending[0]
            job["status"] = "in_flight"
        return job

    def submit(self, job_id: str, account: str, result: dict[str, Any]) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("account") != account:
                return False
            job["status"] = "done"
            job["result"] = result
            job["finished_at"] = time.time()
        return True

    def cleanup(self) -> int:
        """Drop jobs older than ``ttl_seconds`` regardless of status. Returns count dropped."""
        cutoff = time.time() - self._ttl
        with self._lock:
            stale = [jid for jid, j in self._jobs.items() if j.get("created_at", 0) < cutoff]
            for jid in stale:
                self._jobs.pop(jid, None)
        return len(stale)


def validate_auth_headers(headers: dict[str, str]) -> bool:
    """Check that all required x-auth-* headers are present."""
    return all(key in headers for key in REQUIRED_HEADER_KEYS)


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
_toe_jobs = ToeJobStore()


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
            raise HTTPException(status_code=400, detail=f"Missing required headers. Required: {REQUIRED_HEADER_KEYS}")

        # Update session
        account = request.account
        result = update_session_with_headers(request.headers, account)

        if result["status"] == "success":
            player_data = result["player"]
            if hasattr(player_data, "to_dict"):
                player_data = player_data.to_dict()
            return AuthSuccessResponse(status="success", player=player_data)
        else:
            raise HTTPException(status_code=500, detail=result.get("message", "Failed to update session"))

    # ---- ToE bridge ----------------------------------------------------
    # NOTE: Pydantic models declared as inner classes inside create_app() are
    # not picked up correctly as request bodies by FastAPI in this project's
    # pinned versions (they get treated as query parameters and 422). Use plain
    # ``dict[str, Any]`` and validate manually instead. The models still live as
    # type aliases for downstream clients.

    @app.post("/toe/job")
    def post_toe_job(job_request: dict[str, Any] = Body(...)):
        _toe_jobs.cleanup()
        account = str(job_request.get("account", ""))
        battle = job_request.get("battle") or {}
        if not account or not isinstance(battle, dict):
            raise HTTPException(status_code=400, detail="account and battle are required")
        job_id = _toe_jobs.add(account, battle)
        return {"id": job_id, "status": "pending"}

    @app.get("/toe/next")
    def get_toe_next(account: str):
        """Return the oldest pending job for ``account`` (or 204 if none)."""
        job = _toe_jobs.claim(account)
        if not job:
            raise HTTPException(status_code=204, detail="No pending job")
        return job

    @app.get("/toe/job/{job_id}")
    def get_toe_job(job_id: str, account: str):
        job = _toe_jobs.get(job_id, account)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.post("/toe/job/{job_id}/result")
    def post_toe_result(job_id: str, submit_request: dict[str, Any] = Body(...)):
        account = str(submit_request.get("account", ""))
        result = submit_request.get("result")
        if not account or not isinstance(result, dict):
            raise HTTPException(status_code=400, detail="account and result are required")
        ok = _toe_jobs.submit(job_id, account, result)
        if not ok:
            raise HTTPException(status_code=404, detail="job not found")
        return {"status": "ok"}

    return app


def run_server(host: str = "127.0.0.1", port: int = 8765, once: bool = False) -> None:
    """Run the auth server.

    Args:
        host: Host to bind to (default: 127.0.0.1)
        port: Port to listen on (default: 8765)
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

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
