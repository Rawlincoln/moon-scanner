"""Token analysis API — /api/analyze* and /api/checkers/*."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.security import validate_token_address
from config import SUPPORTED_CHAINS
from services.analyze_token import analyze_token
from services.safety_report import build_safety_report

router = APIRouter(tags=["analyze"])


class AnalyzeRequest(BaseModel):
    chain_id: str = Field(..., min_length=3, max_length=32)
    token_address: str = Field(..., min_length=8, max_length=64)


@router.get("/api/checkers/{chain_id}/{token_address}")
async def get_checker_report(chain_id: str, token_address: str):
    """Standalone multi-checker security report (RugCheck, Padre, DexScreener, etc.)."""
    chain = chain_id.lower().strip()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    addr = validate_token_address(chain, token_address)
    result = await analyze_token(chain, addr)
    return {
        "chainId": chain,
        "tokenAddress": addr,
        "checkerHub": result.get("checkerHub"),
        "safety": result.get("safety"),
        "safetyReport": build_safety_report(
            result.get("safety") or {},
            {
                "pumpfun": (result.get("market") or {}).get("pumpfun"),
                "volume": (result.get("market") or {}).get("volume"),
                "txns": {
                    "m5": (result.get("market") or {}).get("txns_m5"),
                    "h1": (result.get("market") or {}).get("txns_h1"),
                },
                "is_pumpfun_synthetic": (result.get("market") or {}).get(
                    "is_pumpfun_synthetic"
                ),
                "url": (result.get("market") or {}).get("url"),
            },
            checker_hub=result.get("checkerHub"),
        ),
        "links": (result.get("checkerHub") or {}).get("links"),
        "analyzed_at": result.get("analyzedAt"),
    }


@router.post("/api/analyze")
async def analyze_token_post(req: AnalyzeRequest):
    chain = req.chain_id.lower().strip()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    addr = validate_token_address(chain, req.token_address)
    return await analyze_token(chain, addr)


@router.get("/api/analyze/{chain_id}/{token_address}")
async def analyze_token_get(chain_id: str, token_address: str):
    chain = chain_id.lower().strip()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    addr = validate_token_address(chain, token_address)
    return await analyze_token(chain, addr)
