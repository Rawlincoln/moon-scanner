"""Alpha Tracker — group-mentioned tokens → pro BUY alerts."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse

from app.paths import BASE_DIR
from services.alert_auth import force_auth_ok
from services.alpha_tracker import scan_alpha_tracker, status as alpha_status

router = APIRouter(tags=["alpha"])


@router.get("/alpha")
async def alpha_page():
    """Lightweight status page (JSON UI via API; reuse money layout later if needed)."""
    # Prefer dedicated page if present, else redirect-style JSON via API consumers
    page = BASE_DIR / "static" / "alpha.html"
    if page.is_file():
        return FileResponse(page)
    return {
        "ok": True,
        "message": "Use /api/alpha — Alpha Tracker API",
        **alpha_status(),
    }


@router.get("/api/alpha")
async def alpha_status_api():
    return alpha_status()


@router.post("/api/alpha/scan")
async def alpha_scan(
    send: bool = True,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Run one Alpha Tracker cycle (discover → analyze → optional Telegram BUY)."""
    # Open for desk use; force path still accepts admin when locked down later
    _ = x_admin_key
    return await scan_alpha_tracker(send_alerts=send)


@router.post("/api/alpha/scan/force")
async def alpha_scan_force(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    if not force_auth_ok(x_admin_key=x_admin_key, bot_wired=False):
        raise HTTPException(status_code=401, detail="X-Admin-Key required")
    return await scan_alpha_tracker(send_alerts=True)
