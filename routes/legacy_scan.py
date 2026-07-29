"""Deprecated /api/scan and /api/invest."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.deprecated import mark_deprecated
from config import (
    DEFAULT_MAX_AGE_MINUTES,
    DEFAULT_SCAN_LIMIT,
    EXCLUDE_GRADUATED_DEFAULT,
    MAX_AGE_MINUTES_CAP,
    MAX_SCAN_LIMIT,
)
from services.legacy_scan import run_invest, run_scan

router = APIRouter(tags=["legacy-scan"])


@router.get("/api/scan", deprecated=True)
async def scan_tokens(
    response: Response,
    chains: str = Query("solana"),
    limit: int = Query(DEFAULT_SCAN_LIMIT, ge=5, le=MAX_SCAN_LIMIT),
    safe_only: bool = Query(True),
    force: bool = Query(False),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
    early_only: bool = Query(True),
    exclude_graduated: bool = Query(EXCLUDE_GRADUATED_DEFAULT),
):
    """Deprecated — use GET /api/moon."""
    mark_deprecated(response)
    return await run_scan(
        chains=chains,
        limit=limit,
        safe_only=safe_only,
        force=force,
        max_age_minutes=max_age_minutes,
        early_only=early_only,
        exclude_graduated=exclude_graduated,
    )


@router.get("/api/invest", deprecated=True)
async def invest_recommendations(
    response: Response,
    chains: str = Query("solana"),
    limit: int = Query(15, ge=5, le=MAX_SCAN_LIMIT),
    safe_only: bool = Query(True),
    force: bool = Query(False),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
    exclude_graduated: bool = Query(EXCLUDE_GRADUATED_DEFAULT),
):
    """Deprecated — use GET /api/moon. Ranked invest picks (legacy)."""
    mark_deprecated(response)
    return await run_invest(
        chains=chains,
        limit=limit,
        safe_only=safe_only,
        force=force,
        max_age_minutes=max_age_minutes,
        exclude_graduated=exclude_graduated,
    )
