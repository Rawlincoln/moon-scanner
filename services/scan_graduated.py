"""Scan pipeline for graduated / large runners."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any

from services.graduated_runners import (
    FLASH_GRAD_MIN_AGE,
    GRAD_MCAP_MAX,
    GRAD_MCAP_MIN,
    MAX_AGE_MIN,
    MIN_AGE_MIN,
    evaluate_graduated,
    filter_and_rank_graduated,
    graduated_card_from_coin,
    graduated_reject_reason,
)
from services.padre_feed import PadreFeedClient
from services.pumpfun import PumpFunClient
from services.realtime_bus import realtime_bus
from services.scan_moon import enrich_moon_card

logger = logging.getLogger("moon-scanner.scan_graduated")

CACHE_TTL = 20.0
_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_lock = asyncio.Lock()
_pump = PumpFunClient()
_padre_feed = PadreFeedClient()


async def _discover_graduated() -> list[dict]:
    """Favor high mcap + last trade (graduated coins rarely show in 'created')."""
    sorts = ("market_cap", "last_trade_timestamp", "created_timestamp")
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
    # Extra pages of market_cap for large names
    try:
        more = await _padre_feed._fetch_pump_sorted("market_cap", 100)
        for coin in more or []:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            merged.append(coin)
    except Exception:
        pass
    return merged


def _short_reject(reason: str | None) -> str:
    if not reason:
        return "other"
    r = reason.lower()
    if "too small" in r:
        return "mcap_low"
    if "too large" in r:
        return "mcap_high"
    if "fresh" in r:
        return "too_fresh"
    if "old" in r:
        return "too_old"
    if "dump" in r or "dead" in r or "crash" in r:
        return "dumped"
    if "curve" in r or "heat" in r or "moons" in r:
        return "not_graduated"
    if "honeypot" in r or "rugged" in r:
        return "honeypot"
    return "other"


def _prio(c: dict) -> float:
    m = float(c.get("mcap_usd") or 0)
    a = float(c.get("ath_mcap") or 0)
    ret = (m / a) if a > 0 else 0.5
    complete = 1.0 if c.get("complete") or (c.get("pumpfun") or {}).get("complete") else 0.0
    replies = int((c.get("pumpfun") or {}).get("reply_count") or 0)
    return ret * 40 + complete * 15 + min(replies, 40) * 0.2 + min(m / 1e6, 30)


async def scan_graduated_runners(
    *,
    limit: int = 16,
    max_age_minutes: float = 7 * 24 * 60,
    force: bool = False,
) -> dict[str, Any]:
    now = time.time()
    if (
        not force
        and _cache.get("data")
        and now - float(_cache.get("ts") or 0) < CACHE_TTL
    ):
        cached = dict(_cache["data"])
        cached["cached"] = True
        return cached

    async with _lock:
        now = time.time()
        if (
            not force
            and _cache.get("data")
            and now - float(_cache.get("ts") or 0) < CACHE_TTL
        ):
            cached = dict(_cache["data"])
            cached["cached"] = True
            return cached

        age_cap = min(float(max_age_minutes), MAX_AGE_MIN)
        coins = await _discover_graduated()
        if not isinstance(coins, list):
            coins = []

        rt = set(realtime_bus.priority_mints(limit=20, max_age_sec=600))
        seen: set[str] = set()
        cards: list[dict] = []
        scanned = 0
        rejected = 0
        pre_reject: Counter[str] = Counter()

        for coin in coins:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            scanned += 1
            age = PumpFunClient.coin_age_minutes(coin)
            # Flash grads can be as young as FLASH_GRAD_MIN_AGE
            min_age_pre = FLASH_GRAD_MIN_AGE * 0.8
            if age > age_cap or age < min_age_pre:
                rejected += 1
                pre_reject["age"] += 1
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            if mcap < GRAD_MCAP_MIN * 0.85 or mcap > GRAD_MCAP_MAX:
                rejected += 1
                pre_reject["mcap"] += 1
                continue
            card = graduated_card_from_coin(coin)
            if not card:
                rejected += 1
                pre_reject["card"] += 1
                continue
            if mint in rt:
                card["realtime"] = True
            cards.append(card)

        cards.sort(key=_prio, reverse=True)
        enrich_n = min(len(cards), max(limit * 3, 20))
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
                reason = graduated_reject_reason(c)
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

        # Second-wave enrich when first pass empty (P1 from audit, small cost)
        if not accurate and rest:
            wave2_n = min(len(rest), max(limit * 2, 12))
            wave2 = rest[:wave2_n]
            rest = rest[wave2_n:]
            enriched2 = list(await asyncio.gather(*[_one(c) for c in wave2]))
            enrich_n += wave2_n
            post_reject["second_wave_enrich"] += wave2_n
            _consume(enriched2)

        if rest:
            post_reject["not_enriched_overflow"] += len(rest)

        display = filter_and_rank_graduated(accurate, min_score=48, limit=limit)
        for t in display:
            if not t.get("grad"):
                t["grad"] = evaluate_graduated(t)

        if display:
            try:
                from services.telegram_alerts import notify_new_picks

                asyncio.create_task(notify_new_picks("grad", display))
            except Exception:
                pass

        payload = {
            "ok": True,
            "mode": "graduated_large_runners",
            "scanned_at": time.time(),
            "cached": False,
            "tokens": display,
            "near_misses": near_misses,
            "counts": {
                "shown": len(display),
                "runner": sum(1 for t in display if t.get("grad_label") == "RUNNER"),
                "dip": sum(1 for t in display if t.get("grad_label") == "DIP"),
                "watch": sum(1 for t in display if t.get("grad_label") == "WATCH"),
                "candidates_raw": scanned,
                "band_hits": len(cards),
                "enriched": enrich_n,
                "rejected": rejected,
            },
            "reject_breakdown": {
                **{f"pre_{k}": v for k, v in pre_reject.items()},
                **{f"post_{k}": v for k, v in post_reject.items()},
            },
            "band": {
                "mcap_min": GRAD_MCAP_MIN,
                "mcap_max": GRAD_MCAP_MAX,
                "min_age_min": MIN_AGE_MIN,
                "max_age_min": MAX_AGE_MIN,
                "ath_runner_frac": 0.72,
                "ath_dip_frac": [0.28, 0.72],
            },
            "rule": (
                "GRADUATED / LARGE RUNNERS — post-migration or ≥~$80k mcap. "
                "RUNNER = near ATH structure; DIP = pullback with life; "
                "WATCH = large but mixed. Not early Heat/Snipes. Size carefully."
            ),
            "warning": (
                "These already ran hard. Different risk than $6k entries. "
                "Many continue dumping after graduation."
            ),
        }
        _cache["data"] = payload
        _cache["ts"] = time.time()
        return payload
