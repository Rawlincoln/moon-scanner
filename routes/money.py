"""Money desk — complete system API + UI page."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse

from app.paths import BASE_DIR
from config import BANKROLL_USD, MONEY_SYSTEM_ARMED, TELEGRAM_MONEY_MODE
from services.alert_auth import force_auth_ok
from services.capital import can_open_trade, desk_snapshot, enrich_plan_with_size, size_position
from services.position_manager import manage_open_positions, status as pm_status
from services.trade_journal import get_journal

router = APIRouter(tags=["money"])


@router.get("/money")
async def money_page():
    return FileResponse(BASE_DIR / "static" / "money.html")


@router.get("/api/money")
async def money_desk():
    """Full money desk snapshot (public, no secrets)."""
    j = get_journal()
    desk = desk_snapshot(j)
    return {
        "ok": True,
        **desk,
        "positions": pm_status(),
        "journal": j.summary(),
    }


@router.get("/api/money/size")
async def money_size_preview(
    entry_mcap: float = 15000,
    stop_pct: float = 18.0,
    bankroll: float | None = None,
    risk_pct: float | None = None,
):
    """Preview position size for a given entry/stop."""
    return {
        "ok": True,
        **size_position(
            entry_mcap=entry_mcap,
            stop_pct=stop_pct / 100.0 if stop_pct > 1 else stop_pct,
            bankroll=bankroll,
            risk_pct=risk_pct,
        ),
    }


@router.post("/api/money/plan")
async def money_plan_from_token(request: Request):
    """Build sized plan from a token payload {kind, mcap_usd, ...}."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(400, f"invalid json: {exc}") from exc
    kind = str(body.get("kind") or body.get("feed") or "moon")
    token = body.get("token") if isinstance(body.get("token"), dict) else body
    plan = enrich_plan_with_size(kind, token)
    ok, why = can_open_trade(get_journal(), kind=kind)
    return {"ok": True, "plan": plan, "can_open": ok, "can_open_reason": why}


@router.post("/api/money/manage")
async def money_manage_now(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    if not force_auth_ok(x_admin_key=x_admin_key):
        raise HTTPException(401, "X-Admin-Key required")
    return await manage_open_positions()


@router.post("/api/money/close")
async def money_close(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Close trade by id with exit_mcap."""
    if not force_auth_ok(x_admin_key=x_admin_key):
        raise HTTPException(401, "X-Admin-Key required")
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(400, f"invalid json: {exc}") from exc
    tid = body.get("id") or body.get("trade_id")
    exit_mcap = body.get("exit_mcap")
    try:
        tid_i = int(tid)
        exit_f = float(exit_mcap)
    except (TypeError, ValueError):
        raise HTTPException(400, "id and exit_mcap required")
    row = get_journal().close_trade(
        tid_i, exit_mcap=exit_f, notes=str(body.get("notes") or "")[:500]
    )
    if not row:
        raise HTTPException(404, "trade not found")
    return {"ok": True, "trade": row, "desk": desk_snapshot(get_journal())}


@router.get("/api/money/playbook")
async def money_playbook():
    desk = desk_snapshot(get_journal())
    return {
        "ok": True,
        "armed": MONEY_SYSTEM_ARMED,
        "money_mode": TELEGRAM_MONEY_MODE,
        "bankroll_usd": BANKROLL_USD,
        "playbook": desk.get("playbook"),
        "rules": {
            "entries": "MOON or SNIPE only",
            "size": "risk % of bankroll to stop distance",
            "exits": "STOP hard · TP1 scale 50% · TP2 close · trail BE after TP1",
            "session": "max open, max/day, daily −R stop, profit lock",
            "paper": "until E[R] > 0 over ≥20 closed trades",
        },
    }
