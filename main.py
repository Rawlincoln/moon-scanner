"""Moon Scanner — Multi-chain token safety & signal analyzer."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import (
    BACKGROUND_SCAN_INTERVAL_SEC,
    BACKGROUND_SCAN_PER_COLUMN,
    CACHE_TTL,
    DEFAULT_MAX_AGE_MINUTES,
    DEFAULT_SCAN_LIMIT,
    EVM_CHAIN_IDS,
    EXCLUDE_GRADUATED_DEFAULT,
    FAST_SCAN_SKIP_DEX_ORDERS,
    IS_PRODUCTION,
    IS_RENDER,
    MAX_AGE_MINUTES_CAP,
    MAX_SCAN_LIMIT,
    PADRE_TRADE_URL,
    SCAN_MCAP_FOCUS_MAX_USD,
    SCAN_MCAP_MAX_USD,
    SIXK_ENTRY_SWEET_MAX,
    SIXK_ENTRY_SWEET_MIN,
    SIXK_RADAR_MAX_USD,
    SIXK_RADAR_MIN_USD,
    SUPPORTED_CHAINS,
    TRENCHES_CACHE_TTL,
    TRENCHES_CONCURRENCY,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("moon-scanner")
from services.dexscreener import DexScreenerClient
from services.discovery import DiscoveryService, is_dead_token, is_early_eligible
from services.evm_analyzer import EVMAnalyzer
from services.padre import PadreClient
from services.pumpfun import PumpFunClient
from services.scorer import compute_moon_score
from services.signals import (
    generate_entry_signal,
    generate_exit_signal,
    generate_invest_signal,
)
from services.padre_feed import PadreFeedClient
from services.safety_report import build_safety_report
from services.checker_hub import run_checker_hub
from services.alpha_setup import analyze_alpha_setup
from services.avoid_filters import analyze_avoid_flags
from services.smart_money import (
    analyze_smart_money,
    analyze_smart_money_async,
)
from services.social_signals import analyze_social_narrative
from services.solana_analyzer import SolanaAnalyzer
from services.learning.memory import LearningMemory
from services.learning.tracker import LearningEngine

_learning_memory = LearningMemory(BASE_DIR / "data" / "learning.db")
_learning = LearningEngine(_learning_memory)


async def _background_trenches_warm() -> None:
    """Keep $6k radar warm so we don't miss climbers by minutes."""
    await asyncio.sleep(8)
    while True:
        try:
            logger.info("Background $6k radar refresh starting")
            await _analyze_trenches(
                per_column=BACKGROUND_SCAN_PER_COLUMN,
                max_age_minutes=max(DEFAULT_MAX_AGE_MINUTES, 30),
                force=True,
            )
            logger.info("Background $6k radar refresh done")
        except Exception as exc:
            logger.warning("Background $6k radar refresh failed: %s", exc)
        await asyncio.sleep(BACKGROUND_SCAN_INTERVAL_SEC)


async def _background_learning_loop() -> None:
    """Poll tracked tokens; record mcap/dev-dump/crash; update learned model."""
    await asyncio.sleep(12)
    while True:
        try:
            n = await _learning.poll_active()
            if n:
                logger.info("Learning poll updated %s active tokens", n)
        except Exception as exc:
            logger.warning("Learning poll failed: %s", exc)
        await asyncio.sleep(35)


@asynccontextmanager
async def lifespan(app: FastAPI):
    port = os.getenv("PORT", "8765")
    logger.info("Moon Scanner starting on port %s (deploy=%s)", port, "render" if IS_RENDER else "local")
    try:
        seeded = _learning.seed_known_examples()
        if seeded:
            logger.info("Learning seeded %s historical examples", seeded)
    except Exception as exc:
        logger.warning("Learning seed failed: %s", exc)
    tasks: list[asyncio.Task] = []
    if BACKGROUND_SCAN_INTERVAL_SEC > 0 and BACKGROUND_SCAN_PER_COLUMN > 0:
        tasks.append(asyncio.create_task(_background_trenches_warm()))
    tasks.append(asyncio.create_task(_background_learning_loop()))
    yield
    logger.info("Moon Scanner shutting down")
    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Moon Scanner",
    description="Identify safe early tokens on EVM & Solana with entry/exit signals",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

dex = DexScreenerClient()
pump = PumpFunClient()
padre = PadreClient()
discovery = DiscoveryService()
padre_feed = PadreFeedClient()
evm = EVMAnalyzer()
sol = SolanaAnalyzer()

_scan_cache: dict[str, Any] = {"data": None, "ts": 0}
_trenches_cache: dict[str, Any] = {"key": None, "data": None, "ts": 0}
_trenches_lock = asyncio.Lock()
_trenches_refreshing = False


class AnalyzeRequest(BaseModel):
    chain_id: str
    token_address: str


class ScanRequest(BaseModel):
    chains: list[str] = Field(default_factory=lambda: ["solana"])
    limit: int = DEFAULT_SCAN_LIMIT
    safe_only: bool = True
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES
    early_only: bool = True


