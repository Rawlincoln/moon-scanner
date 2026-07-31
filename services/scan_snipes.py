"""Safe-snipe scan pipeline — 2× take-profit band with capital filters."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any

from config import MIGRATION_MCAP_MAX_USD
from services.realtime_bus import realtime_bus
from services.safe_snipes import (
    MAX_AGE_MIN,
    MIN_AGE_MIN,
    SNIPE_MCAP_MAX,
    SNIPE_MCAP_MIN,
    TARGET_MULT,
    evaluate_snipe,
    filter_and_rank_snipes,
    snipe_card_from_coin,
    snipe_reject_reason,
)
from services.scan_moon import enrich_moon_card
from services.snipe_outcomes import get_snipe_outcomes
from services.padre_feed import PadreFeedClient
from services.pumpfun import PumpFunClient

logger = logging.getLogger("moon-scanner.scan_snipes")

SNIPE_CACHE_TTL = 12.0
_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_lock = asyncio.Lock()
_pump = PumpFunClient()
_padre_feed = PadreFeedClient()


async def _discover_snipe_coins() -> list[dict]:
    """Merge pump.fun lists so the $3.5k–$16k band is not missed.

    ``last_trade_timestamp`` alone skews old/high-mcap; ``created_timestamp``
    catches fresh launches; ``market_cap`` helps mid-band climbers.
    """
    sorts = ("created_timestamp", "last_trade_timestamp", "market_cap")
    batches = await asyncio.gather(
        *[_padre_feed._fetch_pump_sorted(s, 80) for s in sorts],
        return_exceptions=True,
    )
    merged: list[dict] = []
    seen: set[str] = set()
    for batch in batches:
        if isinstance(batch, Exception) or not isinstance(batch, list):
            continue
        for coin in batch:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            merged.append(coin)

    if len(merged) < 40:
        try:
            latest = await _pump.get_latest_coins(limit=80)
        except Exception:
            latest = []
        for coin in latest or []:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            merged.append(coin)

    return merged


def _short_reject(reason: str | None) -> str:
    if not reason:
        return "other"
    r = reason.lower()
    if "below" in r and "band" in r:
        return "mcap_low"
    if "above" in r and "band" in r:
        return "mcap_high"
    if "too fresh" in r or "sniper window" in r:
        return "too_fresh"
    if "too old" in r:
        return "too_old"
    if "faded" in r or "dump" in r or "dumped" in r:
        return "dumped"
    if "bundled" in r:
        return "bundled"
    if "sniper" in r or "max wallet" in r:
        return "snipers"
    if "honeypot" in r or "rugged" in r:
        return "safety"
    if "2× target too high" in r or "2x target too high" in r:
        return "target_too_high"
    if "avoid" in r or "blocklist" in r:
        return "avoid"
    return "other"


async def scan_safe_snipes(
    *,
    limit: int = 12,
    max_age_minutes: float = 60.0,
    force: bool = False,
) -> dict[str, Any]:
    now = time.time()
    if (
        not force
        and _cache.get("data")
        and now - float(_cache.get("ts") or 0) < SNIPE_CACHE_TTL
    ):
        cached = dict(_cache["data"])
        cached["cached"] = True
        return cached

    async with _lock:
        now = time.time()
        if (
            not force
            and _cache.get("data")
            and now - float(_cache.get("ts") or 0) < SNIPE_CACHE_TTL
        ):
            cached = dict(_cache["data"])
            cached["cached"] = True
            return cached

        age_cap = min(float(max_age_minutes), MAX_AGE_MIN + 5)
        coins = await _discover_snipe_coins()
        if not isinstance(coins, list):
            coins = []

        rt = set(realtime_bus.priority_mints(limit=30, max_age_sec=240))
        if rt:
            coins = sorted(
                coins, key=lambda c: (0 if str(c.get("mint") or "") in rt else 1,)
            )

        seen: set[str] = set()
        cards: list[dict] = []
        scanned = 0
        rejected = 0
        pre_reject: Counter[str] = Counter()
        mcap_lo = SNIPE_MCAP_MIN * 0.85
        mcap_hi = min(SNIPE_MCAP_MAX * 1.25, MIGRATION_MCAP_MAX_USD)

        for coin in coins:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            scanned += 1
            age = PumpFunClient.coin_age_minutes(coin)
            if age > age_cap or age < MIN_AGE_MIN:
                rejected += 1
                pre_reject["age"] += 1
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            if mcap < mcap_lo or mcap > mcap_hi:
                rejected += 1
                pre_reject["mcap"] += 1
                continue
            card = snipe_card_from_coin(coin)
            if not card:
                rejected += 1
                pre_reject["card"] += 1
                continue
            if mint in rt:
                card["realtime"] = True
            cards.append(card)

        # Prefer mid-band / near-ATH before enrich
        def _prio(c: dict) -> float:
            m = float(c.get("mcap_usd") or 0)
            a = float(c.get("ath_mcap") or 0)
            ret = (m / a) if a > 0 else 0.8
            return ret * 40 - abs(m - 7_000) / 400 + min(float(c.get("age_minutes") or 0), 30)

        cards.sort(key=_prio, reverse=True)
        enrich_n = min(len(cards), max(limit * 4, 20) + (6 if rt else 0))
        to_enrich = cards[:enrich_n]
        rest = cards[enrich_n:]

        sem = asyncio.Semaphore(6)

        async def _one(c: dict) -> dict:
            async with sem:
                return await enrich_moon_card(c, skip_narrative_gate=True)

        enriched = list(await asyncio.gather(*[_one(c) for c in to_enrich]))
        accurate: list[dict] = []
        post_reject: Counter[str] = Counter()
        near_misses: list[dict] = []

        def _consume(batch: list) -> None:
            nonlocal rejected
            for c in batch:
                mint = (c.get("tokenAddress") or c.get("mint") or "").strip()
                if c.get("enrich_ok") is not True:
                    rejected += 1
                    post_reject["enrich"] += 1
                    if len(near_misses) < 10:
                        errs = c.get("enrich_errors") or ["incomplete"]
                        near_misses.append(
                            {
                                "symbol": c.get("symbol") or "?",
                                "name": c.get("name") or "",
                                "tokenAddress": mint,
                                "mcap_usd": c.get("mcap_usd"),
                                "ath_mcap": c.get("ath_mcap"),
                                "age_minutes": c.get("age_minutes"),
                                "reject": "safety unknown — "
                                + ", ".join(str(e) for e in errs[:2]),
                                "reject_key": "enrich",
                            }
                        )
                    continue
                reason = snipe_reject_reason(c)
                if reason:
                    rejected += 1
                    key = _short_reject(reason)
                    post_reject[key] += 1
                    if len(near_misses) < 10:
                        near_misses.append(
                            {
                                "symbol": c.get("symbol") or "?",
                                "name": c.get("name") or "",
                                "tokenAddress": mint,
                                "mcap_usd": c.get("mcap_usd"),
                                "ath_mcap": c.get("ath_mcap"),
                                "age_minutes": c.get("age_minutes"),
                                "reject": reason,
                                "reject_key": key,
                            }
                        )
                    continue
                accurate.append(c)

        _consume(enriched)

        # Second wave when first pass found nothing
        if not accurate and rest:
            wave2_n = min(len(rest), max(limit * 3, 12))
            wave2 = rest[:wave2_n]
            rest = rest[wave2_n:]
            enriched2 = list(await asyncio.gather(*[_one(c) for c in wave2]))
            enrich_n += wave2_n
            post_reject["second_wave_enrich"] += wave2_n
            _consume(enriched2)

        if rest:
            post_reject["not_enriched_overflow"] += len(rest)

        gates = {"min_score": 55, "adapted": False, "sample_n": 0, "reasons": ["defaults"]}
        try:
            gates = get_snipe_outcomes().suggested_gates()
        except Exception as exc:
            logger.debug("snipe gates failed: %s", exc)

        display = filter_and_rank_snipes(
            accurate,
            min_score=int(gates.get("min_score") or 55),
            limit=limit,
            require_holders=True,
        )
        for t in display:
            if not t.get("snipe"):
                t["snipe"] = evaluate_snipe(t)

        try:
            sn = get_snipe_outcomes().record_shown(display)
            if sn:
                logger.info("Snipe outcomes recorded %s", sn)
        except Exception as exc:
            logger.debug("snipe outcomes record failed: %s", exc)

        # Train learning model on shown snipes
        try:
            from services.scan_moon import get_learning

            eng = get_learning()
            if eng is not None and display:
                eng.observe_feed_cards(display, source="snipes", limit=limit)
        except Exception as exc:
            logger.debug("learning observe snipes failed: %s", exc)

        reject_breakdown = {
            **{f"pre_{k}": v for k, v in pre_reject.items()},
            **{f"post_{k}": v for k, v in post_reject.items()},
        }

        payload = {
            "ok": True,
            "mode": "safe_snipes_2x",
            "scanned_at": time.time(),
            "cached": False,
            "tokens": display,
            "near_misses": near_misses,
            "counts": {
                "shown": len(display),
                "snipe": sum(1 for t in display if t.get("snipe_label") == "SNIPE"),
                "setup": sum(1 for t in display if t.get("snipe_label") == "SETUP"),
                "candidates_raw": scanned,
                "band_hits": len(cards),
                "enriched": enrich_n,
                "rejected": rejected,
            },
            "reject_breakdown": reject_breakdown,
            "gates": gates,
            "band": {
                "mcap_min": SNIPE_MCAP_MIN,
                "mcap_max": SNIPE_MCAP_MAX,
                "target_mult": TARGET_MULT,
                "max_bundled_pct_snipe": 5.0,
                "max_bundled_pct_setup": 8.0,
                "min_ath_retention": 0.80,
            },
            "rule": (
                "Safe snipes for ~2× only. Entry $3.5k–$16k mcap. "
                "SNIPE: holders known + bundled ≤5% + clean snipers. "
                "SETUP: up to ~8% bundle with holders (smaller size). "
                f"No dump from ATH, age {MIN_AGE_MIN:.1f}–{MAX_AGE_MIN:.0f}m. "
                "Learning soft-rank + adaptive min_score. Empty list is normal."
            ),
        }
        _cache["data"] = payload
        _cache["ts"] = time.time()
        return payload
