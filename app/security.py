"""Admin auth, rate limiting, address validation, CORS helpers."""

from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Callable
from urllib.parse import parse_qs, urlparse

from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from config import (
    ADMIN_API_KEY,
    CORS_ORIGINS_LIST,
    IS_PRODUCTION,
    RATE_LIMIT_ANALYZE_PER_MIN,
    RATE_LIMIT_BURST,
    RATE_LIMIT_FORCE_COST,
    RATE_LIMIT_PER_MIN,
    TRUST_X_FORWARDED_FOR,
)

# Solana base58 mint (no 0,O,I,l) — 32–44 chars
_SOL_MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# Paths that are expensive / amplify third-party APIs (scan + force-refresh)
_EXPENSIVE_PREFIXES = (
    "/api/moon",
    "/api/snipes",
    "/api/heat",
    "/api/elite",
    "/api/graduated",
    "/api/analyze",
    "/api/checkers",
    "/api/scan",
    "/api/invest",
    "/api/padre",
    "/api/runner-radar",
    "/api/learning/predict",
    "/api/pumpfun",
    "/api/alerts/telegram/cycle",
    "/api/alerts/telegram/tick",
)
# Cheap JSON under those prefixes — never burn rate budget
_EXPENSIVE_EXEMPT = (
    "/api/elite/traders",
    "/api/fomo",
    "/api/fomo/wallets",
    "/api/fomo/wallets/export",
    "/api/fomo/wallets/import",
    "/api/alpha",
    "/api/moon/outcomes",
    "/api/moon/outcomes/export",
    "/api/moon/outcomes/import",
    "/api/alerts/status",
    "/api/realtime/status",
)

_ANALYZE_PREFIXES = (
    "/api/analyze",
    "/api/checkers",
    "/api/learning/predict",
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


def client_ip(request: Request) -> str:
    """Resolve client IP without trusting spoofable leftmost XFF.

    - Local / TRUST_X_FORWARDED_FOR=false: use TCP peer only.
    - Production with trusted reverse proxy: use **rightmost** XFF hop
      (the IP the proxy saw / appended), not the client-supplied first hop.
    """
    peer = ""
    if request.client:
        peer = (request.client.host or "").strip()
    if not TRUST_X_FORWARDED_FOR:
        return peer or "unknown"
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if not xff:
        return peer or "unknown"
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if not parts:
        return peer or "unknown"
    # Rightmost = last proxy-appended client (not attacker-controlled first hop)
    return parts[-1]


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Basic browser hardening headers (no secrets)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        # Allow same-origin API + fonts + padre/dex external navigations from UI
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' https: data:; "
            "connect-src 'self' https:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'",
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding windows for expensive API routes.

    - 60s window capped at RATE_LIMIT_PER_MIN (scan) or ANALYZE limit
    - 10s burst window capped at RATE_LIMIT_BURST
    - force=true costs RATE_LIMIT_FORCE_COST tokens
    """

    def __init__(self, app, *, per_min: int | None = None, burst: int | None = None):
        super().__init__(app)
        self.per_min = int(per_min if per_min is not None else RATE_LIMIT_PER_MIN)
        self.burst = int(burst if burst is not None else RATE_LIMIT_BURST)
        self.analyze_per_min = int(RATE_LIMIT_ANALYZE_PER_MIN)
        self.force_cost = max(1, int(RATE_LIMIT_FORCE_COST))
        self._hits_min: dict[str, deque[float]] = defaultdict(deque)
        self._hits_burst: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _is_expensive(self, path: str) -> bool:
        if path in _EXPENSIVE_EXEMPT:
            return False
        if any(path == e or path.startswith(e + "/") for e in _EXPENSIVE_EXEMPT):
            return False
        return any(path == p or path.startswith(p + "/") for p in _EXPENSIVE_PREFIXES)

    def _is_analyze(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in _ANALYZE_PREFIXES)

    def _request_cost(self, request: Request) -> int:
        q = parse_qs(urlparse(str(request.url)).query)
        force_vals = [v.lower() for v in q.get("force", [])]
        if any(v in ("1", "true", "yes") for v in force_vals):
            return self.force_cost
        return 1

    def _limit_for_path(self, path: str) -> int:
        if self._is_analyze(path):
            return self.analyze_per_min
        return self.per_min

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path or ""
        if not self._is_expensive(path):
            return await call_next(request)

        expected = (ADMIN_API_KEY or "").strip()
        provided = (request.headers.get("X-Admin-Key") or "").strip()
        if expected and safe_secret_eq(provided, expected):
            return await call_next(request)

        ip = client_ip(request)
        now = time.time()
        cost = self._request_cost(request)
        min_limit = self._limit_for_path(path)
        burst_limit = self.burst

        with self._lock:
            qmin = self._hits_min[ip]
            qburst = self._hits_burst[ip]
            while qmin and now - qmin[0] > 60.0:
                qmin.popleft()
            while qburst and now - qburst[0] > 10.0:
                qburst.popleft()
            if len(qburst) + cost > burst_limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "ok": False,
                        "error": "rate_limited",
                        "detail": f"Burst limit — max ~{burst_limit} expensive calls / 10s",
                    },
                    headers={"Retry-After": "10"},
                )
            if len(qmin) + cost > min_limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "ok": False,
                        "error": "rate_limited",
                        "detail": (
                            f"Too many requests — max ~{min_limit}/min"
                            + (
                                " for analyze"
                                if self._is_analyze(path)
                                else " for scans"
                            )
                            + (
                                f" (force costs {self.force_cost}x)"
                                if cost > 1
                                else ""
                            )
                        ),
                    },
                    headers={"Retry-After": "20"},
                )
            for _ in range(cost):
                qmin.append(now)
                qburst.append(now)
        return await call_next(request)
