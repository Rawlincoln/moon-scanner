"""Organic Heat scan — high-recall companion to strict Moons feed."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any

from config import MIGRATION_MCAP_MAX_USD
from services.organic_heat import (
    HEAT_MCAP_MAX,
    HEAT_MCAP_MIN,
    MAX_AGE_MIN,
    MIN_AGE_MIN,
    evaluate_heat,
    filter_and_rank_heat,
    heat_card_from_coin,
    heat_reject_reason,
)
from services.padre_feed import PadreFeedClient
from services.pumpfun import PumpFunClient
from services.realtime_bus import realtime_bus
from services.scan_moon import enrich_moon_card

logger = logging.getLogger("moon-scanner.scan_heat")

HEAT_CACHE_TTL = 12.0
_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_lock = asyncio.Lock()
_pump = PumpFunClient()
_padre_feed = PadreFeedClient()


async def _discover_heat_coins() -> list[dict]:
    sorts = ("created_timestamp", "last_trade_timestamp", "market_cap")
    batches = await asyncio.gather(
        *[_padre_feed._fetch_pump_sorted(s, 100) for s in sorts],
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
    if len(merged) < 50:
        try:
            latest = await _pump.get_latest_coins(limit=100)
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
    if "too small" in r:
        return "mcap_low"
    if "too large" in r:
        return "mcap_high"
    if "too fresh" in r:
        return "too_fresh"
    if "too old" in r:
        return "too_old"
    if "dump" in r or "crashed" in r:
        return "dumped"
    if "honeypot" in r or "rugged" in r:
        return "honeypot"
    if "bundle" in r or "sniper" in r:
        return "bundle"
    if "hard avoid" in r or "blocklist" in r:
        return "avoid"
    if "heat" in r:
        return "low_heat"
    if "enrich" in r or "safety" in r:
        return "enrich"
    return "other"


def _prio(c: dict) -> float:
    m = float(c.get("mcap_usd") or 0)
    a = float(c.get("ath_mcap") or 0)
    ret = (m / a) if a > 0 else 0.8
    replies = int((c.get("pumpfun") or {}).get("reply_count") or 0)
    social = c.get("socialSignals") or {}
    edge = float(social.get("edge_score") or 0)
    boost = 12 if c.get("realtime") else 0
    if social.get("has_edge") or social.get("influencer_tweet"):
        boost += 10
    return ret * 35 + min(replies, 50) * 0.5 + min(edge, 40) * 0.25 + boost - abs(m - 12_000) / 800


async def scan_organic_heat(
    *,
    limit: int = 16,
    max_age_minutes: float = 120.0,
    force: bool = False,
) -> dict[str, Any]:
    now = time.time()
    if (
        not force
        and _cache.get("data")
        and now - float(_cache.get("ts") or 0) < HEAT_CACHE_TTL
    ):
        cached = dict(_cache["data"])
        cached["cached"] = True
        return cached

    async with _lock:
        now = time.time()
        if (
            not force
            and _cache.get("data")
            and now - float(_cache.get("ts") or 0) < HEAT_CACHE_TTL
        ):
            cached = dict(_cache["data"])
            cached["cached"] = True
            return cached

        age_cap = min(float(max_age_minutes), MAX_AGE_MIN)
        coins = await _discover_heat_coins()
        if not isinstance(coins, list):
            coins = []

        rt = set(realtime_bus.priority_mints(limit=40, max_age_sec=300))
        if rt:
            coins = sorted(
                coins, key=lambda c: (0 if str(c.get("mint") or "") in rt else 1,)
            )

        seen: set[str] = set()
        cards: list[dict] = []
        scanned = 0
        rejected = 0
        pre_reject: Counter[str] = Counter()
        mcap_lo = HEAT_MCAP_MIN * 0.9
        mcap_hi = min(HEAT_MCAP_MAX * 1.1, MIGRATION_MCAP_MAX_USD * 1.2)

        for coin in coins:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            scanned += 1
            age = PumpFunClient.coin_age_minutes(coin)
            if age > age_cap or age < MIN_AGE_MIN * 0.6:
                rejected += 1
                pre_reject["age"] += 1
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            if mcap < mcap_lo or mcap > mcap_hi:
                rejected += 1
                pre_reject["mcap"] += 1
                continue
            card = heat_card_from_coin(coin)
            if not card:
                rejected += 1
                pre_reject["card"] += 1
                continue
            if mint in rt:
                card["realtime"] = True
            cards.append(card)

        cards.sort(key=_prio, reverse=True)
        # Higher enrich budget than moons — recall mode
        enrich_n = min(len(cards), max(limit * 4, 28) + (8 if rt else 0))
        to_enrich = cards[:enrich_n]
        rest = cards[enrich_n:]

        sem = asyncio.Semaphore(8)

        async def _one(c: dict) -> dict:
            async with sem:
                # Skip moon narrative gate — heat does not require influencer edge
                return await enrich_moon_card(c, skip_narrative_gate=True)

        enriched = list(await asyncio.gather(*[_one(c) for c in to_enrich]))
        accurate: list[dict] = []
        post_reject: Counter[str] = Counter()
        near_misses: list[dict] = []

        def _consume(batch: list) -> None:
            nonlocal rejected
            for c in batch:
                mint = (c.get("tokenAddress") or c.get("mint") or "").strip()
                # Heat allows incomplete enrich as RISKY if hard reject fails
                reason = heat_reject_reason(c)
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
                # Soft-skip if score will fail — still add for filter
                accurate.append(c)

        _consume(enriched)

        if not accurate and rest:
            wave2_n = min(len(rest), max(limit * 3, 16))
            wave2 = rest[:wave2_n]
            rest = rest[wave2_n:]
            enriched2 = list(await asyncio.gather(*[_one(c) for c in wave2]))
            enrich_n += wave2_n
            post_reject["second_wave_enrich"] += wave2_n
            _consume(enriched2)

        if rest:
            post_reject["not_enriched_overflow"] += len(rest)

        # Also try pre-enrich rejects that only failed enrich — already in accurate if pass
        display = filter_and_rank_heat(accurate, min_score=36, limit=limit)
        for t in display:
            if not t.get("heat"):
                t["heat"] = evaluate_heat(t)

        # Observe shown heat for learning (optional)
        try:
            from services.scan_moon import get_learning

            eng = get_learning()
            if eng is not None and display:
                # Only train on enrich_ok HEAT/WARM to avoid garbage
                train = [
                    t
                    for t in display
                    if t.get("enrich_ok") is True
                    and t.get("heat_label") in ("HEAT", "WARM")
                ]
                if train:
                    eng.observe_feed_cards(train, source="heat", limit=limit)
        except Exception as exc:
            logger.debug("learning observe heat failed: %s", exc)

        reject_breakdown = {
            **{f"pre_{k}": v for k, v in pre_reject.items()},
            **{f"post_{k}": v for k, v in post_reject.items()},
        }

        payload = {
            "ok": True,
            "mode": "organic_heat_high_recall",
            "scanned_at": time.time(),
            "cached": False,
            "tokens": display,
            "near_misses": near_misses,
            "counts": {
                "shown": len(display),
                "heat": sum(1 for t in display if t.get("heat_label") == "HEAT"),
                "warm": sum(1 for t in display if t.get("heat_label") == "WARM"),
                "risky": sum(1 for t in display if t.get("heat_label") == "RISKY"),
                "candidates_raw": scanned,
                "band_hits": len(cards),
                "enriched": enrich_n,
                "rejected": rejected,
            },
            "reject_breakdown": reject_breakdown,
            "band": {
                "mcap_min": HEAT_MCAP_MIN,
                "mcap_max": HEAT_MCAP_MAX,
                "ath_soft_floor": 0.68,
                "ath_hard_dump": 0.55,
                "min_age": MIN_AGE_MIN,
                "max_age": MAX_AGE_MIN,
            },
            "rule": (
                "ORGANIC HEAT = high recall, NOT capital protection. "
                f"Band ${HEAT_MCAP_MIN:,.0f}–${HEAT_MCAP_MAX:,.0f}. "
                "Allows pullbacks, thin narrative, incomplete holders → labeled RISKY. "
                "Still blocks rugs, critical bundles, hard dumps. "
                "Dust size only. Use Moons for high-accuracy picks."
            ),
            "warning": (
                "Many HEAT/WARM/RISKY tokens dump. This feed exists to catch "
                "organic runners Moons intentionally skip. Never size large."
            ),
        }
        _cache["data"] = payload
        _cache["ts"] = time.time()
        return payload
