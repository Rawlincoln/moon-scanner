"""Elite copy-trade API + HTML page."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.paths import BASE_DIR
from config import MAX_AGE_MINUTES_CAP
from services.elite_traders import roster_public
from services.scan_elite import scan_elite_signals

router = APIRouter(tags=["elite"])


@router.get("/elite")
async def elite_page():
    return FileResponse(BASE_DIR / "static" / "elite.html")


@router.get("/api/elite")
async def elite_scan(
    limit: int = Query(16, ge=1, le=40),
    max_age_minutes: float = Query(120, ge=5, le=MAX_AGE_MINUTES_CAP),
    force: bool = Query(False),
):
    """Tokens where top-20 smart wallets are on the book + full safety passes."""
    return await scan_elite_signals(
        limit=limit, max_age_minutes=max_age_minutes, force=force
    )


@router.get("/api/elite/traders")
async def elite_traders_list():
    """The 20 elite desk wallets (seeds + learned)."""
    return roster_public()
