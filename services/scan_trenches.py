"""Padre trenches scan pipeline — extracted from main.py (behavior-preserving).

Legacy product surface; moon UI prefers /api/moon.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from config import (
    BACKGROUND_SCAN_INTERVAL_SEC,
    BACKGROUND_SCAN_PER_COLUMN,
    DEFAULT_MAX_AGE_MINUTES,
    DUMP_HIDE_FRAC,
    GRADUATION_MCAP_USD,
    MIGRATION_MCAP_MAX_USD,
    MIGRATION_NEAR_MIN_PCT,
    NEAR_ATH_BUY_FRAC,
    NEAR_MIG_MIN_MCAP,
    NEAR_MIGRATION_MAX_STICKY,
    NEAR_MIGRATION_STICKY_TTL_SEC,
    REQUEST_TIMEOUT,
    RUNNER_ALERT_TTL_SEC,
    RUNNER_RADAR_INTERVAL_SEC,
    SCAN_MCAP_FOCUS_MAX_USD,
    SCAN_MCAP_MAX_USD,
    SIXK_ENTRY_SWEET_MAX,
    SIXK_ENTRY_SWEET_MIN,
    SIXK_RADAR_MAX_USD,
    SIXK_RADAR_MIN_USD,
    TRENCHES_CACHE_TTL,
    TRENCHES_CONCURRENCY,
    UNDER25K_MAX_USD,
    UNDER25K_MIN_USD,
    USER_AGENT,
)
from services.analyze_token import analyze_token as _analyze_token
from services.avoid_filters import BLOCKED_MINTS
from services.checker_hub import run_checker_hub
from services.padre import PadreClient
from services.padre_feed import PadreFeedClient
from services.pumpfun import PumpFunClient
from services.runner_radar import (
    build_runner_alerts,
    extract_ath_mcap,
    is_crashed_runner,
    score_runner_candidate,
)
from services.safety_report import build_safety_report

logger = logging.getLogger("moon-scanner.scan_trenches")

_padre = PadreClient()
_padre_feed = PadreFeedClient()
_learning_memory: Any = None


def bind_learning_memory(memory: Any) -> None:
    """Wire LearningMemory for trenches response learning summary."""
    global _learning_memory
    _learning_memory = memory


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
            if mint in BLOCKED_MINTS:
                _near_mig_store.pop(mint, None)
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

        # Expire old pins + re-check crash / blocklist on stored snapshots
        dead: list[str] = []
        for m, v in _near_mig_store.items():
            if m in BLOCKED_MINTS:
                dead.append(m)
                continue
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
    """Enrich live tokens with sticky metadata only.

    Never re-inject sticky-only mints — that resurrected dumps with stale high mcap.
    """
    sticky_by: dict[str, dict] = {
        t.get("tokenAddress"): t
        for t in sticky
        if t.get("tokenAddress") and t.get("tokenAddress") not in BLOCKED_MINTS
    }
    out: list[dict[str, Any]] = []
    for t in live:
        m = t.get("tokenAddress")
        if not m or m in BLOCKED_MINTS:
            continue
        prev = sticky_by.get(m) or {}
        merged = dict(t)
        if prev:
            merged["_sticky_near_mig"] = True
            merged["_first_seen"] = prev.get("_first_seen")
            merged["_last_seen"] = time.time()
            merged["_pinned_sec"] = int(
                time.time() - float(prev.get("_first_seen") or time.time())
            )
            # Carry historical peak so dump filter still works on live mcap
            peak = max(
                float(prev.get("_peak_mcap") or 0),
                float(merged.get("_peak_mcap") or 0),
                float(merged.get("ath_mcap") or 0),
                extract_ath_mcap(merged),
            )
            merged["_peak_mcap"] = peak
            if peak > 0:
                merged["ath_mcap"] = max(float(merged.get("ath_mcap") or 0), peak)
            crashed, _ = is_crashed_runner(merged)
            if crashed:
                continue
        out.append(merged)
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



_trenches_cache: dict[str, Any] = {"key": None, "data": None, "ts": 0}
_trenches_lock = asyncio.Lock()
_trenches_refreshing = False


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
    ath = float(
        cand.get("_ath_mcap")
        or cand.get("ath_market_cap")
        or pf.get("ath_market_cap")
        or 0
    )
    return {
        "column": column,
        "chainId": "solana",
        "tokenAddress": mint,
        "name": pf.get("name") or cand.get("name") or "Unknown",
        "symbol": pf.get("symbol") or cand.get("symbol") or "?",
        "icon": cand.get("icon") or pf.get("image_uri"),
        "mcap_usd": mcap,
        "ath_mcap": ath or None,
        "ath_market_cap": ath or None,
        "age_minutes": cand.get("_age_minutes") or cand.get("age_minutes"),
        "bonding_progress": bond,
        "safetyTier": "SCANNING",
        "safetyScore": 0,
        "safetyReport": {
            "verdict": verdict,
            "bundle": {"bundled": False},
            "snipers": {},
        },
        "pumpfun": {
            "usd_market_cap": mcap,
            "ath_market_cap": ath or None,
            "name": pf.get("name"),
            "symbol": pf.get("symbol"),
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
    columns = await _padre_feed.fetch_trenches_columns(
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
    columns = await _padre_feed.fetch_trenches_columns(
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
            pre = _padre_feed._candidate_mcap(cand)
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
        mcap = _padre_feed._candidate_mcap(cand)
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
        "padre_url": _padre.trenches_url(),
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
        "learning": (_learning_memory.get_outcomes_summary() if _learning_memory else {}),
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
            if a.get("tokenAddress") in BLOCKED_MINTS:
                continue
            # Find full token
            full = next(
                (t for t in all_tokens if t.get("tokenAddress") == a["tokenAddress"]),
                None,
            )
            if (
                full
                and _not_dumped(full)
                and (full.get("runnerRadar") or {}).get("score", 0) >= 55
            ):
                migration_picks.insert(0, full)
        # Final hard strip — never ship dumps in any list
        for key in (
            "migration_picks",
            "under25k_picks",
            "early_lottery",
            "alpha_picks",
            "safe_picks",
            "sixk_picks",
            "checker_picks",
            "runner_alerts",
        ):
            items = response.get(key) or []
            response[key] = [t for t in items if _not_dumped(t) and t.get("tokenAddress") not in BLOCKED_MINTS]
        response["migration_picks"] = response["migration_picks"][:20]
        response["counts"]["migration_picks"] = len(response["migration_picks"])
        response["counts"]["near_mig_sticky"] = len(_near_mig_store)
        _ = alert_mints  # silence lint
    except Exception as exc:
        logger.debug("runner alerts attach failed: %s", exc)
    _trenches_cache.update({"key": cache_key, "data": response, "ts": time.time()})
    return response




def trenches_health_snapshot() -> dict[str, Any]:
    """Fields for /api/health."""
    cache_age = (
        time.time() - _trenches_cache.get("ts", 0)
        if _trenches_cache.get("data")
        else None
    )
    return {
        "trenches_cached": bool(_trenches_cache.get("data")),
        "trenches_refreshing": _trenches_refreshing,
        "cache_age_sec": round(cache_age, 1) if cache_age is not None else None,
        "runner_alerts": len(_runner_alert_store),
    }


# Public names used by routes / main lifespan
analyze_trenches = _analyze_trenches
fetch_trenches_feed = _fetch_trenches_feed
refresh_runner_alerts = _refresh_runner_alerts_from_cache
preview_from_candidate = _preview_from_candidate
background_trenches_warm = _background_trenches_warm
background_runner_alert_loop = _background_runner_alert_loop
