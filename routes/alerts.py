"""Telegram alert status + test ping + cron tick (24/7 cloud).

P0: fail-closed when secrets missing in production; never put ADMIN key in query.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from config import ADMIN_API_KEY, TELEGRAM_CRON_SECRET
from services import telegram_alerts as tg
from services.alert_auth import (
    admin_header_ok,
    cron_secret_ok,
    force_auth_ok,
)

router = APIRouter(tags=["alerts"])


def _bot_wired() -> bool:
    st = tg.status()
    return bool(st.get("configured") or st.get("bot_set"))


@router.get("/api/alerts/status")
async def alerts_status():
    """Public-ish status (no secrets) — is Telegram wired?"""
    st = tg.status()
    return {
        "ok": True,
        **st,
        "auth": {
            "admin_key_configured": bool((ADMIN_API_KEY or "").strip()),
            "cron_secret_configured": bool((TELEGRAM_CRON_SECRET or "").strip()),
            "tick_query_accepts": "TELEGRAM_CRON_SECRET only (not ADMIN_API_KEY)",
        },
    }


@router.post("/api/alerts/telegram/test")
async def telegram_test(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Send a test Telegram message. Requires admin or cron secret header."""
    if not force_auth_ok(x_admin_key=x_admin_key, bot_wired=_bot_wired()):
        raise HTTPException(
            status_code=401,
            detail="X-Admin-Key required (admin or cron secret)",
        )
    return await tg.send_test_message()


@router.post("/api/alerts/telegram/cycle")
async def telegram_cycle(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Force one alert scan cycle. Requires admin header (not query)."""
    if not force_auth_ok(x_admin_key=x_admin_key, bot_wired=_bot_wired()):
        raise HTTPException(status_code=401, detail="X-Admin-Key required")
    return await tg.run_alert_cycle(force=True)


@router.get("/api/alerts/telegram/tick")
async def telegram_tick(
    key: str | None = Query(
        None,
        description="TELEGRAM_CRON_SECRET only (never put ADMIN_API_KEY in URLs)",
    ),
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """Cron-friendly GET: scan feeds and push new Telegram alerts.

    Auth (any one):
    - ``?key=TELEGRAM_CRON_SECRET`` (dumb cron services)
    - Header ``X-Cron-Secret: …``
    - Header ``X-Admin-Key: ADMIN_API_KEY``

    ADMIN_API_KEY is never accepted via query string.
    """
    if cron_secret_ok(x_cron_secret) or force_auth_ok(
        x_admin_key=x_admin_key,
        key=key,
        allow_query_cron=True,
        bot_wired=_bot_wired(),
    ):
        result = await tg.run_alert_cycle(force=True)
        return {"ok": True, "source": "cron_tick", **result}
    raise HTTPException(
        status_code=401,
        detail="invalid or missing TELEGRAM_CRON_SECRET (or X-Admin-Key)",
    )
