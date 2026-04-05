# Auth Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically capture authentication headers from the Hero Wars browser game and save them as HW-Genie sessions via a local HTTP server and userscript.

**Architecture:** FastAPI-based local HTTP server (`hw-genie auth-server`) that receives auth headers from a browser userscript. Uses nonce-based CSRF protection and CORS origin restriction. Reuses existing `auth.py` functions for session management.

**Tech Stack:** Python (FastAPI, uvicorn), TypeScript (userscript), pytest

---

### Task 1: Add FastAPI and uvicorn dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependencies to requirements.txt**

Add to the end of `requirements.txt`:
```
fastapi==0.115.6
uvicorn==0.34.0
```

- [ ] **Step 2: Install dependencies**

Run: `pip install fastapi==0.115.6 uvicorn==0.34.0`

- [ ] **Step 3: Verify installation**

Run: `python -c "import fastapi; import uvicorn; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add fastapi and uvicorn dependencies"
```

---

### Task 2: Create auth server module

**Files:**
- Create: `src/python/hw_genie/commands/auth_server.py`
- Test: `src/python/tests/test_auth_server.py`

- [ ] **Step 1: Write tests for nonce generation and validation**

Create `src/python/tests/test_auth_server.py`:

```python
"""Tests for the auth server module."""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from hw_genie.commands.auth_server import create_app, AuthServer


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
        assert "https://heroes-wb.nextersglobal.com" in data["allowed_origins"]

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
            "headers": {"x-auth-token": "test"}
        })
        assert response.status_code == 401

    @patch("hw_genie.commands.auth_server.update_session_with_headers")
    def test_post_auth_success(self, mock_update, client):
        mock_player = MagicMock()
        mock_player.name = "TestPlayer"
        mock_player.level = 100
        mock_player.gold = 1000
        mock_player.gems = 500
        mock_player.energy = 120
        mock_player.arena_rank = 10
        mock_player.grand_rank = 5
        mock_update.return_value = {
            "status": "success",
            "player": mock_player
        }

        # First get a valid nonce
        nonce_resp = client.get("/nonce")
        nonce = nonce_resp.json()["nonce"]

        response = client.post("/auth", json={
            "nonce": nonce,
            "account": "default",
            "headers": {
                "x-auth-application-id": "3",
                "x-auth-network-ident": "web",
                "x-auth-player-id": "12345",
                "x-auth-session-id": "abc",
                "x-auth-session-key": "",
                "x-auth-signature": "sig",
                "x-auth-token": "token",
                "x-auth-user-id": "67890",
                "x-request-id": "100"
            }
        })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["player"]["name"] == "TestPlayer"

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
            "headers": {"x-auth-token": "test"}
        })

        assert response.status_code == 500
        data = response.json()
        assert data["status"] == "error"


class TestValidateHeaders:
    """Test header validation."""

    def test_valid_headers_pass(self):
        from hw_genie.commands.auth_server import validate_auth_headers
        headers = {
            "x-auth-application-id": "3",
            "x-auth-network-ident": "web",
            "x-auth-player-id": "12345",
            "x-auth-session-id": "abc",
            "x-auth-signature": "sig",
            "x-auth-token": "token",
            "x-auth-user-id": "67890",
        }
        result = validate_auth_headers(headers)
        assert result is True

    def test_missing_required_header_fails(self):
        from hw_genie.commands.auth_server import validate_auth_headers
        headers = {
            "x-auth-token": "test"
        }
        result = validate_auth_headers(headers)
        assert result is False

    def test_empty_header_fails(self):
        from hw_genie.commands.auth_server import validate_auth_headers
        assert validate_auth_headers({}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest src/python/tests/test_auth_server.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'hw_genie.commands.auth_server'"

- [ ] **Step 3: Create the auth server module**

Create `src/python/hw_genie/commands/auth_server.py`:

```python
"""Auth server module for automatic authentication header capture."""
import os
import secrets
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from hw_genie.core.auth import update_session_with_headers


# TODO(security): Future enhancements
# 1. Add configurable nonce TTL (currently nonce is single-use with no timeout)
# 2. Add token-based authentication as an alternative to nonce
#    - Generate a persistent API key that userscript includes in every request
#    - Token passed via environment variable or config file
# 3. Make CORS origins configurable via config file / environment variable
#    - Current: hardcoded to heroes-wb.nextersglobal.com
#    - Future: HW_GENIE_AUTH_ALLOWED_ORIGINS env var or config file
# 4. Consider HTTPS/TLS option for non-localhost deployments