def _format_pair_summary(pair: dict) -> dict:
    txns_m5 = (pair.get("txns") or {}).get("m5") or {}
    txns_h1 = (pair.get("txns") or {}).get("h1") or {}
    txns_h24 = (pair.get("txns") or {}).get("h24") or {}
    pump_coin = pair.get("pumpfun") or {}

    age_hours = None
    age_minutes = None
    if pair.get("pairCreatedAt"):
        age_ms = time.time() * 1000 - pair["pairCreatedAt"]
        age_hours = round(age_ms / 3_600_000, 2)
        age_minutes = round(age_ms / 60_000, 1)
    elif pump_coin.get("created_timestamp"):
        age_ms = time.time() * 1000 - pump_coin["created_timestamp"]
        age_hours = round(age_ms / 3_600_000, 2)
        age_minutes = round(age_ms / 60_000, 1)

    summary = {
        "pairAddress": pair.get("pairAddress"),
        "dexId": pair.get("dexId"),
        "url": pair.get("url"),
        "priceUsd": pair.get("priceUsd"),
        "priceChange": pair.get("priceChange"),
        "volume": pair.get("volume"),
        "liquidity": pair.get("liquidity"),
        "marketCap": pair.get("marketCap"),
        "fdv": pair.get("fdv"),
        "pairCreatedAt": pair.get("pairCreatedAt"),
        "baseToken": pair.get("baseToken"),
        "quoteToken": pair.get("quoteToken"),
        "txns_m5": txns_m5,
        "txns_h1": txns_h1,
        "txns_h24": txns_h24,
        "is_pumpfun_synthetic": bool(pair.get("is_pumpfun_synthetic")),
        "age_hours": age_hours,
        "age_minutes": age_minutes,
        "is_pumpfun": bool(pump_coin),
    }

    if pump_coin:
        summary["pumpfun"] = {
            "bonding_progress": round(PumpFunClient.bonding_progress(pump_coin), 1),
            "usd_market_cap": pump_coin.get("usd_market_cap"),
            "reply_count": pump_coin.get("reply_count", 0),
            "complete": pump_coin.get("complete", False),
            "creator": pump_coin.get("creator"),
            "pump_url": f"https://pump.fun/coin/{pump_coin.get('mint', '')}",
        }

    return summary


def _padre_links(chain_id: str, token_address: str) -> dict[str, str]:
    return {
        "trade": padre.trade_url(chain_id, token_address),
        "trenches": padre.trenches_url(),
        "new_pairs": padre.new_pairs_url(),
    }


async def _resolve_pair(
    chain_id: str, token_address: str, candidate: dict | None = None
) -> dict:
    cand = candidate or {}
    pump_coin = cand.get("pumpfun")
    if not pump_coin and chain_id == "solana":
        pump_coin = await pump.get_coin(token_address)

    dex_pair = cand.get("_dex_pair")
    if not dex_pair:
        pairs = await dex.get_token_pairs(chain_id, token_address)
        dex_pair = dex.pick_best_pair(pairs)

    if pump_coin:
        return pump.to_market_pair(pump_coin, dex_pair)

    if dex_pair:
        return dex_pair

    raise HTTPException(404, f"No market data for {token_address}")


def _token_mcap(pair: dict, candidate: dict | None = None) -> float:
    pump = (pair or {}).get("pumpfun") or (candidate or {}).get("pumpfun") or {}
    try:
        return float(
            pump.get("usd_market_cap")
            or (pair or {}).get("marketCap")
            or (pair or {}).get("fdv")
            or (candidate or {}).get("_mcap")
            or (candidate or {}).get("marketCap")
            or 0
        )
    except (TypeError, ValueError):
        return 0.0


