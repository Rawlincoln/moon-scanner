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
    NEAR_MIGRATION_MAX_STICKY,
    NEAR_MIGRATION_STICKY_TTL_SEC,
    REQUEST_TIMEOUT,
    RUNNER_ALERT_TTL_SEC,
    RUNNER_RADAR_INTERVAL_SEC,
    DEFAULT_SCAN_LIMIT,
    EVM_CHAIN_IDS,
    EXCLUDE_GRADUATED_DEFAULT,
    FAST_SCAN_SKIP_DEX_ORDERS,
    GRADUATION_MCAP_USD,
    IS_PRODUCTION,
    IS_RENDER,
    MAX_AGE_MINUTES_CAP,
    MAX_SCAN_LIMIT,
    DUMP_HIDE_FRAC,
    MIGRATION_MCAP_MAX_USD,
    MIGRATION_NEAR_MIN_PCT,
    NEAR_ATH_BUY_FRAC,
    NEAR_MIG_BUY_MIN_BOND,
    NEAR_MIG_BUY_MIN_SCORE,
    NEAR_MIG_MIN_MCAP,
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
    UNDER25K_MAX_USD,
    UNDER25K_MIN_USD,
    USER_AGENT,
)
import httpx

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
from services.migration_path import analyze_migration_path
from services.deep_analysis import build_deep_analysis
from services.runner_radar import (
    build_runner_alerts,
    extract_ath_mcap,
    is_crashed_runner,
    score_runner_candidate,
)
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

# Sticky runner alerts (mint → alert dict) — survives across scans
_runner_alert_store: dict[str, dict[str, Any]] = {}
_runner_alert_lock = asyncio.Lock()
# Sticky near-migration tokens (mint → token snapshot) — survive missed polls
_near_mig_store: dict[str, dict[str, Any]] = {}
_near_mig_lock = asyncio.Lock()


def _is_near_migration_token(t: dict[str, Any]) -> bool:
    if t.get("skipped"):
        return False
    mig = t.get("migrationPath") or {}
    lane = mig.get("lane") or t.get("migrationLane") or ""
    bond = float(t.get("bonding_progress") or mig.get("bonding_pct") or 0)
    mcap = float(t.get("mcap_usd") or 0)
    if lane in ("near_migration", "migrated"):
        return True
    if t.get("column") in ("almost_bonded", "recently_bonded"):
        return True
    if bond >= MIGRATION_NEAR_MIN_PCT:
        return True
    # ~$28k+ still on path to graduation
    if mcap >= GRADUATION_MCAP_USD * (MIGRATION_NEAR_MIN_PCT / 100.0) and mcap <= MIGRATION_MCAP_MAX_USD:
        return True
    return False