ALLOWED_ORIGINS = ["https://heroes-wb.nextersglobal.com"]
REQUIRED_HEADERS = [
    "x-auth-application-id",
    "x-auth-network-ident",
    "x-auth-player-id",
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


class AuthErrorResponse(BaseModel):
    status: str
    message: str


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
    app = FastAPI(title="HW-Genie Auth Server")

    # CORS middleware - restrict to Hero Wars origin only
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
            raise HTTPException(
                status_code=400,
                detail=f"Missing required headers. Required: {REQUIRED_HEADERS}"
            )

        # Update session
        account = request.account or "default"
        result = update_session_with_headers(request.headers, account)

        if result["status"] == "success":
            player_data = result["player"]
            if hasattr(player_data, "to_dict"):
                player_data = player_data.to_dict()
            return AuthSuccessResponse(status="success", player=player_data)
        else:
            raise HTTPException(
                status_code=500,
                detail=result.get("message", "Failed to update session")
            )

    return app


def run_server(host: str = "127.0.0.1", port: int = 8765, once: bool = False) -> None:
    """Run the auth server.

    Args:
        host: Host to bind to (default: 127.0.0.1)
        port: Port to listen on (default: 8765)
        once: If True, exit after first successful auth capture
    """
    import uvicorn

    if once:
        print(f"Auth server starting in single-capture mode on http://{host}:{port}")
    else:
        print(f"Auth server starting on http://{host}:{port}")
        print(f"Allowed origins: {', '.join(ALLOWED_ORIGINS)}")
        print("Press Ctrl+C to stop")

    uvicorn.run(create_app(), host=host, port=port, log_level="info")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest src/python/tests/test_auth_server.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run linter**

Run: `ruff check --fix src/python/hw_genie/commands/auth_server.py src/python/tests/test_auth_server.py`

- [ ] **Step 6: Commit**

```bash
git add src/python/hw_genie/commands/auth_server.py src/python/tests/test_auth_server.py
git commit -m "feat: add auth server with nonce-based CSRF protection"
```

---

### Task 3: Add auth-server CLI subcommand

**Files:**
- Modify: `src/python/hw_genie/main.py`

- [ ] **Step 1: Add auth-server subcommand to main.py**

Add import at the top of `main.py` (after existing imports):
```python
from hw_genie.commands.auth_server import run_server
```

Add `import os` at the top if not already present.

Add new command function before `main()`:
```python
def cmd_auth_server(args):
    """Start the auth capture server."""
    port = args.port or int(os.environ.get("HW_GENIE_AUTH_PORT", 8765))
    run_server(port=port, once=args.once)
```

Add subparser in `main()` function (after the auth subparser):
```python
# Auth Server
p_auth_server = subparsers.add_parser("auth-server", help="Start auth capture server")
p_auth_server.add_argument("--port", "-p", type=int, help=f"Port to listen on (default: 8765, env: HW_GENIE_AUTH_PORT)")
p_auth_server.add_argument("--once", action="store_true", help="Exit after first successful auth capture")
p_auth_server.set_defaults(func=cmd_auth_server)
```

- [ ] **Step 2: Verify CLI works**

Run: `.venv/bin/hw-genie auth-server --help`
Expected: Shows help with --port and --once options

- [ ] **Step 3: Run full test suite to ensure nothing is broken**

Run: `pytest -v`
Expected: All tests PASS (including new auth_server tests)

- [ ] **Step 4: Run linter**

Run: `ruff check --fix src/python/hw_genie/main.py`

- [ ] **Step 5: Commit**

```bash
git add src/python/hw_genie/main.py
git commit -m "feat: add auth-server CLI subcommand"
```

---

### Task 4: Implement the userscript

**Files:**
- Modify: `src/userscripts/index.ts`

- [ ] **Step 1: Write the userscript**

Replace the contents of `src/userscripts/index.ts` with:

```typescript
// ==UserScript==
// @name         HW-Genie Auth Capture
// @namespace    https://github.com/HW-Genie
// @version      1.0.0
// @description  Automatically capture auth headers and send to HW-Genie auth server
// @match        https://heroes-wb.nextersglobal.com/*
// @grant        none
// ==/UserScript==

(() => {
  "use strict";

  const AUTH_SERVER_URL = "http://localhost:8765";
  let captured = false;
  let headersCaptured: Record<string, string> | null = null;

  function log(msg: string, ...args: unknown[]) {
    console.log(`[HW-Genie] ${msg}`, ...args);
  }

  /**
   * Fetch a nonce from the auth server.
   */
  async function fetchNonce(): Promise<string | null> {
    try {
      const res = await fetch(`${AUTH_SERVER_URL}/nonce`);
      if (!res.ok) return null;
      const data = await res.json();
      return data.nonce;
    } catch {
      return null;
    }
  }

  /**
   * Send captured headers to the auth server.
   */
  async function sendHeaders(headers: Record<string, string>): Promise<boolean> {
    const nonce = await fetchNonce();
    if (!nonce) {
      log("Failed to fetch nonce. Is the auth server running?");
      return false;
    }

    try {
      const res = await fetch(`${AUTH_SERVER_URL}/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nonce, headers, account: "default" }),
      });

      const data = await res.json();

      if (res.ok && data.status === "success") {
        log(`Auth captured successfully! Player: ${data.player.name} (Lv.${data.player.level})`);
        return true;
      } else {
        log(`Auth failed: ${data.message || data.detail || "Unknown error"}`);
        return false;
      }
    } catch (e) {
      log(`Error sending auth: ${e}`);
      return false;
    }
  }

  /**
   * Intercept XMLHttpRequest to capture x-auth-* headers.
   */
  function interceptXHR() {
    const originalOpen = XMLHttpRequest.prototype.open;

    XMLHttpRequest.prototype.open = function (
      method: string,
      url: string | URL,
      ...rest: unknown[]
    ) {
      const urlString = url.toString();

      // Only intercept requests to the Hero Wars API
      if (!urlString.includes("heroes-wb.nextersglobal.com/api/")) {
        return originalOpen.apply(this, [method, url, ...rest]);
      }

      const originalSetRequestHeader = this.setRequestHeader.bind(this);
      this.setRequestHeader = function (name: string, value: string) {
        const lowerName = name.toLowerCase();
        if (lowerName.startsWith("x-auth-")) {
          if (!headersCaptured) {
            headersCaptured = {};
          }
          headersCaptured[lowerName] = value;
        }
        return originalSetRequestHeader(name, value);
      };

      return originalOpen.apply(this, [method, url, ...rest]);
    };
  }

  /**
   * Main: start interception and send headers once captured.
   */
  async function main() {
    log("Starting auth capture...");
    interceptXHR();

    // Poll for captured headers
    const pollInterval = setInterval(() => {
      if (captured) {
        clearInterval(pollInterval);
        return;
      }
      if (headersCaptured && Object.keys(headersCaptured).length >= 6) {
        captured = true;
        clearInterval(pollInterval);

        sendHeaders(headersCaptured).then((success) => {
          if (success) {
            log("Auth capture complete.");
          } else {
            captured = false; // Allow retry on failure
          }
        });
      }
    }, 500);
  }

  // Start when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
```

- [ ] **Step 2: Verify TypeScript compiles**

Run from `src/userscripts/`: `bun run --bun tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/userscripts/index.ts
git commit -m "feat: add userscript for automatic auth header capture"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `.agents/skills/hero-wars-auth/SKILL.md`

- [ ] **Step 1: Update AGENTS.md**

Add the auth-server command to the command list section:
```
*   **認証サーバー起動**: `hw-genie auth-server` (自動認証キャプチャ用)
*   **認証サーバー (1回限り)**: `hw-genie auth-server --once`
```

- [ ] **Step 2: Update README.md**

Add a section about the auth server feature in the appropriate place.

- [ ] **Step 3: Update hero-wars-auth SKILL.md**

Add documentation about the new auth-server subcommand as an alternative to manual curl-based auth.

- [ ] **Step 4: Run linter on all Python files**

Run: `ruff check --fix src/python/`

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md README.md .agents/skills/hero-wars-auth/SKILL.md
git commit -m "docs: add auth-server documentation"
```