async def _analyze_token(
    chain_id: str,
    token_address: str,
    candidate: dict | None = None,
    *,
    fast: bool = False,
) -> dict:
    # Early drop: skip heavy analysis if feed already shows mcap too high
    if candidate:
        pre_mcap = _token_mcap({}, candidate)
        if pre_mcap > SCAN_MCAP_MAX_USD:
            return {
                "chainId": chain_id,
                "tokenAddress": token_address,
                "skipped": True,
                "skipReason": f"mcap ${pre_mcap:,.0f} > ${SCAN_MCAP_MAX_USD:,.0f}",
                "mcap_usd": pre_mcap,
                "analyzedAt": time.time(),
            }

    # Parallel market resolve + Padre audit
    pair, padre_audit = await asyncio.gather(
        _resolve_pair(chain_id, token_address, candidate),
        padre.get_token_audit(chain_id, token_address),
    )
    pump_coin = pair.get("pumpfun") or (candidate or {}).get("pumpfun")
    mcap_for_sm = _token_mcap(pair, candidate)

    # Late drop after live mcap resolve
    if mcap_for_sm > SCAN_MCAP_MAX_USD:
        return {
            "chainId": chain_id,
            "tokenAddress": token_address,
            "skipped": True,
            "skipReason": f"live mcap ${mcap_for_sm:,.0f} > ${SCAN_MCAP_MAX_USD:,.0f}",
            "mcap_usd": mcap_for_sm,
            "market": _format_pair_summary(pair),
            "analyzedAt": time.time(),
        }

    if chain_id == "solana":
        safety = await sol.analyze(
            token_address,
            pump_coin=pump_coin,
            padre_audit=padre_audit,
        )
    elif chain_id in EVM_CHAIN_IDS:
        safety = await evm.analyze(
            chain_id, token_address, pair.get("pairAddress")
        )
        if padre_audit:
            from services.padre import PadreClient

            parsed = PadreClient.parse_audit(padre_audit)
            safety["padre"] = parsed
            if parsed.get("honeypot"):
                safety["is_honeypot"] = True
                safety["passed"] = False
            safety.setdefault("issues", []).extend(parsed.get("issues", []))
    else:
        safety = {
            "passed": False,
            "issues": [f"Unsupported chain: {chain_id}"],
            "type": "unknown",
        }

    moon = compute_moon_score(safety, pair, early_mode=True)
    entry = generate_entry_signal(safety, pair, moon, early_mode=True)
    exit_sig = generate_exit_signal(safety, pair, moon)

    sources = (candidate or {}).get("sources") or []
    if not sources and (candidate or {}).get("source"):
        sources = [
            s.strip()
            for s in (candidate or {})["source"].split(",")
            if s.strip()
        ]
    invest = generate_invest_signal(
        safety, pair, moon, sources=sources, early_mode=True
    )
    trench = invest.get("trench")

    base_token = pair.get("baseToken") or {}
    social = analyze_social_narrative(
        pump_coin=pump_coin,
        name=base_token.get("name", ""),
        symbol=base_token.get("symbol", ""),
        description=(candidate or {}).get("description")
        or (pump_coin or {}).get("description", ""),
        links=(candidate or {}).get("links")
        or ((pump_coin and PumpFunClient.to_candidate(pump_coin).get("links")) or None),
    )

    # Re-run avoid with full pump ATH + dex sell pressure (catches Baby Corn P&Ds)
    avoid = analyze_avoid_flags(
        safety, pump_coin, mint=token_address, pair=pair
    )
    safety["avoid"] = avoid
    if avoid.get("avoid"):
        safety["passed"] = False
        for reason in avoid.get("reasons") or []:
            tag = f"AVOID: {reason}"
            if tag not in (safety.get("issues") or []):
                safety.setdefault("issues", []).append(tag)

    checker_hub = run_checker_hub(
        chain_id, token_address, safety, pair=pair, padre_audit=padre_audit
    )

    # Fast bulk scan: skip DexScreener order round-trip
    if fast or FAST_SCAN_SKIP_DEX_ORDERS:
        smart_money = analyze_smart_money(
            safety, pair=pair, mcap_usd=mcap_for_sm, dex_orders=None
        )
    else:
        smart_money = await analyze_smart_money_async(
            safety,
            pair,
            chain_id,
            token_address,
            mcap_usd=mcap_for_sm,
        )

    alpha = analyze_alpha_setup(
        safety=safety,
        pair=pair,
        pump=pump_coin,
        social=social,
        smart_money=smart_money,
        mcap_usd=mcap_for_sm,
    )
    # Boost invest signal when moon-setup matches KIWI-style early profile
    if alpha.get("is_alpha") and invest.get("signal") in (
        "WATCH",
        "INVEST",
        "STRONG_INVEST",
        None,
        "",
    ):
        if not (safety.get("avoid") or {}).get("avoid"):
            invest = dict(invest)
            invest["signal"] = alpha.get("signal") or invest.get("signal") or "INVEST"
            invest["confidence"] = max(
                int(invest.get("confidence") or 0),
                int(alpha.get("confidence") or 0),
            )
            invest["summary"] = alpha.get("summary") or invest.get("summary")
            reasons = list(invest.get("reasons") or [])
            for r in (alpha.get("reasons") or [])[:4]:
                if r not in reasons:
                    reasons.insert(0, r)
            invest["reasons"] = reasons[:8]
            invest["alpha_boost"] = True

    links = _padre_links(chain_id, token_address)

    result = {
        "chainId": chain_id,
        "tokenAddress": token_address,
        "safety": safety,
        "market": _format_pair_summary(pair),
        "moonScore": moon,
        "entrySignal": entry,
        "exitSignal": exit_sig,
        "investSignal": invest,
        "trenchAnalysis": trench,
        "socialSignals": social,
        "smartMoney": smart_money,
        "alphaSetup": alpha,
        "checkerHub": checker_hub,
        "padre": links,
        "analyzedAt": time.time(),
        "source": (candidate or {}).get("source", ""),
        "sources": sources,
        "icon": (candidate or {}).get("icon", ""),
        "description": (candidate or {}).get("description", ""),
        "mcap_usd": mcap_for_sm,
    }
    try:
        trade_plan = _learning.observe_analysis(result)
        if trade_plan:
            result["tradePlan"] = trade_plan
            # Align invest signal with learned plan when strong
            if trade_plan.get("action") == "SKIP":
                invest = dict(invest)
                invest["signal"] = "AVOID"
                invest["confidence"] = max(
                    int(invest.get("confidence") or 0),
                    int(trade_plan.get("confidence") or 0),
                )
                invest["summary"] = trade_plan.get("summary") or invest.get("summary")
                result["investSignal"] = invest
            elif (
                trade_plan.get("action") == "ENTER"
                and invest.get("signal") in ("WATCH", "INVEST", "STRONG_INVEST", None, "")
                and not (safety.get("avoid") or {}).get("avoid")
            ):
                invest = dict(invest)
                if invest.get("signal") not in ("STRONG_INVEST", "INVEST"):
                    invest["signal"] = "INVEST"
                invest["confidence"] = max(
                    int(invest.get("confidence") or 0),
                    int(trade_plan.get("confidence") or 0),
                )
                invest["summary"] = trade_plan.get("summary") or invest.get("summary")
                invest["learned"] = True
                result["investSignal"] = invest
    except Exception as exc:
        logger.debug("learning observe failed: %s", exc)
    return result


