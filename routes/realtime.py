"""Realtime feed status and launch bus."""

from __future__ import annotations

from fastapi import APIRouter, Query

from services.realtime_bus import realtime_bus
from services.yellowstone_feed import status_payload as realtime_status_payload

router = APIRouter(tags=["realtime"])


@router.get("/api/realtime/status")
async def realtime_status():
    """Geyser / Yellowstone / logsSubscribe stack status."""
    return realtime_status_payload()


@router.get("/api/realtime/launches")
async def realtime_launches(
    limit: int = Query(30, ge=1, le=100),
    max_age_sec: float = Query(300, ge=30, le=3600),
):
    """Recent create/buy mints from the realtime bus."""
    return {
        "ok": True,
        "launches": realtime_bus.recent(limit=limit, max_age_sec=max_age_sec),
        "stats": realtime_bus.stats(),
    }
