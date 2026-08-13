"""Elite copy-trade scan — discover tokens held by top 20 smart wallets.

Pipeline:
  1. Discover active pump coins (same multi-sort as heat)
  2. Enrich top candidates (RugCheck holders required for elite match)
  3. Credit quality holders → learn elites
  4. Match elite wallets on book + full safety (elite_signals)
  5. Rank ELITE / COPY / WATCH
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any

from config import MIGRATION_MCAP_MAX_USD, PADRE_TRADE_URL
from services.avoid_filters import BLOCKED_MINTS, analyze_avoid_flags
from services.elite_signals import (
    ELITE_MCAP_MAX,
    ELITE_MCAP_MIN,
    MAX_AGE_MIN,
    MIN_AGE_MIN,
    filter_and_rank_elite,
)
from services.elite_traders import credit_holders_from_token, get_elite_roster
from services.padre_feed import PadreFeedClient
from services.pumpfun import PumpFunClient
from services.scan_moon import enrich_moon_card
from services.social_signals import analyze_social_narrative

logger = logging.getLogger("moon-scanner.scan_elite")

ELITE_CACHE_TTL = 15.0
_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_lock = asyncio.Lock()
_pump = PumpFunClient()
_padre_feed = PadreFeedClient()


def elite_card_from_coin(coin: dict, *, source: str = "pump.fun") -> dict[str, Any] | None:
    mint = (coin.get("mint") or "").strip()
    if not mint or mint in BLOCKED_MINTS:
        return None
    if coin.get("is_banned") or coin.get("complete"):
        return None
    mcap = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or 0)
    bond = PumpFunClient.bonding_progress(coin)
    age = PumpFunClient.coin_age_minutes(coin)
    if mcap < ELITE_MCAP_MIN * 0.9 or mcap > min(ELITE_MCAP_MAX * 1.05, MIGRATION_MCAP_MAX_USD):
        return None
    if age < MIN_AGE_MIN * 0.7 or age > MAX_AGE_MIN + 20:
        return None
    avoid = analyze_avoid_flags(
        safety={"mint": mint, "name": coin.get("name"), "symbol": coin.get("symbol")},
        pump={**coin, "age_minutes": age},
        mint=mint,
    )
    if avoid.get("hard_avoid") or avoid.get("hard"):
        return None
    social = analyze_social_narrative(
        pump_coin=coin,
        name=coin.get("name") or "",
        symbol=coin.get("symbol") or "",
        description=coin.get("description") or "",
    )
    return {
        "tokenAddress": mint,
        "chainId": "solana",
        "name": coin.get("name") or "Unknown",
        "symbol": coin.get("symbol") or "?",
        "icon": coin.get("image_uri"),
        "mcap_usd": mcap,
        "ath_mcap": ath or None,
        "bonding_progress": round(bond, 1),
        "age_minutes": round(age, 1),
        "pumpfun": {
            "usd_market_cap": mcap,
            "ath_market_cap": ath or None,
            "bonding_progress": bond,
            "twitter": coin.get("twitter"),
            "telegram": coin.get("telegram"),
            "website": coin.get("website"),
            "description": coin.get("description"),
            "reply_count": coin.get("reply_count", 0),
            "creator": coin.get("creator"),
            "image_uri": coin.get("image_uri"),
        },
        "safetyReport": {"avoid": avoid},
        "avoid": avoid,
        "socialSignals": social,
        "pump_url": f"https://pump.fun/coin/{mint}",
        "padre_url": f"{PADRE_TRADE_URL}/trade/solana/{mint}",
        "source": source,
    }


async def _discover() -> list[dict]:
    sorts = ("last_trade_timestamp", "market_cap", "created_timestamp")
    batches = await asyncio.gather(
        *[_padre_feed._fetch_pump_sorted(s, 90) for s in sorts],
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


def _prio(c: dict) -> float:
    m = float(c.get("mcap_usd") or 0)
    a = float(c.get("ath_mcap") or 0)
    ret = (m / a) if a > 0 else 0.85
    replies = int((c.get("pumpfun") or {}).get("reply_count") or 0)
    bond = float(c.get("bonding_progress") or 0)
    return ret * 40 + min(replies, 40) * 0.4 + min(bond, 60) * 0.3 + min(m / 1000, 40)


async def scan_elite_signals(
    *,
    limit: int = 16,
    max_age_minutes: float = 120.0,
    force: bool = False,
) -> dict[str, Any]:
    now = time.time()
    if (
        not force
        and _cache.get("data")
        and now - float(_cache.get("ts") or 0) < ELITE_CACHE_TTL
    ):
        cached = dict(_cache["data"])
        cached["cached"] = True
        return cached

    async with _lock:
        now = time.time()
        if (
            not force
            and _cache.get("data")
            and now - float(_cache.get("ts") or 0) < ELITE_CACHE_TTL
        ):
            cached = dict(_cache["data"])
            cached["cached"] = True
            return cached

        age_cap = min(float(max_age_minutes), MAX_AGE_MIN)
        coins = await _discover()
        cards: list[dict] = []
        scanned = 0
        rejected = 0
        pre: Counter[str] = Counter()
        seen: set[str] = set()

        for coin in coins:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            scanned += 1
            age = PumpFunClient.coin_age_minutes(coin)
            if age > age_cap or age < MIN_AGE_MIN * 0.6:
                rejected += 1
                pre["age"] += 1
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            if mcap < ELITE_MCAP_MIN * 0.9 or mcap > ELITE_MCAP_MAX:
                rejected += 1
                pre["mcap"] += 1
                continue
            card = elite_card_from_coin(coin)
            if not card:
                rejected += 1
                pre["card"] += 1
                continue
            cards.append(card)

        cards.sort(key=_prio, reverse=True)
        # Need holders → enrich more aggressively
        enrich_n = min(len(cards), max(limit * 5, 28))
        to_enrich = cards[:enrich_n]

        async def _en(c: dict) -> dict:
            return await enrich_moon_card(c, skip_narrative_gate=True)

        enriched = await asyncio.gather(*[_en(c) for c in to_enrich], return_exceptions=True)
        ready: list[dict] = []
        post: Counter[str] = Counter()
        for i, res in enumerate(enriched):
            if isinstance(res, Exception):
                post["enrich_err"] += 1
                continue
            c = res if isinstance(res, dict) else to_enrich[i]
            # Learn from any quality-enriched book (before elite filter)
            if c.get("enrich_ok") is True and holders_known_safe(c):
                try:
                    credit_holders_from_token(c, points=1.5, reason="quality_token")
                except Exception:
                    pass
            ready.append(c)

        display = filter_and_rank_elite(ready, min_score=52, limit=limit)
        # Extra credit for tokens that made the elite feed
        for t in display:
            if t.get("elite_label") in ("ELITE", "COPY"):
                try:
                    credit_holders_from_token(t, points=3.0, reason="elite_signal")
                except Exception:
                    pass

        near_misses = []
        from services.elite_signals import elite_reject_reason

        for c in ready:
            if any(c.get("tokenAddress") == d.get("tokenAddress") for d in display):
                continue
            r = elite_reject_reason(c)
            if r and "no elite" not in (r or "").lower():
                near_misses.append(
                    {
                        "symbol": c.get("symbol"),
                        "mint": c.get("tokenAddress"),
                        "reject": r,
                        "mcap_usd": c.get("mcap_usd"),
                    }
                )
            if len(near_misses) >= 10:
                break

        roster = get_elite_roster(limit=20)
        payload = {
            "ok": True,
            "mode": "elite_copy_trade",
            "scanned_at": time.time(),
            "cached": False,
            "tokens": display,
            "near_misses": near_misses,
            "traders": [
                {
                    "id": t.get("id"),
                    "address": t.get("address"),
                    "label": t.get("label"),
                    "tier": t.get("tier"),
                    "style": t.get("style"),
                    "source": t.get("source"),
                    "score": t.get("score"),
                    "wins": t.get("wins"),
                }
                for t in roster
            ],
            "counts": {
                "shown": len(display),
                "elite": sum(1 for t in display if t.get("elite_label") == "ELITE"),
                "copy": sum(1 for t in display if t.get("elite_label") == "COPY"),
                "watch": sum(1 for t in display if t.get("elite_label") == "WATCH"),
                "candidates_raw": scanned,
                "band_hits": len(cards),
                "enriched": enrich_n,
                "rejected": rejected,
                "roster": len(roster),
            },
            "reject_breakdown": {
                **{f"pre_{k}": v for k, v in pre.items()},
                **{f"post_{k}": v for k, v in post.items()},
            },
            "band": {
                "mcap_min": ELITE_MCAP_MIN,
                "mcap_max": ELITE_MCAP_MAX,
                "min_age": MIN_AGE_MIN,
                "max_age": MAX_AGE_MIN,
                "elite_slots": 20,
            },
            "rule": (
                "ELITE COPY — buy signal when one of our top-20 smart wallets is on the book "
                "AND full capital safety passes (hard avoid, flash, wash, bundle, mint/freeze path). "
                "Replace seed wallets in data/elite_traders.json with real GMGN/Kolscan addresses. "
                "Learned wallets auto-promote from HEAT/MOON quality holders."
            ),
            "warning": (
                "Copy-trading is high risk. Elites get rekt too. Size small. Not financial advice."
            ),
        }
        _cache["data"] = payload
        _cache["ts"] = time.time()
        if display:
            try:
                from services.telegram_alerts import notify_new_picks

                asyncio.create_task(notify_new_picks("elite", display))
            except Exception:
                pass
        return payload


def holders_known_safe(token: dict) -> bool:
    try:
        from services.accuracy import holders_known

        return holders_known(token)
    except Exception:
        return bool((token.get("safety") or {}).get("top_holders"))
