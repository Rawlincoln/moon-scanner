"""Safe snipes API + HTML page."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.paths import BASE_DIR
from config import MAX_AGE_MINUTES_CAP
from services.scan_snipes import scan_safe_snipes

router = APIRouter(tags=["snipes"])


@router.get("/snipes")
async def snipes_page():
    return FileResponse(BASE_DIR / "static" / "snipes.html")


@router.get("/api/snipes")
async def snipes_scan(
    limit: int = Query(12, ge=1, le=30),
    max_age_minutes: float = Query(60, ge=5, le=MAX_AGE_MINUTES_CAP),
    force: bool = Query(False),
):
    """Safe early entries sized for ~2× take-profit (capital filters)."""
    return await scan_safe_snipes(
        limit=limit, max_age_minutes=max_age_minutes, force=force
    )
