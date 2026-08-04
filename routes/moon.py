"""Moon product API — /api/moon and outcomes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from config import MAX_AGE_MINUTES_CAP
from services.alert_auth import cron_secret_ok, force_auth_ok
from services.moon_picks import moon_mode
from services.scan_moon import get_moon_outcomes, scan_moon_tokens

router = APIRouter(tags=["moon"])


@router.get("/api/moon")
async def moon_scan(
    limit: int = Query(16, ge=1, le=40),
    max_age_minutes: float = Query(120, ge=5, le=MAX_AGE_MINUTES_CAP),
    force: bool = Query(False),
):
    """Primary feed: high-accuracy moon candidates only.

    ``force`` bypasses short cache; abuse is limited by RateLimitMiddleware.
    """
    return await scan_moon_tokens(
        limit=limit, max_age_minutes=max_age_minutes, force=force
    )


@router.get("/api/moon/outcomes")
async def moon_outcomes_api():
    """Win/dump stats for moon UI recommendations (15m / 1h / 6h tracking)."""
    try:
        outs = get_moon_outcomes()
        return {
            "ok": True,
            "moon_mode": moon_mode(),
            "db_path": outs.db_path(),
            **outs.summary(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/moon/outcomes/export")
async def moon_outcomes_export(
    key: str | None = Query(None),
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    limit: int = Query(8000, ge=1, le=20000),
):
    """Export outcome rows for durable backup (GHA cache / disk restore)."""
    if not (
        cron_secret_ok(x_cron_secret)
        or force_auth_ok(x_admin_key=x_admin_key, key=key, allow_query_cron=True)
    ):
        raise HTTPException(status_code=401, detail="auth required")
    outs = get_moon_outcomes()
    rows = outs.export_rows(limit=limit)
    return {
        "ok": True,
        "moon_mode": moon_mode(),
        "db_path": outs.db_path(),
        "n": len(rows),
        "rows": rows,
    }


@router.post("/api/moon/outcomes/import")
async def moon_outcomes_import(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """Merge exported rows (restore after free-tier redeploy). Auth required."""
    if not (
        cron_secret_ok(x_cron_secret)
        or force_auth_ok(x_admin_key=x_admin_key)
    ):
        raise HTTPException(status_code=401, detail="auth required")
    body: dict[str, Any]
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
    rows = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="body.rows must be a list")
    result = get_moon_outcomes().import_rows(rows, merge=True)
    return {"ok": True, **result, "db_path": get_moon_outcomes().db_path()}
