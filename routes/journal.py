"""Trade journal API — open alerts, close trades, EV summary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from services.alert_auth import force_auth_ok
from services.alert_invalidation import run_invalidation_cycle, status as inv_status
from services.money_plan import build_money_plan
from services.trade_journal import get_journal
from config import TELEGRAM_MONEY_MODE

router = APIRouter(tags=["journal"])


@router.get("/api/journal")
async def journal_summary():
    """Public EV summary (no secrets)."""
    j = get_journal()
    return {
        "ok": True,
        "money_mode": TELEGRAM_MONEY_MODE,
        "invalidation": inv_status(),
        **j.summary(),
    }


@router.get("/api/journal/trades")
async def journal_list(
    status: str | None = Query(None, description="open|closed|invalid"),
    limit: int = Query(40, ge=1, le=200),
):
    rows = get_journal().list_trades(status=status, limit=limit)
    # strip huge plan for list view
    out = []
    for r in rows:
        item = dict(r)
        if item.get("plan_json") and len(str(item["plan_json"])) > 40:
            try:
                import json

                item["plan"] = json.loads(item["plan_json"])
            except Exception:
                item["plan"] = None
            del item["plan_json"]
        out.append(item)
    return {"ok": True, "n": len(out), "trades": out}


@router.get("/api/journal/trades/{trade_id}")
async def journal_one(trade_id: int):
    row = get_journal().get(trade_id)
    if not row:
        raise HTTPException(404, "trade not found")
    import json

    try:
        row["plan"] = json.loads(row.get("plan_json") or "{}")
    except Exception:
        row["plan"] = {}
    return {"ok": True, "trade": row}


@router.post("/api/journal/trades/{trade_id}/close")
async def journal_close(
    trade_id: int,
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Close a trade with exit mcap (auth required in production)."""
    if not force_auth_ok(x_admin_key=x_admin_key):
        raise HTTPException(401, "X-Admin-Key required")
    try:
        body = await request.json()
    except Exception:
        body = {}
    exit_mcap = body.get("exit_mcap") if isinstance(body, dict) else None
    try:
        exit_m = float(exit_mcap)
    except (TypeError, ValueError):
        raise HTTPException(400, "exit_mcap required (number)")
    notes = str((body or {}).get("notes") or "")[:500]
    row = get_journal().close_trade(trade_id, exit_mcap=exit_m, notes=notes)
    if not row:
        raise HTTPException(404, "trade not found")
    return {"ok": True, "trade": row, "summary": get_journal().summary()}


@router.post("/api/journal/open")
async def journal_open_manual(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Manually open a journal row (paper log of a trade you took)."""
    if not force_auth_ok(x_admin_key=x_admin_key):
        raise HTTPException(401, "X-Admin-Key required")
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(400, f"invalid json: {exc}") from exc
    mint = str(body.get("mint") or body.get("tokenAddress") or "").strip()
    if not mint:
        raise HTTPException(400, "mint required")
    kind = str(body.get("feed") or body.get("kind") or "moon").lower()
    token = {
        "tokenAddress": mint,
        "symbol": body.get("symbol") or "?",
        "name": body.get("name") or "",
        "mcap_usd": body.get("entry_mcap") or body.get("mcap_usd") or 0,
        "moon_label": body.get("label") or "MOON",
        "snipe_label": body.get("label") or "SNIPE",
    }
    paper = body.get("paper")
    tid = get_journal().open_from_alert(
        kind,
        token,
        paper=bool(paper) if paper is not None else None,
        alert_sent=False,
    )
    return {
        "ok": True,
        "id": tid,
        "plan": build_money_plan(kind, token),
        "trade": get_journal().get(tid) if tid else None,
    }


@router.post("/api/journal/invalidate")
async def journal_invalidate_now(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Force one invalidation cycle (auth)."""
    if not force_auth_ok(x_admin_key=x_admin_key):
        raise HTTPException(401, "X-Admin-Key required")
    return await run_invalidation_cycle()
