"""Trade journal API — open alerts, close trades, EV summary."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

from services.alert_auth import force_auth_ok
from services.alert_invalidation import run_invalidation_cycle, status as inv_status
from services.capital import can_open_trade, enrich_plan_with_size
from services.money_plan import build_money_plan
from services.pending_alerts import (
    get_pending,
    list_pending,
    remove_pending,
)
from services.trade_journal import get_journal
from config import MONEY_OPEN_MANAGE, TELEGRAM_MONEY_MODE

router = APIRouter(tags=["journal"])


def _require_manage(x_admin_key: str | None) -> None:
    if MONEY_OPEN_MANAGE:
        return
    if not force_auth_ok(x_admin_key=x_admin_key, bot_wired=False):
        raise HTTPException(
            401, "X-Admin-Key required (or set MONEY_OPEN_MANAGE=1)"
        )


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
    """Close a trade with exit mcap."""
    _require_manage(x_admin_key)
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


@router.get("/api/journal/pending")
async def journal_pending(limit: int = Query(20, ge=1, le=50)):
    """Alerts waiting for Take / Skip on the Money desk."""
    rows = list_pending(limit=limit)
    return {
        "ok": True,
        "n": len(rows),
        "pending": rows,
        "hint": "Click I took this only when you actually entered — risk slots count confirmed trades only.",
    }


@router.post("/api/journal/take")
async def journal_take(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Confirm you took an alert — opens journal position (counts risk slots)."""
    _require_manage(x_admin_key)
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(400, f"invalid json: {exc}") from exc

    pending_id = str(body.get("id") or body.get("pending_id") or "").strip()
    pending = get_pending(pending_id) if pending_id else None

    mint = str(
        (pending or {}).get("mint")
        or body.get("mint")
        or body.get("tokenAddress")
        or ""
    ).strip()
    if not mint:
        raise HTTPException(400, "mint or pending id required")

    kind = str(
        (pending or {}).get("feed") or body.get("feed") or body.get("kind") or "moon"
    ).lower()
    label = str((pending or {}).get("label") or body.get("label") or "MOON").upper()
    entry = body.get("entry_mcap") or (pending or {}).get("entry_mcap") or 0
    try:
        entry_f = float(entry or 0)
    except (TypeError, ValueError):
        entry_f = 0.0

    token = {
        "tokenAddress": mint,
        "symbol": (pending or {}).get("symbol") or body.get("symbol") or "?",
        "name": (pending or {}).get("name") or body.get("name") or "",
        "mcap_usd": entry_f,
        "moon_label": label if kind == "moon" else "",
        "snipe_label": label if kind == "snipe" else label,
        "elite_label": label if kind == "elite" else "",
        "liquidity_usd": body.get("liquidity_usd"),
    }
    plan = (pending or {}).get("plan") if isinstance((pending or {}).get("plan"), dict) else None
    if not plan or not plan.get("entry_mcap"):
        plan = enrich_plan_with_size(kind, token)
    # Optional user override fill mcap
    if body.get("fill_mcap"):
        try:
            fill = float(body["fill_mcap"])
            if fill > 0:
                plan = enrich_plan_with_size(kind, {**token, "mcap_usd": fill})
        except (TypeError, ValueError):
            pass

    j = get_journal()
    ok_gate, why = can_open_trade(j, kind=kind)
    if not ok_gate:
        raise HTTPException(409, detail=why)

    paper = body.get("paper")
    tid = j.open_from_alert(
        kind,
        token,
        paper=bool(paper) if paper is not None else None,
        alert_sent=True,
        plan=plan,
    )
    if not tid:
        raise HTTPException(400, "could not open trade (missing entry mcap?)")

    if pending_id:
        remove_pending(pending_id)
    else:
        remove_pending(mint=mint)

    return {
        "ok": True,
        "message": f"Position opened — #{tid} counts toward risk slots",
        "id": tid,
        "trade": j.get(tid),
        "plan": plan,
        "desk_gate": why,
    }


@router.post("/api/journal/skip")
async def journal_skip(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Dismiss a pending alert without opening a position."""
    _require_manage(x_admin_key)
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(400, f"invalid json: {exc}") from exc
    pending_id = str(body.get("id") or body.get("pending_id") or "").strip()
    mint = str(body.get("mint") or "").strip()
    if pending_id:
        ok = remove_pending(pending_id)
    elif mint:
        ok = remove_pending(mint=mint)
    else:
        raise HTTPException(400, "id or mint required")
    if not ok:
        raise HTTPException(404, "pending alert not found")
    return {"ok": True, "message": "Skipped — no risk slot used"}


@router.post("/api/journal/open")
async def journal_open_manual(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Manually open a journal row (paper log of a trade you took)."""
    _require_manage(x_admin_key)
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
        "liquidity_usd": body.get("liquidity_usd"),
    }
    j = get_journal()
    ok_gate, why = can_open_trade(j, kind=kind)
    if not ok_gate:
        raise HTTPException(409, detail=why)
    paper = body.get("paper")
    plan = enrich_plan_with_size(kind, token)
    tid = j.open_from_alert(
        kind,
        token,
        paper=bool(paper) if paper is not None else None,
        alert_sent=False,
        plan=plan,
    )
    return {
        "ok": True,
        "id": tid,
        "plan": plan,
        "trade": j.get(tid) if tid else None,
        "gate": why,
    }


@router.post("/api/journal/invalidate")
async def journal_invalidate_now(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Force one invalidation cycle (auth)."""
    if not force_auth_ok(x_admin_key=x_admin_key):
        raise HTTPException(401, "X-Admin-Key required")
    return await run_invalidation_cycle()