async def _pin_near_migration_tokens(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pin near-migration tokens so they stay on screen across brief polls."""
    now = time.time()
    async with _near_mig_lock:
        for t in tokens:
            mint = t.get("tokenAddress") or ""
            if not mint:
                continue
            # Always track peak for known pins even if re-scored
            prev = _near_mig_store.get(mint) or {}
            mcap = float(t.get("mcap_usd") or 0)
            ath = extract_ath_mcap(t)
            peak = max(
                float(prev.get("_peak_mcap") or 0),
                ath,
                mcap,
            )
            t = dict(t)
            t["_peak_mcap"] = peak
            t["ath_mcap"] = ath or prev.get("ath_mcap")

            crashed, reason = is_crashed_runner(t, mcap=mcap, ath=ath, peak=peak)
            if crashed:
                _near_mig_store.pop(mint, None)
                continue
            if not _is_near_migration_token(t) and mint not in _near_mig_store:
                continue
            # If already pinned but no longer near-mig and not crashed, keep until TTL
            # unless mcap collapsed below floor
            if mint in _near_mig_store and mcap > 0 and mcap < 3_500 and peak >= 12_000:
                _near_mig_store.pop(mint, None)
                continue

            snap = dict(t)
            snap["_sticky_near_mig"] = True
            snap["_first_seen"] = prev.get("_first_seen") or now
            snap["_last_seen"] = now
            snap["_peak_mcap"] = peak
            _near_mig_store[mint] = snap

        # Expire old pins + re-check crash on stored snapshots
        dead: list[str] = []
        for m, v in _near_mig_store.items():
            if now - float(v.get("_last_seen") or 0) > NEAR_MIGRATION_STICKY_TTL_SEC:
                dead.append(m)
                continue
            crashed, _ = is_crashed_runner(v)
            if crashed:
                dead.append(m)
        for m in dead:
            _near_mig_store.pop(m, None)

        # Cap size — keep highest bonding / most recent
        if len(_near_mig_store) > NEAR_MIGRATION_MAX_STICKY:
            ranked = sorted(
                _near_mig_store.values(),
                key=lambda x: (
                    -(float(x.get("bonding_progress") or 0)),
                    -float(x.get("_last_seen") or 0),
                ),
            )
            keep = {t.get("tokenAddress") for t in ranked[:NEAR_MIGRATION_MAX_STICKY]}
            for m in list(_near_mig_store.keys()):
                if m not in keep:
                    _near_mig_store.pop(m, None)

        return list(_near_mig_store.values())


def _merge_sticky_near_mig(
    live: list[dict[str, Any]], sticky: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Live scan wins; sticky fills gaps so tokens don't flash and vanish."""
    by_mint: dict[str, dict] = {}
    for t in sticky:
        m = t.get("tokenAddress")
        if m:
            by_mint[m] = t
    for t in live:
        m = t.get("tokenAddress")
        if not m:
            continue
        if m in by_mint:
            # Merge: keep sticky first_seen, update live fields
            prev = by_mint[m]
            merged = dict(t)
            merged["_sticky_near_mig"] = True
            merged["_first_seen"] = prev.get("_first_seen")
            merged["_last_seen"] = time.time()
            merged["_pinned_sec"] = int(
                time.time() - float(prev.get("_first_seen") or time.time())
            )
            by_mint[m] = merged
        else:
            by_mint[m] = t
    # Sticky-only (missed this poll) — still show, mark as pinned
    out = list(by_mint.values())
    for t in out:
        if t.get("_last_seen") and time.time() - float(t["_last_seen"]) > 15:
            t["_pinned_stale"] = True
            # Soft banner so user knows it's held from a prior scan
            if not t.get("investSummary"):
                t["investSummary"] = (
                    "Pinned near-migration — still tracking (may have left live feed)"
                )
    out.sort(
        key=lambda x: (
            -(float(x.get("bonding_progress") or 0)),
            -(float(x.get("mcap_usd") or 0)),
        )
    )
    return out


async def _background_trenches_warm() -> None:
    """Keep trenches + runner band warm so climbers aren't minutes late."""
    await asyncio.sleep(6)
    while True:
        try:
            logger.info("Background trenches/runner refresh starting")
            await _analyze_trenches(
                per_column=BACKGROUND_SCAN_PER_COLUMN,
                max_age_minutes=max(DEFAULT_MAX_AGE_MINUTES, 60),
                force=True,
            )
            logger.info("Background trenches/runner refresh done")
        except Exception as exc:
            logger.warning("Background trenches refresh failed: %s", exc)
        await asyncio.sleep(BACKGROUND_SCAN_INTERVAL_SEC)


async def _background_runner_alert_loop() -> None:
    """Recompute sticky runner alerts + revalidate near-mig mcaps from pump.fun."""
    await asyncio.sleep(10)
    while True:
        try:
            await _revalidate_sticky_near_mig_live()
            await _refresh_runner_alerts_from_cache()
        except Exception as exc:
            logger.warning("Runner alert loop failed: %s", exc)
        await asyncio.sleep(RUNNER_RADAR_INTERVAL_SEC)


async def _revalidate_sticky_near_mig_live() -> None:
    """Fetch live mcap for pinned near-mig tokens; drop dumps immediately."""
    async with _near_mig_lock:
        mints = list(_near_mig_store.keys())
    if not mints:
        return
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": USER_AGENT,
            "Origin": "https://pump.fun",
            "Accept": "application/json",
        },
    ) as client:
        for mint in mints[:30]:
            try:
                resp = await client.get(
                    f"https://frontend-api-v3.pump.fun/coins/{mint}"
                )
                if resp.status_code != 200:
                    continue
                coin = resp.json()
                mcap = float(coin.get("usd_market_cap") or 0)
                ath = float(coin.get("ath_market_cap") or 0)
                async with _near_mig_lock:
                    prev = _near_mig_store.get(mint)
                    if not prev:
                        continue
                    peak = max(
                        float(prev.get("_peak_mcap") or 0),
                        ath,
                        mcap,
                        float(prev.get("mcap_usd") or 0),
                    )
                    prev["mcap_usd"] = mcap
                    prev["ath_mcap"] = ath or prev.get("ath_mcap")
                    prev["_peak_mcap"] = peak
                    prev["_last_seen"] = time.time()
                    bond = PumpFunClient.bonding_progress(coin)
                    prev["bonding_progress"] = bond
                    crashed, reason = is_crashed_runner(
                        prev, mcap=mcap, ath=ath, peak=peak
                    )
                    if crashed or (peak >= 10_000 and mcap < peak * 0.55):
                        logger.info(
                            "Drop sticky near-mig %s: %s (mcap=%.0f peak=%.0f)",
                            mint[:8],
                            reason or "fade",
                            mcap,
                            peak,
                        )
                        _near_mig_store.pop(mint, None)
                        async with _runner_alert_lock:
                            _runner_alert_store.pop(mint, None)
            except Exception:
                continue


