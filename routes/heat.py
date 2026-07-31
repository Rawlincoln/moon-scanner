"""Organic Heat API + HTML page (high-recall companion feed)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.paths import BASE_DIR
from config import MAX_AGE_MINUTES_CAP
from services.scan_heat import scan_organic_heat

router = APIRouter(tags=["heat"])


@router.get("/heat")
async def heat_page():
    return FileResponse(BASE_DIR / "static" / "heat.html")


@router.get("/api/heat")
async def heat_scan(
    limit: int = Query(16, ge=1, le=40),
    max_age_minutes: float = Query(120, ge=5, le=MAX_AGE_MINUTES_CAP),
    force: bool = Query(False),
):
    """High-recall organic climbers — explicit RISKY labels, not safe moons.

    ``force`` bypasses short cache; rate-limited like other scan endpoints.
    """
    return await scan_organic_heat(
        limit=limit, max_age_minutes=max_age_minutes, force=force
    )