async def _scan_one(
    candidate: dict,
    max_age_minutes: float,
    exclude_graduated: bool,
    early_only: bool,
) -> dict | None:
    chain = candidate["chainId"]
    addr = candidate["tokenAddress"]
    try:
        pair = await _resolve_pair(chain, addr, candidate)

        if early_only:
            ok, _reason = is_early_eligible(
                candidate, pair, max_age_minutes, exclude_graduated
            )
            if not ok:
                return None
            if is_dead_token(candidate, pair, max_age_minutes):
                return None

        result = await _analyze_token(chain, addr, candidate)
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


@app.get("/")
async def root():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
@app.get("/api/health")
async def health():
    cache_age = (
        time.time() - _trenches_cache.get("ts", 0)
        if _trenches_cache.get("data")
        else None
    )
    learn = {}
    try:
        learn = _learning_memory.get_outcomes_summary()
    except Exception:
        pass
    return {
        "ok": True,
        "deploy": "render" if IS_RENDER else "local",
        "trenches_cached": bool(_trenches_cache.get("data")),
        "trenches_refreshing": _trenches_refreshing,
        "cache_age_sec": round(cache_age, 1) if cache_age is not None else None,
        "background_scan": BACKGROUND_SCAN_INTERVAL_SEC > 0,
        "learning": {
            "tracked": learn.get("total_tracked", 0),
            "active": learn.get("active", 0),
            "finalized": learn.get("finalized", 0),
        },
    }


@app.get("/api/chains")
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


