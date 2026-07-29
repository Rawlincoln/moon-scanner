"""Moon product API — /api/moon and outcomes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from config import MAX_AGE_MINUTES_CAP
from services.scan_moon import get_moon_outcomes, scan_moon_tokens

router = APIRouter(tags=["moon"])


@router.get("/api/moon")
async def moon_scan(
    limit: int = Query(16, ge=1, le=40),
    max_age_minutes: float = Query(120, ge=5, le=MAX_AGE_MINUTES_CAP),
    force: bool = Query(False),
):
    """Primary feed: high-accuracy moon candidates only."""
    return await scan_moon_tokens(
        limit=limit, max_age_minutes=max_age_minutes, force=force
    )


@router.get("/api/moon/outcomes")
async def moon_outcomes_api():
    """Win/dump stats for moon UI recommendations (15m / 1h / 6h tracking)."""
    try:
        return {"ok": True, **get_moon_outcomes().summary()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
