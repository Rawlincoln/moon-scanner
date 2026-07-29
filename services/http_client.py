"""Shared httpx AsyncClient — one pool for the whole app.

Call `startup()` from FastAPI lifespan and `shutdown()` on exit.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger("moon-scanner.http")

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Return the shared client (lazy-create if lifespan not used)."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=4.0),
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
            follow_redirects=True,
        )
    return _client


async def startup() -> None:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=4.0),
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
            follow_redirects=True,
        )
        logger.info("Shared httpx client started")


async def shutdown() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        logger.info("Shared httpx client closed")
    _client = None


async def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    client = get_client()
    kw: dict[str, Any] = {"params": params, "headers": headers}
    if timeout is not None:
        kw["timeout"] = timeout
    return await client.get(url, **kw)
