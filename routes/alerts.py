"""Telegram alert status + test ping."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.security import safe_secret_eq
from config import ADMIN_API_KEY
from services import telegram_alerts as tg

router = APIRouter(tags=["alerts"])


@router.get("/api/alerts/status")
async def alerts_status():
    """Public-ish status (no secrets) — is Telegram wired?"""
    st = tg.status()
    return {"ok": True, **st}


@router.post("/api/alerts/telegram/test")
async def telegram_test(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    """Send a test Telegram message. Uses admin key if ADMIN_API_KEY is set."""
    expected = (ADMIN_API_KEY or "").strip()
    if expected and not safe_secret_eq((x_admin_key or "").strip(), expected):
        raise HTTPException(status_code=401, detail="admin key required")
    result = await tg.send_test_message()
    return result


@router.post("/api/alerts/telegram/cycle")
async def telegram_cycle(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    """Force one alert scan cycle (admin)."""
    expected = (ADMIN_API_KEY or "").strip()
    if expected and not safe_secret_eq((x_admin_key or "").strip(), expected):
        raise HTTPException(status_code=401, detail="admin key required")
    return await tg.run_alert_cycle(force=True)
