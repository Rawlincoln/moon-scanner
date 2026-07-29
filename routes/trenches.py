"""Legacy trenches / runner / sixk API routes."""

from __future__ import annotations

import time

from fastapi import APIRouter, Query, Response

from config import (
    DEFAULT_MAX_AGE_MINUTES,
    DUMP_HIDE_FRAC,
    MAX_AGE_MINUTES_CAP,
    SIXK_RADAR_MAX_USD,
    SIXK_RADAR_MIN_USD,
)
from services.avoid_filters import BLOCKED_MINTS
from services.padre_feed import PadreFeedClient
from services.runner_radar import is_crashed_runner
from services.scan_trenches import (
    analyze_trenches,
    fetch_trenches_feed,
    preview_from_candidate,
    refresh_runner_alerts,
)

router = APIRouter(tags=["trenches"])

_DEPRECATED = (
    "Prefer GET /api/moon (moon-only capital-protection feed). "
    "This endpoint is legacy and may be removed."
)
_padre_feed = PadreFeedClient()


def _mark_deprecated(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Sat, 01 Nov 2026 00:00:00 GMT"
    response.headers["Link"] = '</api/moon>; rel="successor-version"'
    response.headers["X-Moon-Scanner-Deprecated"] = _DEPRECATED


@router.get("/api/padre/trenches/feed", deprecated=True)
async def padre_trenches_feed(
    response: Response,
    per_column: int = Query(8, ge=5, le=30),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
):
    """Deprecated — use GET /api/moon. Fast trenches preview (legacy)."""
    _mark_deprecated(response)
    return await fetch_trenches_feed(
        per_column=per_column,
        max_age_minutes=max_age_minutes,
    )


@router.get("/api/runner-radar", deprecated=True)
async def runner_radar(response: Response):
    """Deprecated — use GET /api/moon. Sticky multi-stage alerts (legacy)."""
    _mark_deprecated(response)
    alerts = await refresh_runner_alerts()
    new_only = [a for a in alerts if a.get("is_new_alert")]
    return {
        "ok": True,
        "alerts": alerts[:20],
        "new_alerts": new_only[:10],
        "count": len(alerts),
        "new_count": len(new_only),
        "ts": time.time(),
        "hint": (
            "Multi-stage: early structure · mid climb · near migration · post-migration. "
            "Enable browser notifications in the UI."
        ),
    }


@router.get("/api/padre/sixk")
async def sixk_radar_fast(
    limit: int = Query(24, ge=8, le=50),
    max_age_minutes: float = Query(40, ge=5, le=MAX_AGE_MINUTES_CAP),
):
    """Ultra-fast $2k–$9k climber list (no RugCheck) — for early $6k entry."""
    cands = await _padre_feed.fetch_sixk_radar(
        limit=limit, max_age_minutes=max_age_minutes
    )
    tokens = []
    for c in cands:
        t = preview_from_candidate("sixk_radar", c)
        mint = t.get("tokenAddress") or ""
        if mint in BLOCKED_MINTS:
            continue
        crashed, _ = is_crashed_runner(t)
        if crashed:
            continue
        mcap = float(t.get("mcap_usd") or 0)
        ath = float(t.get("ath_mcap") or t.get("ath_market_cap") or 0)
        if ath >= 2_000 and mcap > 0 and mcap < ath * DUMP_HIDE_FRAC:
            continue
        tokens.append(t)
    sweet = [t for t in tokens if t.get("entrySweet")]
    return {
        "source": "sixk_radar",
        "scanned_at": time.time(),
        "tokens": tokens,
        "sweet_zone": sweet,
        "counts": {
            "total": len(tokens),
            "sweet": len(sweet),
            "band": f"${SIXK_RADAR_MIN_USD:,.0f}–${SIXK_RADAR_MAX_USD:,.0f}",
        },
        "hint": (
            "These are live $2k–$9k climbers from pump.fun last-trade feed. "
            "Full safety runs on Scan; prioritize SWEET ZONE (~$3.5k–$7.5k)."
        ),
    }


@router.get("/api/padre/trenches", deprecated=True)
async def padre_trenches_scan(
    response: Response,
    per_column: int = Query(8, ge=5, le=30),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
    force: bool = Query(False),
):
    """Deprecated — use GET /api/moon. Full trenches analysis (legacy)."""
    _mark_deprecated(response)
    return await analyze_trenches(
        per_column=per_column,
        max_age_minutes=max_age_minutes,
        force=force,
    )
