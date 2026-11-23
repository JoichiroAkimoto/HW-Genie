"""Hero Wars API request signing.

The web client signs every RPC request; the server has started enforcing it
(freshly captured headers replayed with a different body are rejected with
``Invalid signature``). The client implementation lives in
``com.progrestar.common.util.RequestSignatureHelper`` of the game bundle:

    md5(f"{requestId}:{token}:{sessionId}:{body}:{env}")

- ``body`` is the client's compact JSON serialization (the server canonicalizes,
  so insignificant whitespace on the wire is fine).
- ``env`` is the concatenation of ``"<NAME>=<value>"`` for every header whose
  name contains ``X-Env`` (case-sensitive), where ``NAME`` is the header name
  from index 6 uppercased, sorted by ``NAME``. Captured sessions normally have
  no ``X-Env`` headers, which is valid: the part is then empty.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

SIGNATURE_HEADER = "x-auth-signature"


def serialize_body(payload: Mapping[str, Any]) -> str:
    """Serialize a payload the way the client does (compact, raw UTF-8)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup (HTTP header names are case-insensitive)."""
    for key, value in headers.items():
        if key.lower() == name:
            return value or ""
    return ""


def _env_signature_part(headers: Mapping[str, str]) -> str:
    pairs = [
        (key[6:].upper(), value or "")
        for key, value in headers.items()
        if "X-Env" in key
    ]
    pairs.sort(key=lambda item: item[0])
    return "".join(f"{name}={value}" for name, value in pairs)


def compute_signature(headers: Mapping[str, str], body: str) -> str:
    """Return the ``x-auth-signature`` value for ``body`` and ``headers``."""
    source = ":".join(
        (
            _header(headers, "x-request-id"),
            _header(headers, "x-auth-token"),
            _header(headers, "x-auth-session-id"),
            body,
            _env_signature_part(headers),
        )
    )
    return hashlib.md5(source.encode("utf-8")).hexdigest()


def sign_headers(headers: dict[str, str], body: str) -> None:
    """Set ``x-auth-signature`` on ``headers`` for the given body."""
    headers[SIGNATURE_HEADER] = compute_signature(headers, body)