@app.get("/api/pumpfun/latest")
async def pumpfun_latest(
    limit: int = Query(20, ge=1, le=100),
    max_age_minutes: float = Query(DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP),
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


async def _run_scan(
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
        candidates = await discovery.discover_early(
            chain_list,
            limit=limit,
            max_age_minutes=max_age_minutes,
            exclude_graduated=exclude_graduated,
        )
    else:
        candidates = await dex.discover_tokens(chain_list, limit=limit * 2)

    sem = asyncio.Semaphore(6)

    async def bounded(c: dict) -> dict | None:
        async with sem:
            return await _scan_one(
                c, max_age_minutes, exclude_graduated, early_only
            )

    results = await asyncio.gather(
        *[bounded(c) for c in candidates[: limit * 3]]
    )
    tokens = [r for r in results if r is not None]

    if safe_only:
        tokens = [t for t in tokens if t["safety"].get("passed")]

    # Best invest picks first, then multi-source overlap, then newest
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

    response = {
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


@app.get("/api/scan")
async def scan_tokens(
    chains: str = Query("solana"),
    limit: int = Query(DEFAULT_SCAN_LIMIT, ge=5, le=MAX_SCAN_LIMIT),
    safe_only: bool = Query(True),
    force: bool = Query(False),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
    early_only: bool = Query(True),
    exclude_graduated: bool = Query(EXCLUDE_GRADUATED_DEFAULT),
):
    return await _run_scan(
        chains=chains,
        limit=limit,
        safe_only=safe_only,
        force=force,
        max_age_minutes=max_age_minutes,
        early_only=early_only,
        exclude_graduated=exclude_graduated,
    )


def _build_invest_response(data: dict) -> dict:
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
        p for p in picks
        if p["signal"] in ("STRONG_INVEST", "INVEST")
        and (p.get("trench") or {}).get("passed")
    ]
    exit_now = [
        p for p in picks
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


async def _run_invest(
    chains: str = "solana",
    limit: int = 15,
    safe_only: bool = True,
    force: bool = False,
    max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
    exclude_graduated: bool = EXCLUDE_GRADUATED_DEFAULT,
) -> dict:
    data = await _run_scan(
        chains=chains,
        limit=limit,
        safe_only=safe_only,
        force=force,
        max_age_minutes=max_age_minutes,
        early_only=True,
        exclude_graduated=exclude_graduated,
    )
    return _build_invest_response(data)


async def _analyze_trenches(
    per_column: int = 15,
    max_age_minutes: float = 30.0,
    force: bool = False,
) -> dict:
    global _trenches_refreshing
    cache_key = f"{per_column}:{max_age_minutes}"
    cached = _trenches_cache.get("data")
    cache_age = time.time() - _trenches_cache.get("ts", 0)
    if (
        not force
        and _trenches_cache.get("key") == cache_key
        and cached
        and cache_age < TRENCHES_CACHE_TTL
    ):
        return cached

    if _trenches_refreshing and cached:
        stale = dict(cached)
        stale["stale"] = True
        stale["message"] = "Serving cached results while refresh runs…"
        return stale

    async with _trenches_lock:
        if (
            not force
            and _trenches_cache.get("key") == cache_key
            and _trenches_cache.get("data")
            and time.time() - _trenches_cache.get("ts", 0) < TRENCHES_CACHE_TTL
        ):
            return _trenches_cache["data"]

        if _trenches_refreshing and _trenches_cache.get("data"):
            stale = dict(_trenches_cache["data"])
            stale["stale"] = True
            return stale

        _trenches_refreshing = True
        try:
            return await _run_trenches_scan(
                per_column=per_column,
                max_age_minutes=max_age_minutes,
                cache_key=cache_key,
            )
        finally:
            _trenches_refreshing = False


def _preview_from_candidate(column: str, cand: dict) -> dict:
    """Lightweight token card before RugCheck analysis completes."""
    pf = cand.get("pumpfun") or {}
    mint = cand.get("tokenAddress", "")
    mcap = float(pf.get("usd_market_cap") or cand.get("_mcap") or 0)
    sixk = (
        column == "sixk_radar"
        or cand.get("_sixk_radar")
        or (SIXK_RADAR_MIN_USD <= mcap <= SIXK_RADAR_MAX_USD)
    )
    sweet = SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX
    verdict = "Analyzing RugCheck + Padre…"
    if sixk:
        verdict = (
            f"$6K RADAR — mcap ${mcap:,.0f} "
            f"{'(SWEET ENTRY ZONE)' if sweet else '— full check running…'}"
        )
    return {
        "column": column,
        "chainId": "solana",
        "tokenAddress": mint,
        "name": pf.get("name") or cand.get("name") or "Unknown",
        "symbol": pf.get("symbol") or cand.get("symbol") or "?",
        "icon": cand.get("icon") or pf.get("image_uri"),
        "mcap_usd": mcap,
        "age_minutes": cand.get("_age_minutes") or cand.get("age_minutes"),
        "bonding_progress": pf.get("bonding_progress"),
        "safetyTier": "SCANNING",
        "safetyScore": 0,
        "safetyReport": {
            "verdict": verdict,
            "bundle": {"bundled": False},
            "snipers": {},
        },
        "pump_url": f"https://pump.fun/coin/{mint}" if mint else None,
        "preview": True,
        "sixkRadar": sixk,
        "entrySweet": sweet,
        "quickAlpha": cand.get("_quick_alpha", 0),
    }


async def _fetch_trenches_feed(
    per_column: int,
    max_age_minutes: float,
) -> dict:
    columns = await padre_feed.fetch_trenches_columns(
        per_column=per_column, max_age_minutes=max_age_minutes
    )
    preview_columns = {
        col: [_preview_from_candidate(col, c) for c in cands]
        for col, cands in columns.items()
    }
    total = sum(len(v) for v in preview_columns.values())
    sixk_n = len(preview_columns.get("sixk_radar") or [])
    return {
        "source": "padre_trenches_feed",
        "preview": True,
        "scanned_at": time.time(),
        "columns": preview_columns,
        "counts": {
            "sixk_radar": sixk_n,
            "new": len(preview_columns.get("new") or []),
            "almost_bonded": len(preview_columns.get("almost_bonded") or []),
            "recently_bonded": len(preview_columns.get("recently_bonded") or []),
            "total": total,
        },
    }


async def _run_trenches_scan(
    per_column: int,
    max_age_minutes: float,
    cache_key: str,
) -> dict:
    columns = await padre_feed.fetch_trenches_columns(
        per_column=per_column, max_age_minutes=max_age_minutes
    )

    sem = asyncio.Semaphore(TRENCHES_CONCURRENCY)
    analyzed_columns: dict[str, list] = {
        "sixk_radar": [],
        "new": [],
        "almost_bonded": [],
        "recently_bonded": [],
    }

    # Analyze $6k-band climbers FIRST so we don't miss $6k entry by minutes
    work: list[tuple[str, dict]] = []
    dropped_high_mcap = 0
    for col, cands in columns.items():
        for cand in cands:
            pre = padre_feed._candidate_mcap(cand)
            if pre > SCAN_MCAP_MAX_USD:
                dropped_high_mcap += 1
                continue
            work.append((col, cand))

    def _work_priority(item: tuple[str, dict]) -> tuple:
        col, cand = item
        mcap = padre_feed._candidate_mcap(cand)
        in_sixk = SIXK_RADAR_MIN_USD <= mcap <= SIXK_RADAR_MAX_USD
        sweet = SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX
        return (
            0 if col == "sixk_radar" or cand.get("_sixk_radar") else 1,
            0 if sweet else 1,
            0 if in_sixk else 2,
            abs(mcap - 6000) if mcap else 99999,
            -cand.get("_quick_alpha", 0),
            float(cand.get("_age_minutes") or 999),
        )

    work.sort(key=_work_priority)

    async def analyze_one(column: str, cand: dict) -> dict | None:
        async with sem:
            try:
                result = await _analyze_token(
                    "solana", cand["tokenAddress"], cand, fast=True
                )
                if result.get("skipped"):
                    return {
                        "column": column,
                        "skipped": True,
                        "mcap_usd": result.get("mcap_usd") or 0,
                    }
                inv = result.get("investSignal") or {}
                trench = inv.get("trench") or result.get("trenchAnalysis")
                safety = result.get("safety") or {}
                avoid = safety.get("avoid") or {}
                # Drop blocklist / ghost launches from trenches results
                if avoid.get("hard_avoid") or avoid.get("avoid"):
                    return {
                        "column": column,
                        "skipped": True,
                        "skipReason": avoid.get("summary") or "avoid_filter",
                        "mcap_usd": result.get("mcap_usd") or 0,
                    }
                mkt = result.get("market") or {}
                pair = {
                    "pumpfun": mkt.get("pumpfun"),
                    "volume": mkt.get("volume"),
                    "priceChange": mkt.get("priceChange"),
                    "liquidity": mkt.get("liquidity"),
                    "txns": {
                        "m5": mkt.get("txns_m5") or {},
                        "h1": mkt.get("txns_h1") or {},
                    },
                    "is_pumpfun_synthetic": bool(mkt.get("is_pumpfun_synthetic")),
                    "url": mkt.get("url"),
                }
                # Reuse hub from analysis — don't re-run checker_hub
                checker_hub = result.get("checkerHub") or run_checker_hub(
                    "solana", cand["tokenAddress"], safety, pair=pair
                )
                smart_money = result.get("smartMoney") or {}
                report = build_safety_report(
                    safety,
                    pair,
                    trench=trench,
                    checker_hub=checker_hub,
                    smart_money=smart_money,
                )
                base = (result.get("market") or {}).get("baseToken") or {}
                mcap = float(
                    result.get("mcap_usd")
                    or (result.get("market") or {}).get("pumpfun", {}).get(
                        "usd_market_cap"
                    )
                    or (result.get("market") or {}).get("marketCap")
                    or 0
                )
                if mcap > SCAN_MCAP_MAX_USD:
                    return {
                        "column": column,
                        "skipped": True,
                        "mcap_usd": mcap,
                    }
                return {
                    "column": column,
                    "chainId": "solana",
                    "tokenAddress": cand["tokenAddress"],
                    "name": base.get("name") or cand.get("name"),
                    "symbol": base.get("symbol") or cand.get("symbol"),
                    "icon": result.get("icon") or cand.get("icon"),
                    "mcap_usd": mcap,
                    "age_minutes": (result.get("market") or {}).get("age_minutes")
                    or cand.get("_age_minutes"),
                    "bonding_progress": (result.get("market") or {}).get(
                        "pumpfun", {}
                    ).get("bonding_progress"),
                    "safetyTier": report["tier"],
                    "safetyScore": report["score"],
                    "safetyReport": report,
                    "investSignal": inv.get("signal"),
                    "investConfidence": inv.get("confidence"),
                    "investSummary": inv.get("summary"),
                    "trenchPassed": bool((trench or {}).get("passed")),
                    "padre": result.get("padre"),
                    "pump_url": (result.get("market") or {}).get(
                        "pumpfun", {}
                    ).get("pump_url"),
                    "socialSignals": result.get("socialSignals") or {},
                    "smartMoney": smart_money,
                    "alphaSetup": result.get("alphaSetup") or {},
                    "checkerHub": checker_hub,
                    "sixkRadar": column == "sixk_radar"
                    or SIXK_RADAR_MIN_USD <= mcap <= SIXK_RADAR_MAX_USD,
                    "entrySweet": SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX,
                    "tradePlan": result.get("tradePlan") or {},
                }
            except Exception:
                return None

    tasks = [analyze_one(col, cand) for col, cand in work]
    results = await asyncio.gather(*tasks)
    analyze_failures = 0
    skipped_late = dropped_high_mcap
    for r in results:
        if r is None:
            analyze_failures += 1
            continue
        if r.get("skipped"):
            skipped_late += 1
            continue
        analyzed_columns[r["column"]].append(r)

    for col in analyzed_columns:
        # Prefer moon-setup alpha + early mcap — never rank high-mcap first
        def _alpha_rank(t: dict) -> int:
            a = (t.get("alphaSetup") or {}).get("tier") or ""
            return {
                "MOON_SETUP": 0,
                "ALPHA": 1,
                "WATCH_ALPHA": 2,
                "SPEC": 3,
                "WEAK": 4,
                "SKIP": 5,
            }.get(a, 4)

        analyzed_columns[col].sort(
            key=lambda t: (
                _alpha_rank(t),
                0 if (t.get("mcap_usd") or 0) <= SCAN_MCAP_FOCUS_MAX_USD else 1,
                float(t.get("age_minutes") or 999),
                {"SAFE_ENTRY": 0, "WATCH": 1, "CAUTION": 2, "HIGH_RISK": 3, "AVOID": 4, "UNSAFE": 5}.get(
                    t["safetyTier"], 9
                ),
                -(t.get("alphaSetup") or {}).get("score", 0),
                -t.get("safetyScore", 0),
                t.get("mcap_usd") or 0,
            )
        )

    all_tokens = (
        analyzed_columns.get("sixk_radar", [])
        + analyzed_columns["new"]
        + analyzed_columns["almost_bonded"]
        + analyzed_columns["recently_bonded"]
    )
    sixk_live = [
        t for t in all_tokens
        if SIXK_RADAR_MIN_USD <= float(t.get("mcap_usd") or 0) <= SIXK_RADAR_MAX_USD
    ]
    sixk_sweet = [
        t for t in sixk_live
        if SIXK_ENTRY_SWEET_MIN <= float(t.get("mcap_usd") or 0) <= SIXK_ENTRY_SWEET_MAX
    ]
    safe_picks = [
        t for t in all_tokens
        if (
            t["safetyTier"] == "SAFE_ENTRY"
            and not t["safetyReport"]["bundle"]["bundled"]
            and t["safetyReport"]["snipers"]["risk_level"] in ("low", "medium")
        )
        or (t.get("alphaSetup") or {}).get("is_alpha")
    ]
    # Dedupe safe_picks by mint
    _seen_safe: set[str] = set()
    _deduped_safe: list = []
    for t in safe_picks:
        k = t.get("tokenAddress") or ""
        if k in _seen_safe:
            continue
        _seen_safe.add(k)
        _deduped_safe.append(t)
    safe_picks = _deduped_safe
    safe_picks.sort(
        key=lambda t: (
            0 if (t.get("alphaSetup") or {}).get("tier") == "MOON_SETUP" else 1,
            0 if (t.get("alphaSetup") or {}).get("is_alpha") else 2,
            -(t.get("alphaSetup") or {}).get("score", 0),
            -(t.get("socialSignals") or {}).get("highlight", False),
            -t["safetyScore"],
            t.get("mcap_usd") or 0,
        )
    )

    alpha_picks = [
        t for t in all_tokens
        if (t.get("alphaSetup") or {}).get("highlight")
    ]
    alpha_picks.sort(
        key=lambda t: (
            0 if (t.get("alphaSetup") or {}).get("tier") == "MOON_SETUP" else 1,
            -(t.get("alphaSetup") or {}).get("score", 0),
            t.get("mcap_usd") or 0,
        )
    )

    checker_pass = [
        t for t in all_tokens
        if (t.get("checkerHub") or {}).get("consensus", {}).get("verdict") == "PASS"
    ]
    checker_warn = [
        t for t in all_tokens
        if (t.get("checkerHub") or {}).get("consensus", {}).get("verdict") == "WARN"
    ]
    checker_fail = [
        t for t in all_tokens
        if (t.get("checkerHub") or {}).get("consensus", {}).get("verdict") == "FAIL"
    ]

    narrative_picks = [
        t for t in all_tokens
        if (t.get("socialSignals") or {}).get("highlight")
    ]
    narrative_picks.sort(
        key=lambda t: (
            (t.get("socialSignals") or {}).get("influencer_tweet", False),
            len((t.get("socialSignals") or {}).get("influencer_accounts", [])),
        ),
        reverse=True,
    )

    response = {
        "source": "padre_trenches_proxy",
        "padre_url": padre.trenches_url(),
        "scanned_at": time.time(),
        "columns": analyzed_columns,
        "safe_picks": safe_picks[:10],
        "alpha_picks": alpha_picks[:12],
        "sixk_picks": sixk_sweet[:15] or sixk_live[:15],
        "narrative_picks": narrative_picks[:15],
        "counts": {
            "sixk_radar": len(analyzed_columns.get("sixk_radar", [])),
            "new": len(analyzed_columns["new"]),
            "almost_bonded": len(analyzed_columns["almost_bonded"]),
            "recently_bonded": len(analyzed_columns["recently_bonded"]),
            "total": len(all_tokens),
            "safe_picks": len(safe_picks),
            "alpha_picks": len(alpha_picks),
            "sixk_live": len(sixk_live),
            "sixk_sweet": len(sixk_sweet),
            "narrative_picks": len(narrative_picks),
            "checker_pass": len(checker_pass),
            "checker_warn": len(checker_warn),
            "checker_fail": len(checker_fail),
            "analyze_failures": analyze_failures,
            "skipped_late_mcap": skipped_late,
            "mcap_max_usd": SCAN_MCAP_MAX_USD,
            "sixk_band": f"${SIXK_RADAR_MIN_USD:,.0f}–${SIXK_RADAR_MAX_USD:,.0f}",
        },
        "checker_picks": checker_pass[:12],
        "learning": _learning_memory.get_outcomes_summary(),
        "disclaimer": (
            "Padre live WebSocket requires login — data mirrors Trenches via "
            "pump.fun (NEW / Almost Bonded / Recently Bonded). "
            "Not financial advice. Memecoins are extremely high risk. "
            "Learned entry/TP/exit improve as more tokens are tracked."
        ),
    }
    _trenches_cache.update({"key": cache_key, "data": response, "ts": time.time()})
    return response


@app.get("/api/padre/trenches/feed")
async def padre_trenches_feed(
    per_column: int = Query(8, ge=5, le=30),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
):
    """Fast trenches preview — pump.fun tokens only, no RugCheck wait."""
    return await _fetch_trenches_feed(
        per_column=per_column,
        max_age_minutes=max_age_minutes,
    )


@app.get("/api/learning/stats")
async def learning_stats():
    """How many tokens learned + outcome breakdown."""
    summary = _learning_memory.get_outcomes_summary()
    recent = _learning_memory.recent_finalized(15)
    return {
        "ok": True,
        "summary": summary,
        "recent": recent,
        "db": str(BASE_DIR / "data" / "learning.db"),
    }


@app.get("/api/learning/predict/{mint}")
async def learning_predict(mint: str):
    """Full analysis + learned trade plan for one mint."""
    result = await _analyze_token("solana", mint.strip())
    return {
        "mint": mint,
        "tradePlan": result.get("tradePlan"),
        "alphaSetup": result.get("alphaSetup"),
        "investSignal": result.get("investSignal"),
        "mcap_usd": result.get("mcap_usd"),
        "safety": {
            "passed": (result.get("safety") or {}).get("passed"),
            "avoid": (result.get("safety") or {}).get("avoid"),
        },
        "history": _learning_memory.get_token(mint.strip()),
    }


@app.get("/api/padre/sixk")
async def sixk_radar_fast(
    limit: int = Query(24, ge=8, le=50),
    max_age_minutes: float = Query(40, ge=5, le=MAX_AGE_MINUTES_CAP),
):
    """Ultra-fast $2k–$9k climber list (no RugCheck) — for early $6k entry."""
    cands = await padre_feed.fetch_sixk_radar(
        limit=limit, max_age_minutes=max_age_minutes
    )
    tokens = [_preview_from_candidate("sixk_radar", c) for c in cands]
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


@app.get("/api/padre/trenches")
async def padre_trenches_scan(
    per_column: int = Query(8, ge=5, le=30),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
    force: bool = Query(False),
):
    """Real-time Padre Trenches analysis — bundle, dev, sniper, safety for every token."""
    return await _analyze_trenches(
        per_column=per_column,
        max_age_minutes=max_age_minutes,
        force=force,
    )


@app.get("/api/invest")
async def invest_recommendations(
    chains: str = Query("solana"),
    limit: int = Query(15, ge=5, le=MAX_SCAN_LIMIT),
    safe_only: bool = Query(True),
    force: bool = Query(False),
    max_age_minutes: float = Query(
        DEFAULT_MAX_AGE_MINUTES, ge=5, le=MAX_AGE_MINUTES_CAP
    ),
    exclude_graduated: bool = Query(EXCLUDE_GRADUATED_DEFAULT),
):
    """Ranked invest/exit picks — Padre Trenches + Trending + pump.fun merged."""
    return await _run_invest(
        chains=chains,
        limit=limit,
        safe_only=safe_only,
        force=force,
        max_age_minutes=max_age_minutes,
        exclude_graduated=exclude_graduated,
    )


@app.get("/api/checkers/{chain_id}/{token_address}")
async def get_checker_report(chain_id: str, token_address: str):
    """Standalone multi-checker security report (RugCheck, Padre, DexScreener, etc.)."""
    chain = chain_id.lower().strip()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    result = await _analyze_token(chain, token_address)
    return {
        "chainId": chain,
        "tokenAddress": token_address,
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


@app.post("/api/analyze")
async def analyze_token(req: AnalyzeRequest):
    chain = req.chain_id.lower().strip()
    addr = req.token_address.strip()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain}")
    return await _analyze_token(chain, addr)


@app.get("/api/analyze/{chain_id}/{token_address}")
async def analyze_token_get(chain_id: str, token_address: str):
    if chain_id not in SUPPORTED_CHAINS:
        raise HTTPException(400, f"Unsupported chain: {chain_id}")
    return await _analyze_token(chain_id, token_address)


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8765"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=not IS_PRODUCTION)