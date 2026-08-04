"""Lab — fast multi-source cockpit (Germanus alternative)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.paths import BASE_DIR
from app.security import validate_token_address
from services.lab_scan import lab_analyze_smart
from services.scan_archive import get_archive

router = APIRouter(tags=["lab"])


class LabAnalyzeBody(BaseModel):
    mint: str = Field(..., min_length=32, max_length=64)
    force: bool = False
    # fast = parallel multi-source ~1–3s; deep = +full rugcheck ~4–6s
    mode: str = Field(default="fast", pattern="^(fast|deep)$")


@router.get("/lab")
async def lab_page():
    return FileResponse(BASE_DIR / "static" / "lab.html")


@router.get("/api/lab/archive")
async def lab_archive(
    filter: str = Query("all", description="all|unresolved|under_6h|watchlist"),
    limit: int = Query(60, ge=1, le=200),
):
    rows = get_archive().list_archive(filter_mode=filter, limit=limit)
    return {
        "ok": True,
        "filter": filter,
        "n": len(rows),
        "philosophy": "facts_with_evidence_no_verdict",
        "engine": "lab_fast_multi_source",
        "rows": rows,
    }


@router.get("/api/lab/token/{mint}")
async def lab_token_dossier(mint: str):
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
    """Paste CA → fast multi-source cockpit (default) or deep scan."""
    mint = validate_token_address("solana", body.mint)
    return await lab_analyze_smart(
        mint, force=body.force, mode=body.mode or "fast"
    )


@router.get("/api/lab/analyze/{mint}")
async def lab_analyze_get(
    mint: str,
    force: bool = Query(False),
    mode: str = Query("fast", pattern="^(fast|deep)$"),
):
    mint = validate_token_address("solana", mint)
    return await lab_analyze_smart(mint, force=force, mode=mode)


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
