"""Graduated / large runners API + HTML page."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.paths import BASE_DIR
from config import MAX_AGE_MINUTES_CAP
from services.scan_graduated import scan_graduated_runners

router = APIRouter(tags=["graduated"])


@router.get("/graduated")
async def graduated_page():
    return FileResponse(BASE_DIR / "static" / "graduated.html")


@router.get("/api/graduated")
async def graduated_scan(
    limit: int = Query(16, ge=1, le=40),
    max_age_minutes: float = Query(10080, ge=30, le=max(MAX_AGE_MINUTES_CAP, 20160)),
    force: bool = Query(False),
):
    """Post-migration / large mcap runners (not early heat/snipes)."""
    return await scan_graduated_runners(
        limit=limit, max_age_minutes=max_age_minutes, force=force
    )
