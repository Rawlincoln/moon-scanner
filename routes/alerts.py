"""Telegram alert status + test ping + cron tick (24/7 cloud)."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from app.security import safe_secret_eq
from config import ADMIN_API_KEY, TELEGRAM_CRON_SECRET
from services import telegram_alerts as tg

router = APIRouter(tags=["alerts"])


def _auth_ok(
    *,
    x_admin_key: str | None = None,
    key: str | None = None,
) -> bool:
    """Accept ADMIN_API_KEY header or TELEGRAM_CRON_SECRET / admin as ?key=."""
    provided = (x_admin_key or key or "").strip()
    if not provided:
        # No secret configured → allow only when nothing is set (local dev)
        return not (ADMIN_API_KEY or TELEGRAM_CRON_SECRET)
    if ADMIN_API_KEY and safe_secret_eq(provided, ADMIN_API_KEY):
        return True
    if TELEGRAM_CRON_SECRET and safe_secret_eq(provided, TELEGRAM_CRON_SECRET):
        return True
    return False


@router.get("/api/alerts/status")
async def alerts_status():
    """Public-ish status (no secrets) — is Telegram wired?"""
    st = tg.status()
    return {"ok": True, **st}


@router.post("/api/alerts/telegram/test")
async def telegram_test(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    """Send a test Telegram message. Uses admin key if ADMIN_API_KEY is set."""
    if not _auth_ok(x_admin_key=x_admin_key):
        raise HTTPException(status_code=401, detail="admin key required")
    result = await tg.send_test_message()
    return result


@router.post("/api/alerts/telegram/cycle")
async def telegram_cycle(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    """Force one alert scan cycle (admin)."""
    if not _auth_ok(x_admin_key=x_admin_key):
        raise HTTPException(status_code=401, detail="admin key required")
    return await tg.run_alert_cycle(force=True)


@router.get("/api/alerts/telegram/tick")
async def telegram_tick(
    key: str | None = Query(None, description="TELEGRAM_CRON_SECRET or ADMIN_API_KEY"),
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Cron-friendly GET: scan feeds and push new Telegram alerts.

    Use this for 24/7 when hosting on free Render (spins down) or any cloud host.
    Example cron every 2–3 minutes:
      https://YOUR.onrender.com/api/alerts/telegram/tick?key=YOUR_SECRET
    """
    if not _auth_ok(x_admin_key=x_admin_key, key=key):
        raise HTTPException(status_code=401, detail="invalid or missing key")
    result = await tg.run_alert_cycle(force=True)
    return {"ok": True, "source": "cron_tick", **result}
