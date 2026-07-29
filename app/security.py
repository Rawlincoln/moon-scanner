"""Admin auth, rate limiting, address validation, CORS helpers."""

from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Callable

from fastapi import Header, HTTPException, Query, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from config import (
    ADMIN_API_KEY,
    CORS_ORIGINS_LIST,
    IS_PRODUCTION,
    RATE_LIMIT_BURST,
    RATE_LIMIT_PER_MIN,
)

# Solana base58 mint (no 0,O,I,l) — 32–44 chars
_SOL_MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# Paths that are expensive / amplify third-party APIs
_EXPENSIVE_PREFIXES = (
    "/api/moon",
    "/api/snipes",
    "/api/analyze",
    "/api/checkers",
    "/api/scan",
    "/api/invest",
    "/api/padre",
    "/api/runner-radar",
    "/api/learning/predict",
    "/api/pumpfun",
)


def cors_allow_origins() -> list[str]:
    if CORS_ORIGINS_LIST:
        return list(CORS_ORIGINS_LIST)
    if IS_PRODUCTION:
        return [
            "https://moon-scanner-9tlz.onrender.com",
            "https://moon-scanner.onrender.com",
        ]
    return [
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]


def validate_token_address(chain_id: str, token_address: str) -> str:
    """Normalize and validate chain + address; raises HTTPException 400."""
    chain = (chain_id or "").lower().strip()
    addr = (token_address or "").strip()
    if not addr:
        raise HTTPException(400, "token_address required")
    if chain == "solana":
        if not _SOL_MINT_RE.match(addr):
            raise HTTPException(
                400,
                "Invalid Solana mint (expect base58, 32–44 chars, no 0/O/I/l)",
            )
        return addr
    # EVM
    if not _EVM_RE.match(addr):
        raise HTTPException(400, "Invalid EVM address (expect 0x + 40 hex)")
    return addr


def safe_secret_eq(provided: str, expected: str) -> bool:
    """Constant-time compare that never raises on length mismatch."""
    if not provided or not expected:
        return False
    if len(provided) != len(expected):
        return False
    try:
        return secrets.compare_digest(provided, expected)
    except (TypeError, ValueError):
        return False


def _admin_provided(request: Request, header_key: str | None) -> str:
    # Header only — never query string (logs / Referer leak).
    return (
        (header_key or "").strip()
        or (request.headers.get("X-Admin-Key") or "").strip()
    )


def require_admin(
    request: Request,
    x_admin_key: str | None = Header(None, alias="X-Admin-Key"),
) -> None:
    """Gate destructive / admin routes.

    - ADMIN_API_KEY set → must match X-Admin-Key header
    - Production + no key configured → disabled (403)
    - Local + no key → allowed (dev convenience)
    """
    expected = (ADMIN_API_KEY or "").strip()
    provided = _admin_provided(request, x_admin_key)
    if expected:
        if not safe_secret_eq(provided, expected):
            raise HTTPException(401, detail="Invalid or missing admin key")
        return
    if IS_PRODUCTION:
        raise HTTPException(
            403,
            detail="Admin routes disabled until ADMIN_API_KEY is set",
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-IP sliding window for expensive API routes."""

    def __init__(self, app, *, per_min: int | None = None, burst: int | None = None):
        super().__init__(app)
        self.per_min = int(per_min if per_min is not None else RATE_LIMIT_PER_MIN)
        self.burst = int(burst if burst is not None else RATE_LIMIT_BURST)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _client_ip(self, request: Request) -> str:
        xff = request.headers.get("x-forwarded-for") or ""
        if xff:
            return xff.split(",")[0].strip() or "unknown"
        if request.client:
            return request.client.host or "unknown"
        return "unknown"

    def _is_expensive(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in _EXPENSIVE_PREFIXES)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path or ""
        if not self._is_expensive(path):
            return await call_next(request)

        # Admin key bypasses rate limit (ops / force scans)
        expected = (ADMIN_API_KEY or "").strip()
        provided = (request.headers.get("X-Admin-Key") or "").strip()
        if expected and safe_secret_eq(provided, expected):
            return await call_next(request)

        ip = self._client_ip(request)
        now = time.time()
        window = 60.0
        with self._lock:
            q = self._hits[ip]
            while q and now - q[0] > window:
                q.popleft()
            # Allow short burst above per_min for snappy UI double-clicks
            limit = max(self.per_min, self.burst)
            if len(q) >= limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "ok": False,
                        "error": "rate_limited",
                        "detail": f"Too many scan/analyze requests — max ~{self.per_min}/min",
                    },
                    headers={"Retry-After": "15"},
                )
            q.append(now)
        return await call_next(request)
