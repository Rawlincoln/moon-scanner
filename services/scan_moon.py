"""Primary moon scan pipeline — extracted from main.py (behavior-preserving).

Flow: discover → cheap reject → enrich top N → filter_and_rank → outcomes log.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from config import MIGRATION_MCAP_MAX_USD, PADRE_TRADE_URL
from services.avoid_filters import BLOCKED_MINTS, analyze_avoid_flags
from services.bundle_sniper import analyze_bundle_and_snipers
from services.dexscreener import DexScreenerClient
from services.moon_outcomes import MoonOutcomes, get_outcomes
from services.moon_picks import evaluate, filter_and_rank, reject_reason
from services.padre_feed import PadreFeedClient
from services.pumpfun import PumpFunClient
from services.realtime_bus import realtime_bus
from services.social_signals import analyze_social_narrative
from services.solana_analyzer import SolanaAnalyzer
from services.tx_activity import score_tx_activity

logger = logging.getLogger("moon-scanner.scan_moon")

MOON_CACHE_TTL = 10.0

_pump = PumpFunClient()
_dex = DexScreenerClient()
_sol = SolanaAnalyzer()
_padre_feed = PadreFeedClient()
_moon_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_moon_lock = asyncio.Lock()
_outcomes: MoonOutcomes | None = None


def init_outcomes(base_dir: Path | None = None) -> MoonOutcomes:
    global _outcomes
    _outcomes = get_outcomes(base_dir)
    return _outcomes


def get_moon_outcomes() -> MoonOutcomes:
    global _outcomes
    if _outcomes is None:
        _outcomes = get_outcomes()
    return _outcomes


def moon_card_from_coin(coin: dict, *, source: str = "pump.fun") -> dict[str, Any] | None:
    """Build a moon card from raw pump.fun coin (pre-enrich)."""
    mint = (coin.get("mint") or "").strip()
    if not mint or mint in BLOCKED_MINTS:
        return None
    if coin.get("complete") or coin.get("is_banned"):
        return None
    mcap = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or 0)
    bond = PumpFunClient.bonding_progress(coin)
    age = PumpFunClient.coin_age_minutes(coin)
    avoid = analyze_avoid_flags(
        safety={
            "mint": mint,
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
            "description": coin.get("description"),
        },
        pump=coin,
        mint=mint,
    )
    social = analyze_social_narrative(
        pump_coin=coin,
        name=coin.get("name") or "",
        symbol=coin.get("symbol") or "",
        description=coin.get("description") or "",
    )
    card = {
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
    if reject_reason(card):
        return None
    return card


async def enrich_moon_card(
    card: dict[str, Any],
    *,
    skip_narrative_gate: bool = False,
) -> dict[str, Any]:
    """Dex first (cheap reject) → RugCheck only if still viable.

    skip_narrative_gate: for safe-snipes feed (no influencer/narrative hard reject).
    """
    mint = card.get("tokenAddress") or ""
    if not mint:
        return card
    market = card.get("market") or {}

    try:
        pairs = await asyncio.wait_for(
            _dex.get_token_pairs("solana", mint), timeout=2.5
        )
        pair = _dex.pick_best_pair(pairs) if pairs else None
        if pair:
            pc = pair.get("priceChange") or {}
            txns = pair.get("txns") or {}
            m5 = txns.get("m5") or {}
            h1 = txns.get("h1") or {}
            market = {
                "priceChange": pc,
                "txns": {"m5": m5, "h1": h1},
                "volume": pair.get("volume"),
                "liquidity": pair.get("liquidity"),
                "marketCap": pair.get("marketCap"),
                "url": pair.get("url"),
            }
            card["market"] = market
            card["priceChange"] = pc
            card["dex_url"] = pair.get("url")
            try:
                dex_mcap = float(pair.get("marketCap") or 0)
                if dex_mcap > 0 and not card.get("mcap_usd"):
                    card["mcap_usd"] = dex_mcap
            except (TypeError, ValueError):
                pass
            card["txActivity"] = score_tx_activity(
                pair=market, pump=card.get("pumpfun")
            )
    except Exception:
        pass

    try:
        lite = analyze_bundle_and_snipers(
            {},
            card.get("pumpfun") or {},
            market,
            age_minutes=float(card.get("age_minutes") or 0) or None,
            mcap_usd=float(card.get("mcap_usd") or 0) or None,
        )
        card["bundleSniper"] = lite
        card["bundle"] = lite.get("bundle")
        card["snipers"] = lite.get("snipers")
    except Exception:
        pass

    if not skip_narrative_gate and reject_reason(card):
        card["enrich_skipped_rugcheck"] = True
        return card

    try:
        safety = await asyncio.wait_for(
            _sol.analyze(mint, pump_coin=card.get("pumpfun"), fast=False),
            timeout=4.0,
        )
        if safety and not safety.get("error"):
            card["safety"] = safety
            bs = analyze_bundle_and_snipers(
                safety,
                card.get("pumpfun") or {},
                market,
                age_minutes=float(card.get("age_minutes") or 0) or None,
                mcap_usd=float(card.get("mcap_usd") or 0) or None,
            )
            card["bundleSniper"] = bs
            card["bundle"] = bs.get("bundle")
            card["snipers"] = bs.get("snipers")
            card["enrich_skipped_rugcheck"] = False
    except Exception:
        pass
    return card


def rough_priority(card: dict[str, Any]) -> float:
    """Pre-rank before expensive dex enrich — prefer near-ATH climbers."""
    mcap = float(card.get("mcap_usd") or 0)
    ath = float(card.get("ath_mcap") or 0)
    bond = float(card.get("bonding_progress") or 0)
    replies = int((card.get("pumpfun") or {}).get("reply_count") or 0)
    ret = (mcap / ath) if ath > 0 else 0.85
    return ret * 50 + min(bond, 80) * 0.4 + min(replies, 40) * 0.3 + min(mcap / 1000, 40)


async def scan_moon_tokens(
    *,
    limit: int = 16,
    max_age_minutes: float = 60.0,
    force: bool = False,
) -> dict[str, Any]:
    """Moon-only feed: pump.fun discovery + DexScreener accuracy pass."""
    now = time.time()
    if (
        not force
        and _moon_cache.get("data")
        and now - float(_moon_cache.get("ts") or 0) < MOON_CACHE_TTL
    ):
        cached = dict(_moon_cache["data"])
        cached["cached"] = True
        return cached

    async with _moon_lock:
        now = time.time()
        if (
            not force
            and _moon_cache.get("data")
            and now - float(_moon_cache.get("ts") or 0) < MOON_CACHE_TTL
        ):
            cached = dict(_moon_cache["data"])
            cached["cached"] = True
            return cached

        age_cap = max(float(max_age_minutes), 90.0)
        try:
            coins = await _padre_feed._fetch_pump_sorted("last_trade_timestamp", 100)
        except Exception:
            coins = []
        if not coins:
            try:
                coins = await _pump.get_latest_coins(limit=80)
            except Exception:
                coins = []
        if not isinstance(coins, list):
            coins = []

        rt_mints = set(realtime_bus.priority_mints(limit=40, max_age_sec=300))
        if rt_mints:

            def _rt_key(c: dict) -> tuple:
                m = str(c.get("mint") or "")
                return (0 if m in rt_mints else 1,)

            coins = sorted(coins, key=_rt_key)

        seen: set[str] = set()
        cards: list[dict] = []
        scanned = 0
        rejected = 0
        for coin in coins:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            scanned += 1
            age = PumpFunClient.coin_age_minutes(coin)
            if age > age_cap or age < 0.35:
                rejected += 1
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            if mcap < 3_000 or mcap > MIGRATION_MCAP_MAX_USD:
                rejected += 1
                continue
            card = moon_card_from_coin(coin)
            if not card:
                rejected += 1
                continue
            if mint in rt_mints:
                card["realtime"] = True
                ev = realtime_bus.has_mint(mint)
                if ev:
                    card["realtime_source"] = ev.source
                    card["realtime_age_ms"] = round(ev.age_ms(), 1)
            cards.append(card)

        cards.sort(key=rough_priority, reverse=True)
        enrich_n = min(len(cards), max(limit * 2, 12))
        to_enrich = cards[:enrich_n]
        rest = cards[enrich_n:]

        sem = asyncio.Semaphore(6)

        async def _one(c: dict) -> dict:
            async with sem:
                return await enrich_moon_card(c)

        enriched = await asyncio.gather(*[_one(c) for c in to_enrich])
        accurate: list[dict] = []
        for c in list(enriched) + rest:
            if reject_reason(c):
                rejected += 1
                continue
            accurate.append(c)

        # Phase 3: adaptive gates from historical dump/win rates
        gates = {
            "min_score": 55,
            "min_confidence": 52,
            "max_bundled_pct": 12.0,
            "require_influencer": False,
            "adapted": False,
            "sample_n": 0,
            "reasons": ["defaults"],
        }
        try:
            gates = get_moon_outcomes().suggested_gates()
        except Exception as exc:
            logger.debug("suggested_gates failed: %s", exc)

        display = filter_and_rank(
            accurate,
            min_score=int(gates.get("min_score") or 55),
            min_confidence=int(gates.get("min_confidence") or 52),
            max_bundled_pct=float(gates.get("max_bundled_pct") or 12.0),
            require_influencer=bool(gates.get("require_influencer")),
        )[:limit]

        for t in display:
            if not t.get("moon"):
                t["moon"] = evaluate(t)
                t["moon_score"] = t["moon"]["moon_score"]
                t["moon_label"] = t["moon"]["label"]
                t["confidence"] = t["moon"]["confidence"]

        try:
            rec_n = get_moon_outcomes().record_shown(display)
            if rec_n:
                logger.info("Moon outcomes recorded %s new recs", rec_n)
        except Exception as exc:
            logger.debug("moon outcomes record failed: %s", exc)

        outcome_sum: dict[str, Any] = {}
        try:
            full = get_moon_outcomes().summary()
            outcome_sum = {
                k: full.get(k)
                for k in (
                    "total_recs",
                    "active",
                    "finalized",
                    "win_rate_pct",
                    "dump_rate_pct",
                    "hold_rate_pct",
                    "by_label",
                    "by_influencer",
                    "by_bundled_band",
                    "gates",
                )
            }
        except Exception:
            outcome_sum = {"gates": gates}

        payload = {
            "ok": True,
            "mode": "moon_v3_narrative",
            "scanned_at": time.time(),
            "cached": False,
            "tokens": display,
            "counts": {
                "shown": len(display),
                "moon": sum(1 for t in display if t.get("moon_label") == "MOON"),
                "watch": sum(1 for t in display if t.get("moon_label") == "WATCH"),
                "influencer": sum(
                    1
                    for t in display
                    if (t.get("moon") or {}).get("influencer_tweet")
                    or (t.get("socialSignals") or {}).get("influencer_tweet")
                ),
                "candidates_raw": scanned,
                "enriched": enrich_n,
                "analyzed": scanned,
                "rejected": rejected,
            },
            "outcomes": outcome_sum,
            "gates": gates,
            "rule": (
                "v3 capital protection + Phase 3 adaptive gates from past rec outcomes. "
                f"Gates: score≥{gates.get('min_score')} conf≥{gates.get('min_confidence')} "
                f"bundled≤{gates.get('max_bundled_pct')}%"
                + (
                    " influencer-required"
                    if gates.get("require_influencer")
                    else ""
                )
                + ". Near-ATH + narrative edge required; dumps/bundles/ghosts hidden."
            ),
        }
        _moon_cache["data"] = payload
        _moon_cache["ts"] = time.time()
        return payload
