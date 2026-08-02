"""Root, health, chains, and lightweight pump.fun feed."""

from __future__ import annotations

import time

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.deps import learning_memory, padre, pump
from app.paths import BASE_DIR
from config import (
    BACKGROUND_SCAN_INTERVAL_SEC,
    DEFAULT_MAX_AGE_MINUTES,
    EVM_CHAIN_IDS,
    IS_RENDER,
    LEARNING_ACTIVE_CAP_PAID,
    LEARNING_ACTIVE_CAP_PUBLIC,
    MAX_AGE_MINUTES_CAP,
    PADRE_TRADE_URL,
    SUPPORTED_CHAINS,
    rpc_is_paid,
    rpc_provider_label,
)
from services.realtime_bus import realtime_bus
from services.scan_trenches import trenches_health_snapshot

router = APIRouter(tags=["health"])


@router.get("/")
async def root():
    return FileResponse(BASE_DIR / "static" / "index.html")


@router.get("/health")
@router.get("/api/health")
async def health():
    t_health = trenches_health_snapshot()
    learn = {}
    try:
        learn = learning_memory.get_outcomes_summary()
    except Exception:
        pass
    return {
        "ok": True,
        "deploy": "render" if IS_RENDER else "local",
        "trenches_cached": t_health["trenches_cached"],
        "trenches_refreshing": t_health["trenches_refreshing"],
        "cache_age_sec": t_health["cache_age_sec"],
        "background_scan": BACKGROUND_SCAN_INTERVAL_SEC > 0,
        "runner_radar": True,
        "runner_alerts": t_health["runner_alerts"],
        "learning": {
            "tracked": learn.get("total_tracked", 0),
            "active_in_db": learn.get("active", 0),
            "active": learn.get("active", 0),  # legacy alias
            "poll_cap": (
                LEARNING_ACTIVE_CAP_PAID
                if rpc_is_paid()
                else LEARNING_ACTIVE_CAP_PUBLIC
            ),
            "finalized": learn.get("finalized", 0),
        },
        "realtime": realtime_bus.stats().get("feed", {}),
        "rpc": {
            "provider": rpc_provider_label(),
            "paid": rpc_is_paid(),
            "hint": (
                None
                if rpc_is_paid()
                else "Set HELIUS_API_KEY in .env to stop public RPC 429s"
            ),
        },
        "telegram_alerts": _telegram_health(),
    }


def _telegram_health() -> dict:
    try:
        from services.telegram_alerts import status as tg_status

        st = tg_status()
        return {
            "enabled": st.get("enabled"),
            "configured": st.get("configured"),
            "interval_sec": st.get("interval_sec"),
            "feeds": st.get("feeds"),
            "last_sent": (st.get("last_cycle") or {}).get("sent"),
            "last_error": (st.get("last_cycle") or {}).get("error"),
        }
    except Exception:
        return {"configured": False}


@router.get("/api/chains")
async def get_chains():
    return {
        "chains": SUPPORTED_CHAINS,
        "evm": list(EVM_CHAIN_IDS.keys()),
        "padre": {
            "trade_base": f"{PADRE_TRADE_URL}/trade",
            "trenches": padre.trenches_url(),
            "new_pairs": padre.new_pairs_url(),
        },
    }


@router.get("/api/pumpfun/latest")
async def pumpfun_latest(
    limit: int = Query(20, ge=1, le=100),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
):
    """Raw pump.fun feed — newest bonding-curve launches."""
    coins = await pump.get_latest_coins(limit=limit * 2)
    fresh = []
    for coin in coins:
        age = pump.coin_age_minutes(coin)
        if age > max_age_minutes:
            continue
        if coin.get("complete"):
            continue
        fresh.append(
            {
                "mint": coin.get("mint"),
                "name": coin.get("name"),
                "symbol": coin.get("symbol"),
                "age_minutes": round(age, 1),
                "usd_market_cap": coin.get("usd_market_cap"),
                "bonding_progress": round(pump.bonding_progress(coin), 1),
                "reply_count": coin.get("reply_count", 0),
                "url": f"https://pump.fun/coin/{coin.get('mint', '')}",
                "created_timestamp": coin.get("created_timestamp"),
            }
        )
        if len(fresh) >= limit:
            break
    return {"count": len(fresh), "coins": fresh, "fetched_at": time.time()}
