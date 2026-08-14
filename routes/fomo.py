"""FOMO aping channel — live elite buy/exit feed + API."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse

from app.paths import BASE_DIR
from services.alert_auth import force_auth_ok
from services.fomo_watch import poll_once, status as fomo_status

router = APIRouter(tags=["fomo"])


@router.get("/fomo")
async def fomo_page():
    return FileResponse(BASE_DIR / "static" / "fomo.html")


@router.get("/api/fomo")
async def fomo_status_api():
    """Recent FOMO events + watched wallets + poll health."""
    return fomo_status()


@router.post("/api/fomo/poll")
async def fomo_force_poll(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Force one FOMO poll cycle (admin)."""
    if not force_auth_ok(x_admin_key=x_admin_key, bot_wired=True):
        raise HTTPException(status_code=401, detail="X-Admin-Key required")
    return await poll_once(seed=False)