async def _refresh_runner_alerts_from_cache() -> list[dict]:
    """Update sticky alert store from latest trenches scan cache."""
    cached = _trenches_cache.get("data") or {}
    tokens: list[dict] = []
    for key in (
        "migration_picks",
        "under25k_picks",
        "early_lottery",
        "alpha_picks",
        "safe_picks",
        "sixk_picks",
    ):
        tokens.extend(cached.get(key) or [])
    cols = cached.get("columns") or {}
    for col in ("almost_bonded", "under_25k", "sixk_radar", "new", "recently_bonded"):
        tokens.extend(cols.get(col) or [])
    # Dedupe
    seen: set[str] = set()
    deduped: list[dict] = []
    for t in tokens:
        m = t.get("tokenAddress") or ""
        if not m or m in seen or t.get("skipped"):
            continue
        seen.add(m)
        # Attach runner score on the token for UI
        rr = score_runner_candidate(t)
        t["runnerRadar"] = rr
        deduped.append(t)

    prev = set(_runner_alert_store.keys())
    fresh = build_runner_alerts(deduped, prev_mints=prev)
    now = time.time()
    # Live mcap map for sticky crash checks
    live_by_mint = {t.get("tokenAddress"): t for t in deduped if t.get("tokenAddress")}

    async with _runner_alert_lock:
        # Update / insert
        for item in fresh:
            mint = item["tokenAddress"]
            existing = _runner_alert_store.get(mint)
            peak = max(
                float(item.get("_peak_mcap") or 0),
                float((existing or {}).get("_peak_mcap") or 0),
                float(item.get("mcap_usd") or 0),
                float(item.get("ath_mcap") or 0),
            )
            item["_peak_mcap"] = peak
            if existing:
                item["is_new_alert"] = False
                item["first_seen"] = existing.get("first_seen") or existing.get(
                    "alerted_at"
                )
            else:
                item["is_new_alert"] = True
                item["first_seen"] = now
            item["alerted_at"] = now
            _runner_alert_store[mint] = item

        # Purge TTL + crashes (CHOCI-class: ATH $20k → $2k)
        dead: list[str] = []
        for m, v in list(_runner_alert_store.items()):
            if now - float(v.get("first_seen") or v.get("alerted_at") or 0) > RUNNER_ALERT_TTL_SEC:
                dead.append(m)
                continue
            live = live_by_mint.get(m)
            if live:
                # Refresh sticky snapshot with live mcap for crash check
                merged = dict(v)
                merged["mcap_usd"] = live.get("mcap_usd")
                merged["bonding_progress"] = live.get("bonding_progress")
                merged["ath_mcap"] = extract_ath_mcap(live) or v.get("ath_mcap")
                merged["_peak_mcap"] = max(
                    float(v.get("_peak_mcap") or 0),
                    float(live.get("mcap_usd") or 0),
                    extract_ath_mcap(live),
                )
                merged["safetyReport"] = live.get("safetyReport") or v.get("safetyReport")
                merged["priceChange"] = (live.get("market") or {}).get("priceChange")
                rr = score_runner_candidate(merged)
                if rr.get("crashed") or not rr.get("alert"):
                    dead.append(m)
                    continue
                v.update(
                    {
                        "mcap_usd": merged["mcap_usd"],
                        "bonding_progress": merged.get("bonding_progress"),
                        "_peak_mcap": merged["_peak_mcap"],
                        "ath_mcap": merged.get("ath_mcap"),
                        "runnerRadar": rr,
                        "is_new_alert": False,
                        "alerted_at": now,
                    }
                )
            else:
                # Missed poll — still drop if last known mcap is a crash vs peak
                crashed, _ = is_crashed_runner(v)
                if crashed or (v.get("runnerRadar") or {}).get("crashed"):
                    dead.append(m)
                else:
                    v["is_new_alert"] = False
                    v["_missed_poll"] = True
        for m in dead:
            _runner_alert_store.pop(m, None)

        ordered = sorted(
            _runner_alert_store.values(),
            key=lambda x: (
                x.get("runnerRadar", {}).get("priority", 99),
                -(x.get("runnerRadar", {}).get("score") or 0),
            ),
        )
        return ordered


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
    # Rebuild feature_stats periodically so new features + caps improve accuracy
    try:
        ver = "learn_lr_v3_lottery_dump_2026_07"
        if _learning_memory.get_meta("learn_model_version") != ver:
            rebuilt = _learning_memory.rebuild_feature_stats()
            _learning_memory.set_meta("learn_model_version", ver)
            logger.info("Learning feature_stats rebuilt: %s", rebuilt)
    except Exception as exc:
        logger.warning("Learning rebuild failed: %s", exc)
    tasks: list[asyncio.Task] = []
    if BACKGROUND_SCAN_INTERVAL_SEC > 0 and BACKGROUND_SCAN_PER_COLUMN > 0:
        tasks.append(asyncio.create_task(_background_trenches_warm()))
    tasks.append(asyncio.create_task(_background_learning_loop()))
    if RUNNER_RADAR_INTERVAL_SEC > 0:
        tasks.append(asyncio.create_task(_background_runner_alert_loop()))
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
        # Keep ATH + socials so dump filters / avoid rules actually work
        summary["pumpfun"] = {
            "mint": pump_coin.get("mint"),
            "name": pump_coin.get("name"),
            "symbol": pump_coin.get("symbol"),
            "description": pump_coin.get("description"),
            "twitter": pump_coin.get("twitter"),
            "website": pump_coin.get("website"),
            "telegram": pump_coin.get("telegram"),
            "bonding_progress": round(PumpFunClient.bonding_progress(pump_coin), 1),
            "usd_market_cap": pump_coin.get("usd_market_cap"),
            "ath_market_cap": pump_coin.get("ath_market_cap"),
            "ath_market_cap_timestamp": pump_coin.get("ath_market_cap_timestamp"),
            "created_timestamp": pump_coin.get("created_timestamp"),
            "reply_count": pump_coin.get("reply_count", 0),
            "complete": pump_coin.get("complete", False),
            "creator": pump_coin.get("creator"),
            "real_sol_reserves": pump_coin.get("real_sol_reserves"),
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
        cand_pf = (candidate or {}).get("pumpfun") or {}
        pre_ath = float(
            cand_pf.get("ath_market_cap")
            or candidate.get("ath_market_cap")
            or candidate.get("_ath_mcap")
            or 0
        )
        if pre_mcap > SCAN_MCAP_MAX_USD and (candidate or {}).get("column") not in (
            "almost_bonded",
            "recently_bonded",
            "under_25k",
        ):
            # allow migration columns higher via col cap later
            if pre_mcap > MIGRATION_MCAP_MAX_USD:
                return {
                    "chainId": chain_id,
                    "tokenAddress": token_address,
                    "skipped": True,
                    "skipReason": f"mcap ${pre_mcap:,.0f} too high",
                    "mcap_usd": pre_mcap,
                    "analyzedAt": time.time(),
                }
        # Instant dump skip before RugCheck (speed + accuracy)
        if pre_ath >= 3_000 and pre_mcap > 0 and pre_mcap < pre_ath * DUMP_HIDE_FRAC:
            return {
                "chainId": chain_id,
                "tokenAddress": token_address,
                "skipped": True,
                "skipReason": (
                    f"Already dumped −{(1 - pre_mcap / pre_ath) * 100:.0f}% "
                    f"(${pre_ath:,.0f}→${pre_mcap:,.0f})"
                ),
                "mcap_usd": pre_mcap,
                "analyzedAt": time.time(),
            }

    # Market resolve + Padre (timeout Padre so bulk doesn't stall)
    async def _padre_safe():
        try:
            return await asyncio.wait_for(
                padre.get_token_audit(chain_id, token_address),
                timeout=2.5 if fast else 5.0,
            )
        except Exception:
            return None

    pair, padre_audit = await asyncio.gather(
        _resolve_pair(chain_id, token_address, candidate),
        _padre_safe(),
    )
    pump_coin = pair.get("pumpfun") or (candidate or {}).get("pumpfun")
    mcap_for_sm = _token_mcap(pair, candidate)
    ath_live = float((pump_coin or {}).get("ath_market_cap") or 0)

    # Dump skip after live resolve
    if ath_live >= 3_000 and mcap_for_sm > 0 and mcap_for_sm < ath_live * DUMP_HIDE_FRAC:
        return {
            "chainId": chain_id,
            "tokenAddress": token_address,
            "skipped": True,
            "skipReason": (
                f"Already dumped −{(1 - mcap_for_sm / ath_live) * 100:.0f}% "
                f"(ATH ${ath_live:,.0f}→${mcap_for_sm:,.0f})"
            ),
            "mcap_usd": mcap_for_sm,
            "market": _format_pair_summary(pair),
            "analyzedAt": time.time(),
        }

    # Late drop after live mcap resolve
    if mcap_for_sm > MIGRATION_MCAP_MAX_USD:
        return {
            "chainId": chain_id,
            "tokenAddress": token_address,
            "skipped": True,
            "skipReason": f"live mcap ${mcap_for_sm:,.0f} > ${MIGRATION_MCAP_MAX_USD:,.0f}",
            "mcap_usd": mcap_for_sm,
            "market": _format_pair_summary(pair),
            "analyzedAt": time.time(),
        }

    if chain_id == "solana":
        safety = await sol.analyze(
            token_address,
            pump_coin=pump_coin,
            padre_audit=padre_audit,
            fast=fast,
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
    migration = analyze_migration_path(
        mcap_usd=mcap_for_sm,
        bonding_progress=(pump_coin or {}).get("bonding_progress"),
        safety=safety,
        pair=pair,
        pump=pump_coin,
        avoid=(safety.get("avoid") or {}),
        alpha=alpha,
        complete=bool((pump_coin or {}).get("complete")),
    )
    # Capital protection: default WATCH. BUY only multi-gate deep analysis.
    bond_pct = float(migration.get("bonding_pct") or 0)
    ath_now = float((pump_coin or {}).get("ath_market_cap") or ath_live or 0)
    near_ath = ath_now <= 0 or (
        mcap_for_sm > 0 and mcap_for_sm >= ath_now * NEAR_ATH_BUY_FRAC
    )
    tx_act = alpha.get("txActivity") or {}
    invest = dict(invest or {})
    # Strip any generic STRONG from base signals
    if invest.get("signal") in ("STRONG_INVEST", "INVEST"):
        invest["signal"] = "WATCH"
        invest["summary"] = "Default WATCH — multi-gate buy not verified yet."

    deep = build_deep_analysis(
        mcap=mcap_for_sm,
        safety=safety,
        pair=pair,
        pump=pump_coin,
        alpha=alpha,
        migration=migration,
        avoid=safety.get("avoid"),
        smart_money=smart_money,
        social=social,
    )

    if deep.get("verdict") == "BUY" and deep.get("buy_ready"):
        invest["signal"] = "STRONG_INVEST"
        invest["confidence"] = deep.get("confidence")
        invest["summary"] = (
            f"BUY gate passed ({deep.get('gates_passed')}/{deep.get('gates_total')}). "
            f"{deep.get('summary')} · {deep.get('position_advice')}"
        )
        invest["deep_buy"] = True
    elif deep.get("verdict") == "SKIP" or deep.get("dump", {}).get("is_dumped"):
        invest["signal"] = "AVOID"
        invest["confidence"] = deep.get("confidence") or 85
        invest["summary"] = deep.get("summary") or "Skip — dump/risk"
    else:
        invest["signal"] = "WATCH"
        invest["confidence"] = min(50, int(deep.get("confidence") or 40))
        invest["summary"] = (
            f"WATCH only ({deep.get('gates_passed')}/{deep.get('gates_total')} gates). "
            f"Do not size up. {deep.get('position_advice')}"
        )
    invest["migration_lane"] = migration.get("lane")
    invest["ceiling"] = alpha.get("ceiling_label")
    invest["reasons"] = (deep.get("why") or [])[:6] + (deep.get("risks") or [])[:4]

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
        "migrationPath": migration,
        "deepAnalysis": deep,
        "checkerHub": checker_hub,
        "padre": links,
        "analyzedAt": time.time(),
        "source": (candidate or {}).get("source", ""),
        "sources": sources,
        "icon": (candidate or {}).get("icon", ""),
        "description": (candidate or {}).get("description", ""),
        "mcap_usd": mcap_for_sm,
        "ath_mcap": ath_now or None,
    }
    try:
        trade_plan = _learning.observe_analysis(result)
        if trade_plan:
            # Force plan to match multi-gate deep analysis (protect capital)
            trade_plan = dict(trade_plan)
            if deep.get("verdict") == "BUY" and deep.get("buy_ready"):
                trade_plan["action"] = "ENTER"
                trade_plan["summary"] = invest.get("summary")
                trade_plan["confidence"] = deep.get("confidence")
            else:
                if trade_plan.get("action") == "ENTER":
                    trade_plan["action"] = "WATCH" if deep.get("verdict") == "WATCH" else "SKIP"
                trade_plan["summary"] = (
                    f"Gate override: {deep.get('summary')}. "
                    + (trade_plan.get("summary") or "")
                )
            trade_plan["deepAnalysis"] = {
                "verdict": deep.get("verdict"),
                "gates": f"{deep.get('gates_passed')}/{deep.get('gates_total')}",
                "dump": deep.get("dump"),
                "tx": deep.get("tx_interest"),
            }
            result["tradePlan"] = trade_plan
            if trade_plan.get("action") == "SKIP":
                invest = dict(invest)
                invest["signal"] = "AVOID"
                invest["summary"] = trade_plan.get("summary")
                result["investSignal"] = invest
    except Exception as exc:
        logger.debug("learning observe failed: %s", exc)
    result["deepAnalysis"] = deep
    result["investSignal"] = invest
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
        "runner_radar": True,
        "runner_alerts": len(_runner_alert_store),
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
    bond = float(
        cand.get("bonding_progress")
        or pf.get("bonding_progress")
        or (min(100.0, (mcap / GRADUATION_MCAP_USD) * 100) if mcap else 0)
    )
    sixk = (
        column == "sixk_radar"
        or cand.get("_sixk_radar")
        or (SIXK_RADAR_MIN_USD <= mcap <= SIXK_RADAR_MAX_USD)
    )
    sweet = SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX
    if bond >= MIGRATION_NEAR_MIN_PCT or column == "almost_bonded":
        lane = "near_migration"
    elif column == "under_25k" or (
        UNDER25K_MIN_USD <= mcap <= UNDER25K_MAX_USD
    ):
        lane = "under_25k"
    elif mcap > 0 and mcap < UNDER25K_MIN_USD:
        lane = "early_lottery"
    else:
        lane = "early_lottery"
    if lane == "near_migration":
        verdict = (
            f"Near migration {bond:.0f}% — ${mcap:,.0f} "
            f"(~${max(0, GRADUATION_MCAP_USD - mcap):,.0f} to grad) — checking…"
        )
    elif lane == "under_25k":
        verdict = f"Under $25k · {bond:.0f}% bonded — ${mcap:,.0f} — checking…"
    elif sixk:
        verdict = (
            f"Early lottery ${mcap:,.0f} "
            f"{'(sweet $3.5–7.5k — rarely migrates)' if sweet else '— rarely migrates'}"
        )
    else:
        verdict = "Analyzing RugCheck + Padre…"
    return {
        "column": column,
        "chainId": "solana",
        "tokenAddress": mint,
        "name": pf.get("name") or cand.get("name") or "Unknown",
        "symbol": pf.get("symbol") or cand.get("symbol") or "?",
        "icon": cand.get("icon") or pf.get("image_uri"),
        "mcap_usd": mcap,
        "age_minutes": cand.get("_age_minutes") or cand.get("age_minutes"),
        "bonding_progress": bond,
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
        "migrationLane": lane,
        "migrationPath": {
            "lane": lane,
            "bonding_pct": round(bond, 1),
            "score": int(min(90, bond * 0.9))
            if bond >= MIGRATION_NEAR_MIN_PCT
            else int(bond * 0.4),
            "summary": verdict,
            "to_graduation_usd": round(max(0.0, GRADUATION_MCAP_USD - mcap)),
        },
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
            "under_25k": len(preview_columns.get("under_25k") or []),
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
        "under_25k": [],
        "almost_bonded": [],
        "recently_bonded": [],
    }

    # Analyze near-migration FIRST (user: recs never reached migration)
    work: list[tuple[str, dict]] = []
    dropped_high_mcap = 0
    for col, cands in columns.items():
        for cand in cands:
            pre = padre_feed._candidate_mcap(cand)
            cap = (
                MIGRATION_MCAP_MAX_USD
                if col in ("almost_bonded", "recently_bonded", "under_25k")
                else SCAN_MCAP_MAX_USD
            )
            if pre > cap:
                dropped_high_mcap += 1
                continue
            work.append((col, cand))

    def _work_priority(item: tuple[str, dict]) -> tuple:
        col, cand = item
        mcap = padre_feed._candidate_mcap(cand)
        bond = float(cand.get("bonding_progress") or 0)
        if bond <= 0 and mcap > 0:
            bond = min(100.0, (mcap / GRADUATION_MCAP_USD) * 100)
        in_sixk = SIXK_RADAR_MIN_USD <= mcap <= SIXK_RADAR_MAX_USD
        sweet = SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX
        # Priority: almost bonded → under 25k climbers → sixk → new dust
        col_rank = {
            "almost_bonded": 0,
            "under_25k": 1,
            "recently_bonded": 2,
            "sixk_radar": 3,
            "new": 4,
        }.get(col, 5)
        return (
            col_rank,
            0 if bond >= MIGRATION_NEAR_MIN_PCT else 1,
            -bond,
            0 if sweet else 1,
            0 if in_sixk else 2,
            abs(mcap - 6000) if mcap and col_rank >= 3 else 0,
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
                # Only hide absolute junk — soft avoids (entry traps, packaging)
                # still display with AVOID badge so the UI is never empty.
                fatal_flags = {
                    "blocklist",
                    "banned",
                    "honeypot",
                    "rugged",
                    "flash_pump_dump",
                    "drained_curve",
                    "lp_unlocked",
                    "lp_not_locked",
                    "adult_bait",
                }
                flags = set(avoid.get("flags") or [])
                if flags & fatal_flags:
                    return {
                        "column": column,
                        "skipped": True,
                        "skipReason": avoid.get("summary") or "avoid_filter",
                        "mcap_usd": result.get("mcap_usd") or 0,
                    }
                mkt = result.get("market") or {}
                # Hide already-dumped charts (ATH vs live mcap) — user request
                pf_early = mkt.get("pumpfun") or {}
                cand_pf = cand.get("pumpfun") or {}
                mcap_early = float(
                    result.get("mcap_usd")
                    or pf_early.get("usd_market_cap")
                    or cand_pf.get("usd_market_cap")
                    or mkt.get("marketCap")
                    or 0
                )
                ath_early = float(
                    pf_early.get("ath_market_cap")
                    or cand_pf.get("ath_market_cap")
                    or cand.get("ath_market_cap")
                    or cand.get("_ath_mcap")
                    or 0
                )
                dump_probe = {
                    "mcap_usd": mcap_early,
                    "ath_mcap": ath_early,
                    "peak_mcap": ath_early,
                    "_peak_mcap": ath_early,
                    "priceChange": mkt.get("priceChange") or {},
                    "safetyReport": {"avoid": avoid},
                    "column": column,
                    "pumpfun": {**cand_pf, **pf_early},
                }
                dumped, dump_why = is_crashed_runner(dump_probe)
                if dumped or (
                    ath_early >= 3_000
                    and mcap_early > 0
                    and mcap_early < ath_early * DUMP_HIDE_FRAC
                ):
                    return {
                        "column": column,
                        "skipped": True,
                        "skipReason": dump_why
                        or f"Dumped from ATH ${ath_early:,.0f} → ${mcap_early:,.0f}",
                        "mcap_usd": mcap_early,
                    }
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
                col_cap = (
                    MIGRATION_MCAP_MAX_USD
                    if column in ("almost_bonded", "recently_bonded", "under_25k")
                    else SCAN_MCAP_MAX_USD
                )
                if mcap > col_cap:
                    return {
                        "column": column,
                        "skipped": True,
                        "mcap_usd": mcap,
                    }
                bond = (result.get("market") or {}).get("pumpfun", {}).get(
                    "bonding_progress"
                )
                if bond is None and mcap > 0:
                    bond = min(100.0, (mcap / GRADUATION_MCAP_USD) * 100)
                mig = result.get("migrationPath") or {}
                # Re-bucket into the right UI lane by live bonding
                lane = mig.get("lane") or ""
                out_col = column
                if column not in ("recently_bonded",) and lane == "near_migration":
                    out_col = "almost_bonded"
                elif (
                    column in ("new", "sixk_radar")
                    and UNDER25K_MIN_USD <= mcap <= UNDER25K_MAX_USD
                ):
                    out_col = "under_25k"
                pf = (result.get("market") or {}).get("pumpfun") or {}
                ath_mcap = float(
                    pf.get("ath_market_cap")
                    or cand.get("ath_market_cap")
                    or (cand.get("pumpfun") or {}).get("ath_market_cap")
                    or 0
                )
                return {
                    "column": out_col,
                    "chainId": "solana",
                    "tokenAddress": cand["tokenAddress"],
                    "name": base.get("name") or cand.get("name"),
                    "symbol": base.get("symbol") or cand.get("symbol"),
                    "icon": result.get("icon") or cand.get("icon"),
                    "mcap_usd": mcap,
                    "ath_mcap": ath_mcap or None,
                    "peak_mcap": ath_mcap or None,  # peak = ATH only (never current mcap)
                    "_peak_mcap": ath_mcap or None,
                    "age_minutes": (result.get("market") or {}).get("age_minutes")
                    or cand.get("_age_minutes"),
                    "bonding_progress": bond,
                    "priceChange": (result.get("market") or {}).get("priceChange")
                    or {},
                    "txns_m5": (result.get("market") or {}).get("txns_m5") or {},
                    "txActivity": (result.get("alphaSetup") or {}).get("txActivity")
                    or {},
                    "deepAnalysis": result.get("deepAnalysis") or {},
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
                    "migrationPath": mig,
                    "pumpfun": {
                        "ath_market_cap": ath_mcap,
                        "usd_market_cap": mcap,
                        "bonding_progress": bond,
                    },
                    "checkerHub": checker_hub,
                    "sixkRadar": column == "sixk_radar"
                    or SIXK_RADAR_MIN_USD <= mcap <= SIXK_RADAR_MAX_USD,
                    "entrySweet": SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX,
                    "tradePlan": result.get("tradePlan") or {},
                    "migrationLane": lane or out_col,
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
        col = r.get("column") or "new"
        if col not in analyzed_columns:
            analyzed_columns[col] = []
        analyzed_columns[col].append(r)

    for col in analyzed_columns:
        def _alpha_rank(t: dict) -> int:
            a = (t.get("alphaSetup") or {}).get("tier") or ""
            return {
                "MEGA_MOON": 0,
                "MOON_SETUP": 1,
                "ALPHA": 2,
                "WATCH_ALPHA": 3,
                "SPEC": 4,
                "WEAK": 5,
                "SKIP": 6,
            }.get(a, 5)

        # Near-migration: rank by bonding + migration score first
        if col in ("almost_bonded", "under_25k"):
            analyzed_columns[col].sort(
                key=lambda t: (
                    -(t.get("migrationPath") or {}).get("score", 0),
                    -(float(t.get("bonding_progress") or 0)),
                    _alpha_rank(t),
                    {"SAFE_ENTRY": 0, "WATCH": 1, "CAUTION": 2, "HIGH_RISK": 3, "AVOID": 4, "UNSAFE": 5}.get(
                        t.get("safetyTier"), 9
                    ),
                    -t.get("safetyScore", 0),
                )
            )
        else:
            analyzed_columns[col].sort(
                key=lambda t: (
                    _alpha_rank(t),
                    0 if (t.get("mcap_usd") or 0) <= SCAN_MCAP_FOCUS_MAX_USD else 1,
                    float(t.get("age_minutes") or 999),
                    {"SAFE_ENTRY": 0, "WATCH": 1, "CAUTION": 2, "HIGH_RISK": 3, "AVOID": 4, "UNSAFE": 5}.get(
                        t.get("safetyTier"), 9
                    ),
                    -(t.get("alphaSetup") or {}).get("score", 0),
                    -t.get("safetyScore", 0),
                    t.get("mcap_usd") or 0,
                )
            )

    all_tokens = (
        analyzed_columns.get("almost_bonded", [])
        + analyzed_columns.get("under_25k", [])
        + analyzed_columns.get("sixk_radar", [])
        + analyzed_columns.get("new", [])
        + analyzed_columns.get("recently_bonded", [])
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
            0 if (t.get("alphaSetup") or {}).get("tier") == "MEGA_MOON" else 1,
            0 if (t.get("alphaSetup") or {}).get("tier") == "MOON_SETUP" else 2,
            0 if (t.get("alphaSetup") or {}).get("is_alpha") else 3,
            -(t.get("alphaSetup") or {}).get("score", 0),
            -(t.get("socialSignals") or {}).get("highlight", False),
            -t["safetyScore"],
            t.get("mcap_usd") or 0,
        )
    )

    alpha_picks = [
        t for t in all_tokens
        if (t.get("alphaSetup") or {}).get("tier") in (
            "MEGA_MOON", "MOON_SETUP", "ALPHA", "WATCH_ALPHA"
        )
    ]
    alpha_picks.sort(
        key=lambda t: (
            0 if (t.get("migrationPath") or {}).get("lane") == "near_migration" else 1,
            0 if (t.get("alphaSetup") or {}).get("tier") == "MEGA_MOON" else 1,
            0 if (t.get("alphaSetup") or {}).get("tier") == "MOON_SETUP" else 2,
            -(t.get("migrationPath") or {}).get("score", 0),
            -(t.get("alphaSetup") or {}).get("score", 0),
            t.get("mcap_usd") or 0,
        )
    )

    # Attach runner scores early so dump/lottery filters can use them
    for t in all_tokens:
        t["runnerRadar"] = score_runner_candidate(t)

    def _not_dumped(t: dict) -> bool:
        """User rule: never show tokens that already dumped."""
        crashed, _ = is_crashed_runner(t)
        if crashed:
            return False
        rr = t.get("runnerRadar") or {}
        if rr.get("crashed") or rr.get("stage") == "crashed":
            return False
        peak = max(
            float(t.get("_peak_mcap") or 0),
            float(t.get("ath_mcap") or 0),
            float((t.get("pumpfun") or {}).get("ath_market_cap") or 0),
        )
        mcap = float(t.get("mcap_usd") or 0)
        if peak >= 3_000 and mcap > 0 and mcap < peak * DUMP_HIDE_FRAC:
            return False
        # Price dump candles
        pc = t.get("priceChange") or {}
        if float(pc.get("m5") or 0) <= -22 or float(pc.get("h1") or 0) <= -28:
            return False
        # Deep analysis already said dump
        if (t.get("deepAnalysis") or {}).get("dump", {}).get("is_dumped"):
            return False
        if (t.get("deepAnalysis") or {}).get("verdict") == "SKIP" and (
            t.get("deepAnalysis") or {}
        ).get("dump", {}).get("dump_pct_from_ath", 0) >= 25:
            return False
        return True

    # Strip dumps from every column before any picks
    for col in list(analyzed_columns.keys()):
        analyzed_columns[col] = [t for t in analyzed_columns[col] if _not_dumped(t)]
    all_tokens = [t for t in all_tokens if _not_dumped(t)]

    # Near migration — strict quality (user losses on weak recs)
    def _quality_near_mig(t: dict) -> bool:
        if not _is_near_migration_token(t) or not _not_dumped(t):
            return False
        mcap = float(t.get("mcap_usd") or 0)
        if mcap < NEAR_MIG_MIN_MCAP:
            return False
        bond = float(t.get("bonding_progress") or 0)
        mig_s = int((t.get("migrationPath") or {}).get("score") or 0)
        ath = float(t.get("ath_mcap") or 0)
        if ath >= 3_000 and mcap < ath * NEAR_ATH_BUY_FRAC:
            return False  # faded from ATH — not a buy path
        deep = t.get("deepAnalysis") or {}
        if deep.get("verdict") == "SKIP":
            return False
        tx = t.get("txActivity") or (t.get("alphaSetup") or {}).get("txActivity") or {}
        if tx.get("tilt") == "DOWN" or tx.get("zone") in ("dead", "wash", "one_way"):
            return False
        return bond >= MIGRATION_NEAR_MIN_PCT or mig_s >= 55

    migration_picks = [t for t in all_tokens if _quality_near_mig(t)]
    # Prefer true BUY-gate tokens first
    migration_picks.sort(
        key=lambda t: (
            0 if (t.get("deepAnalysis") or {}).get("buy_ready") else 1,
            0 if (t.get("investSignal") == "STRONG_INVEST") else 1,
            -int((t.get("migrationPath") or {}).get("score") or 0),
            -float(t.get("bonding_progress") or 0),
            -int((t.get("txActivity") or {}).get("score") or 0),
            -t.get("safetyScore", 0),
        )
    )
    # Pin near-migration so a missed poll doesn't wipe the section
    try:
        sticky_near = await _pin_near_migration_tokens(
            migration_picks + (analyzed_columns.get("almost_bonded") or [])
        )
        # Drop dumps from sticky before merge
        sticky_near = [t for t in sticky_near if _not_dumped(t)]
        migration_picks = [
            t for t in _merge_sticky_near_mig(migration_picks, sticky_near) if _not_dumped(t)
        ]
        ab = analyzed_columns.get("almost_bonded") or []
        analyzed_columns["almost_bonded"] = [
            t for t in _merge_sticky_near_mig(ab, sticky_near) if _not_dumped(t)
        ]
    except Exception as exc:
        logger.debug("near-mig sticky failed: %s", exc)
    under25k_picks = [
        t
        for t in all_tokens
        if _not_dumped(t)
        and (
            (t.get("migrationPath") or {}).get("lane") == "under_25k"
            or (
                UNDER25K_MIN_USD
                <= float(t.get("mcap_usd") or 0)
                <= UNDER25K_MAX_USD
            )
        )
        and (t.get("migrationPath") or {}).get("lane") != "near_migration"
    ]
    under25k_picks.sort(
        key=lambda t: (
            -(t.get("migrationPath") or {}).get("score", 0),
            -(t.get("alphaSetup") or {}).get("score", 0),
            -float(t.get("bonding_progress") or 0),
        )
    )
    # Early lottery: quality only + never invest; most die under $7k
    early_lottery = []
    for t in all_tokens:
        mcap = float(t.get("mcap_usd") or 0)
        bond = float(t.get("bonding_progress") or 0)
        if not (0 < mcap < UNDER25K_MIN_USD and bond < MIGRATION_NEAR_MIN_PCT):
            if (t.get("migrationPath") or {}).get("lane") != "early_lottery":
                continue
        if not _not_dumped(t):
            continue
        # Require at least one real signal or hide garbage
        alpha = t.get("alphaSetup") or {}
        social = t.get("socialSignals") or {}
        if (
            (alpha.get("score") or 0) < 48
            and not social.get("highlight")
            and not (alpha.get("megaFingerprint") or {}).get("score", 0) >= 50
        ):
            continue
        # Force non-invest presentation
        t = dict(t)
        t["investSignal"] = "WATCH"
        t["investSummary"] = (
            f"Early lottery ${mcap:,.0f} — most die under $7k. "
            "No ENTER; watch structure only."
        )
        if t.get("tradePlan"):
            tp = dict(t["tradePlan"])
            if tp.get("action") == "ENTER":
                tp["action"] = "WATCH"
                tp["summary"] = t["investSummary"]
            t["tradePlan"] = tp
        early_lottery.append(t)
    early_lottery.sort(
        key=lambda t: (
            -(t.get("alphaSetup") or {}).get("score", 0),
            float(t.get("age_minutes") or 999),
        )
    )
    early_lottery = early_lottery[:8]

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
        # Order: migration first (what can graduate), then under $25k, then lottery
        "migration_picks": migration_picks[:20],
        "under25k_picks": under25k_picks[:12],
        "early_lottery": early_lottery[:12],
        "safe_picks": safe_picks[:10],
        "alpha_picks": alpha_picks[:12],
        "sixk_picks": sixk_sweet[:15] or sixk_live[:15],
        "narrative_picks": narrative_picks[:15],
        "runner_alerts": [],  # filled below after sticky store update
        "counts": {
            "sixk_radar": len(analyzed_columns.get("sixk_radar", [])),
            "new": len(analyzed_columns.get("new", [])),
            "under_25k": len(analyzed_columns.get("under_25k", [])),
            "almost_bonded": len(analyzed_columns.get("almost_bonded", [])),
            "recently_bonded": len(analyzed_columns.get("recently_bonded", [])),
            "total": len(all_tokens),
            "migration_picks": len(migration_picks),
            "under25k_picks": len(under25k_picks),
            "early_lottery": len(early_lottery),
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
            "migration_mcap_max_usd": MIGRATION_MCAP_MAX_USD,
            "graduation_mcap_usd": GRADUATION_MCAP_USD,
            "sixk_band": f"${SIXK_RADAR_MIN_USD:,.0f}–${SIXK_RADAR_MAX_USD:,.0f}",
        },
        "checker_picks": checker_pass[:12],
        "learning": _learning_memory.get_outcomes_summary(),
        "disclaimer": (
            "Sections: Near Migration · Under $25k · Early Lottery. "
            "Runner radar alerts multi-stage $10M-path candidates. Not financial advice."
        ),
    }
    # runnerRadar already attached above
    _trenches_cache.update({"key": cache_key, "data": response, "ts": time.time()})
    try:
        alerts = await _refresh_runner_alerts_from_cache()
        response["runner_alerts"] = alerts[:15]
        response["counts"]["runner_alerts"] = len(alerts)
        # Promote high-score runner alerts into migration_picks if missing
        alert_mints = {a["tokenAddress"] for a in alerts}
        have = {t.get("tokenAddress") for t in migration_picks}
        for a in alerts:
            if a["tokenAddress"] in have:
                continue
            # Find full token
            full = next(
                (t for t in all_tokens if t.get("tokenAddress") == a["tokenAddress"]),
                None,
            )
            if full and (full.get("runnerRadar") or {}).get("score", 0) >= 55:
                migration_picks.insert(0, full)
        response["migration_picks"] = migration_picks[:20]
        response["counts"]["migration_picks"] = len(migration_picks)
        response["counts"]["near_mig_sticky"] = len(_near_mig_store)
        _ = alert_mints  # silence lint
    except Exception as exc:
        logger.debug("runner alerts attach failed: %s", exc)
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


@app.get("/api/runner-radar")
async def runner_radar():
    """Sticky multi-stage alerts for $10M–$100M path candidates.

    Poll this every ~10s from the UI for browser notifications.
    """
    alerts = await _refresh_runner_alerts_from_cache()
    new_only = [a for a in alerts if a.get("is_new_alert")]
    return {
        "ok": True,
        "alerts": alerts[:20],
        "new_alerts": new_only[:10],
        "count": len(alerts),
        "new_count": len(new_only),
        "ts": time.time(),
        "hint": (
            "Multi-stage: early structure · mid climb · near migration · post-migration. "
            "Enable browser notifications in the UI."
        ),
    }


@app.get("/api/learning/stats")
async def learning_stats():
    """How many tokens learned + outcome breakdown."""
    from services.learning.mega_seeds import MEGA_SEEDS, MEGA_SEEDS_VERSION

    summary = _learning_memory.get_outcomes_summary()
    recent = _learning_memory.recent_finalized(20)
    mega_recent = [
        r
        for r in _learning_memory.recent_finalized(80)
        if r.get("outcome") in ("MEGA", "SUPER")
        or float(r.get("ath_mcap") or 0) >= 1_000_000
    ][:15]
    return {
        "ok": True,
        "summary": summary,
        "recent": recent,
        "base_rates": _learning_memory.outcome_base_rates(),
        "model": "likelihood_ratio_v2",
        "model_version": _learning_memory.get_meta("learn_model_version"),
        "mega_seeds": {
            "version": MEGA_SEEDS_VERSION,
            "applied": _learning_memory.get_meta("mega_seeds_version"),
            "catalog_size": len(MEGA_SEEDS),
            "in_db": mega_recent,
        },
        "db": str(BASE_DIR / "data" / "learning.db"),
    }


@app.post("/api/learning/reseed")
async def learning_reseed(force: bool = Query(False)):
    """Re-apply historical mega + scam seeds into the learning DB."""
    n = _learning.seed_known_examples(force=force)
    return {
        "ok": True,
        "seeded": n,
        "summary": _learning_memory.get_outcomes_summary(),
        "version": _learning_memory.get_meta("mega_seeds_version"),
    }


@app.post("/api/learning/rebuild")
async def learning_rebuild():
    """Recompute feature→outcome table from all finalized tokens (accuracy refresh)."""
    rebuilt = _learning_memory.rebuild_feature_stats()
    _learning_memory.set_meta("learn_model_version", "learn_lr_v2_2026_07")
    return {
        "ok": True,
        "rebuilt": rebuilt,
        "summary": _learning_memory.get_outcomes_summary(),
        "base_rates": _learning_memory.outcome_base_rates(),
        "model": "likelihood_ratio_v2",
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