"""Lab — Germanus-inspired paste-CA analyze + public scan archive."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.paths import BASE_DIR
from app.security import validate_token_address
from services.analyze_token import analyze_token
from services.cockpit import extract_cockpit
from services.dexscreener import DexScreenerClient
from services.scan_archive import get_archive

router = APIRouter(tags=["lab"])
_dex = DexScreenerClient()


class LabAnalyzeBody(BaseModel):
    mint: str = Field(..., min_length=32, max_length=64)
    force: bool = False


@router.get("/lab")
async def lab_page():
    return FileResponse(BASE_DIR / "static" / "lab.html")


@router.get("/api/lab/archive")
async def lab_archive(
    filter: str = Query("all", description="all|unresolved|under_6h|watchlist"),
    limit: int = Query(60, ge=1, le=200),
):
    """Public archive table (latest scan per mint)."""
    rows = get_archive().list_archive(filter_mode=filter, limit=limit)
    return {
        "ok": True,
        "filter": filter,
        "n": len(rows),
        "philosophy": "facts_with_evidence_no_verdict",
        "rows": rows,
    }


@router.get("/api/lab/token/{mint}")
async def lab_token_dossier(mint: str):
    """Dossier: latest cockpit + history + deltas."""
    mint = validate_token_address("solana", mint)
    arch = get_archive()
    hist = arch.history(mint, limit=15)
    latest = hist[0] if hist else None
    return {
        "ok": True,
        "mint": mint,
        "scan_count": arch.scan_count(mint),
        "latest": latest,
        "history": hist,
        "watchlist": any(w["mint"] == mint for w in arch.watchlist()),
    }


@router.post("/api/lab/analyze")
async def lab_analyze(body: LabAnalyzeBody):
    """Paste CA → deep analyze → archive snapshot (with freshness probe)."""
    mint = validate_token_address("solana", body.mint)
    arch = get_archive()

    # Freshness probe: cheap dex liquidity vs archive
    live_liq = None
    try:
        pairs = await _dex.get_token_pairs("solana", mint)
        pair = _dex.pick_best_pair(pairs) if pairs else None
        if pair:
            live_liq = float((pair.get("liquidity") or {}).get("usd") or 0) or None
    except Exception:
        live_liq = None

    if not body.force:
        fresh, meta = arch.freshness_ok(mint, live_liq)
        if fresh:
            latest = arch.latest(mint)
            return {
                "ok": True,
                "served_from": "archive",
                "freshness": meta,
                "message": (
                    "Liquidity drift <10% — served archive snapshot "
                    "(Germanus-style freshness probe). Use force=true to rescan."
                ),
                "scan": latest,
                "cockpit": (latest or {}).get("cockpit"),
            }

    result = await analyze_token("solana", mint, fast=False)
    if result.get("skipped"):
        return {
            "ok": True,
            "served_from": "live_skipped",
            "result": result,
            "cockpit": extract_cockpit(result),
        }

    # Attach symbol/name from market if missing
    mkt = result.get("market") or {}
    pf = mkt.get("pumpfun") or {}
    base = mkt.get("baseToken") if isinstance(mkt.get("baseToken"), dict) else {}
    if not result.get("symbol"):
        result["symbol"] = pf.get("symbol") or base.get("symbol") or mkt.get("symbol")
    if not result.get("name"):
        result["name"] = pf.get("name") or base.get("name") or mkt.get("name")
    result["analyzedAt"] = result.get("analyzedAt") or time.time()

    stored = arch.store(result, store_raw=False)
    return {
        "ok": True,
        "served_from": "live_scan",
        "freshness": {"forced": body.force, "live_liquidity": live_liq},
        "archive": stored,
        "cockpit": stored.get("cockpit"),
        "delta": stored.get("delta"),
        "result_summary": {
            "mcap_usd": result.get("mcap_usd"),
            "skipped": result.get("skipped"),
            "analyzedAt": result.get("analyzedAt"),
        },
    }


@router.get("/api/lab/analyze/{mint}")
async def lab_analyze_get(
    mint: str,
    force: bool = Query(False),
):
    return await lab_analyze(LabAnalyzeBody(mint=mint, force=force))


class WatchBody(BaseModel):
    mint: str
    symbol: str = ""
    name: str = ""
    notes: str = ""


@router.get("/api/lab/watchlist")
async def lab_watchlist():
    return {"ok": True, "items": get_archive().watchlist()}


@router.post("/api/lab/watchlist")
async def lab_watch_add(body: WatchBody):
    mint = validate_token_address("solana", body.mint)
    return get_archive().star(
        mint, symbol=body.symbol, name=body.name, notes=body.notes
    )


@router.delete("/api/lab/watchlist/{mint}")
async def lab_watch_del(mint: str):
    mint = validate_token_address("solana", mint)
    return get_archive().unstar(mint)
