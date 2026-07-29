"""Legacy multi-token scan + invest ranking (deprecated APIs)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException

from config import (
    CACHE_TTL,
    DEFAULT_MAX_AGE_MINUTES,
    DEFAULT_SCAN_LIMIT,
    EXCLUDE_GRADUATED_DEFAULT,
)
from services.analyze_token import analyze_token, resolve_pair
from services.dexscreener import DexScreenerClient
from services.discovery import DiscoveryService, is_dead_token, is_early_eligible

_dex = DexScreenerClient()
_discovery = DiscoveryService()
_scan_cache: dict[str, Any] = {"data": None, "ts": 0}


async def scan_one(
    candidate: dict,
    max_age_minutes: float,
    exclude_graduated: bool,
    early_only: bool,
) -> dict | None:
    chain = candidate["chainId"]
    addr = candidate["tokenAddress"]
    try:
        pair = await resolve_pair(chain, addr, candidate)

        if early_only:
            ok, _reason = is_early_eligible(
                candidate, pair, max_age_minutes, exclude_graduated
            )
            if not ok:
                return None
            if is_dead_token(candidate, pair, max_age_minutes):
                return None

        result = await analyze_token(chain, addr, candidate)
        result["source"] = candidate.get("source", "")
        result["sources"] = candidate.get("sources", [])
        result["icon"] = candidate.get("icon", "")
        result["description"] = candidate.get("description", "")

        age_min = result["market"].get("age_minutes")
        invest_sig = (result.get("investSignal") or {}).get("signal", "")
        invest_rank = {
            "STRONG_INVEST": 0,
            "INVEST": 1,
            "WATCH": 2,
            "TAKE_PROFIT": 3,
            "EXIT_NOW": 4,
            "DEV_DUMP_WARNING": 5,
            "AVOID": 6,
        }.get(invest_sig, 7)
        overlap = len(candidate.get("sources", []))
        result["_sort_age"] = age_min if age_min is not None else 9999
        result["_sort_invest"] = invest_rank
        result["_sort_overlap"] = -overlap
        return result
    except HTTPException:
        return None
    except Exception:
        return None


async def run_scan(
    chains: str = "solana",
    limit: int = DEFAULT_SCAN_LIMIT,
    safe_only: bool = True,
    force: bool = False,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
    early_only: bool = True,
    exclude_graduated: bool = EXCLUDE_GRADUATED_DEFAULT,
) -> dict:
    chain_list = [c.strip() for c in chains.split(",") if c.strip()]
    cache_key = (
        f"{','.join(chain_list)}:{limit}:{safe_only}:"
        f"{max_age_minutes}:{early_only}:{exclude_graduated}"
    )

    if (
        not force
        and _scan_cache.get("key") == cache_key
        and _scan_cache.get("data")
        and time.time() - _scan_cache.get("ts", 0) < CACHE_TTL
    ):
        return _scan_cache["data"]

    if early_only:
        candidates = await _discovery.discover_early(
            chain_list,
            limit=limit,
            max_age_minutes=max_age_minutes,
            exclude_graduated=exclude_graduated,
        )
    else:
        candidates = await _dex.discover_tokens(chain_list, limit=limit * 2)

    sem = asyncio.Semaphore(6)

    async def bounded(c: dict) -> dict | None:
        async with sem:
            return await scan_one(
                c, max_age_minutes, exclude_graduated, early_only
            )

    results = await asyncio.gather(
        *[bounded(c) for c in candidates[: limit * 3]]
    )
    tokens = [r for r in results if r is not None]

    if safe_only:
        tokens = [t for t in tokens if t["safety"].get("passed")]

    tokens.sort(
        key=lambda t: (
            t.get("_sort_invest", 7),
            t.get("_sort_overlap", 0),
            t.get("_sort_age", 9999),
        )
    )
    for t in tokens:
        t.pop("_sort_age", None)
        t.pop("_sort_invest", None)
        t.pop("_sort_overlap", None)
    tokens = tokens[:limit]

    response: dict[str, Any] = {
        "count": len(tokens),
        "safe_only": safe_only,
        "early_only": early_only,
        "max_age_minutes": max_age_minutes,
        "exclude_graduated": exclude_graduated,
        "chains": chain_list,
        "tokens": tokens,
        "scanned_at": time.time(),
        "disclaimer": (
            "Not financial advice. Always DYOR. "
            "No tool guarantees safety — scams evolve daily."
        ),
    }

    _scan_cache.update({"key": cache_key, "data": response, "ts": time.time()})
    return response


def build_invest_response(data: dict) -> dict:
    picks = []
    for t in data.get("tokens", []):
        inv = t.get("investSignal") or {}
        picks.append(
            {
                "chainId": t["chainId"],
                "tokenAddress": t["tokenAddress"],
                "name": (t.get("market") or {}).get("baseToken", {}).get("name"),
                "symbol": (t.get("market") or {}).get("baseToken", {}).get("symbol"),
                "signal": inv.get("signal"),
                "confidence": inv.get("confidence"),
                "action": inv.get("action"),
                "summary": inv.get("summary"),
                "reasons": inv.get("reasons", [])[:5],
                "timing": inv.get("timing"),
                "exit_trigger": inv.get("exit_trigger", False),
                "sources": t.get("sources", []),
                "source_badges": inv.get("source_badges", []),
                "market": inv.get("market", {}),
                "trench": inv.get("trench") or t.get("trenchAnalysis"),
                "moonScore": t.get("moonScore"),
                "padre": t.get("padre"),
                "icon": t.get("icon"),
            }
        )

    invest_now = [
        p
        for p in picks
        if p["signal"] in ("STRONG_INVEST", "INVEST")
        and (p.get("trench") or {}).get("passed")
    ]
    exit_now = [
        p
        for p in picks
        if p["signal"] in ("EXIT_NOW", "DEV_DUMP_WARNING", "TAKE_PROFIT")
    ]
    watch = [p for p in picks if p["signal"] == "WATCH"]

    return {
        "scanned_at": data.get("scanned_at"),
        "count": len(picks),
        "invest_now": invest_now,
        "exit_now": exit_now,
        "watch": watch,
        "all": picks,
        "disclaimer": data.get("disclaimer"),
    }


async def run_invest(
    chains: str = "solana",
    limit: int = 15,
    safe_only: bool = True,
    force: bool = False,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
    exclude_graduated: bool = EXCLUDE_GRADUATED_DEFAULT,
) -> dict:
    data = await run_scan(
        chains=chains,
        limit=limit,
        safe_only=safe_only,
        force=force,
        max_age_minutes=max_age_minutes,
        early_only=True,
        exclude_graduated=exclude_graduated,
    )
    return build_invest_response(data)
