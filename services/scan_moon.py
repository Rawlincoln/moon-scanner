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
from services.accuracy import merge_ath_into_token
from services.moon_picks import (
    MIN_MCAP,
    default_rank_gates,
    evaluate,
    filter_and_rank,
    moon_mode,
    reject_reason,
)
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
_learning: Any = None


def init_outcomes(base_dir: Path | None = None) -> MoonOutcomes:
    global _outcomes
    _outcomes = get_outcomes(base_dir)
    return _outcomes


def bind_learning(engine: Any) -> None:
    """Wire LearningEngine so moon/snipe UI recs train the model."""
    global _learning
    _learning = engine


def get_learning() -> Any:
    return _learning


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
        pump={**coin, "age_minutes": age},
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
            "age_minutes": round(age, 1),
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
    try:
        from services.ticker_registry import attach_ticker_uniqueness

        attach_ticker_uniqueness(card, record=True)
    except Exception:
        pass
    # Structural only here — full reject_reason (narrative/edge) after enrich
    # so near-misses and multi-source discovery can surface filtered charts.
    if avoid.get("hard") or avoid.get("hard_avoid"):
        return None
    if mint in BLOCKED_MINTS:
        return None
    return card


async def enrich_moon_card(
    card: dict[str, Any],
    *,
    skip_narrative_gate: bool = False,
) -> dict[str, Any]:
    """Dex first (cheap reject) → RugCheck only if still viable.

    skip_narrative_gate: for safe-snipes feed (no influencer/narrative hard reject).

    Sets ``enrich_ok`` True only when RugCheck (or pump safety fallback) succeeds.
    Incomplete enrich must not be ranked as MOON/SNIPE.
    """
    mint = card.get("tokenAddress") or ""
    if not mint:
        card["enrich_ok"] = False
        card["enrich_errors"] = ["missing_mint"]
        return card
    market = card.get("market") or {}
    errors: list[str] = []
    dex_ok = False
    safety_ok = False

    try:
        pairs = await asyncio.wait_for(
            _dex.get_token_pairs("solana", mint), timeout=2.5
        )
        pair = _dex.pick_best_pair(pairs) if pairs else None
        if pair:
            dex_ok = True
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
                if dex_mcap > 0:
                    if not card.get("mcap_usd"):
                        card["mcap_usd"] = dex_mcap
                    # Multi-source ATH high-water from Dex
                    prev_ath = float(card.get("ath_mcap") or 0)
                    if dex_mcap > prev_ath:
                        card["ath_mcap"] = max(prev_ath, dex_mcap)
            except (TypeError, ValueError):
                pass
            card["txActivity"] = score_tx_activity(
                pair=market, pump=card.get("pumpfun")
            )
            merge_ath_into_token(card)
        else:
            errors.append("dex_no_pair")
    except Exception as exc:
        errors.append(f"dex:{type(exc).__name__}")

    try:
        lite = analyze_bundle_and_snipers(
            {},
            card.get("pumpfun") or {},
            market,
            age_minutes=float(card.get("age_minutes") or 0) or None,
            mcap_usd=float(card.get("mcap_usd") or 0) or None,
        )
        lite["holders_known"] = False
        if lite.get("overall") in ("clean", "low", None, ""):
            lite["overall"] = "unknown"
        card["bundleSniper"] = lite
        card["bundle"] = lite.get("bundle")
        card["snipers"] = lite.get("snipers")
    except Exception as exc:
        errors.append(f"bundle_lite:{type(exc).__name__}")

    # Early narrative/capital reject still skips expensive RugCheck for moon cost,
    # but must never rank as a recommendation.
    if not skip_narrative_gate:
        pre_reason = reject_reason(card)
        if pre_reason:
            card["enrich_skipped_rugcheck"] = True
            card["pre_enrich_reject"] = pre_reason
            card["enrich_ok"] = False
            card["enrich_partial"] = True
            card["enrich_errors"] = errors + ["skipped_pre_enrich:" + pre_reason[:40]]
            return card

    try:
        safety = await asyncio.wait_for(
            _sol.analyze(mint, pump_coin=card.get("pumpfun"), fast=False),
            timeout=4.0,
        )
        if safety and not safety.get("error"):
            safety_ok = True
            card["safety"] = safety
            bs = analyze_bundle_and_snipers(
                safety,
                card.get("pumpfun") or {},
                market,
                age_minutes=float(card.get("age_minutes") or 0) or None,
                mcap_usd=float(card.get("mcap_usd") or 0) or None,
            )
            holders = bool(safety.get("top_holders"))
            bs["holders_known"] = holders
            if not holders and bs.get("overall") in ("clean", "low"):
                bs["overall"] = "unknown"
            card["bundleSniper"] = bs
            card["bundle"] = bs.get("bundle")
            card["snipers"] = bs.get("snipers")
            card["enrich_skipped_rugcheck"] = False
            # Refresh avoid with holder-aware flags
            try:
                from services.avoid_filters import analyze_avoid_flags

                avoid = analyze_avoid_flags(
                    safety=safety,
                    pump=card.get("pumpfun") or {},
                    pair=market,
                    mint=mint,
                )
                card["avoid"] = avoid
                card["safetyReport"] = {**(card.get("safetyReport") or {}), "avoid": avoid}
            except Exception:
                pass
        else:
            errors.append("rugcheck_error_or_empty")
    except Exception as exc:
        errors.append(f"rugcheck:{type(exc).__name__}")

    # Pump bonding tokens may lack Dex pair — pump mcap is acceptable if safety ok.
    # Full enrich_ok requires safety without error; holders_known tracked separately
    # so ranking can demand holder book for MOON/WATCH grades.
    if safety_ok:
        merge_ath_into_token(card)
        safety = card.get("safety") or {}
        holders = bool(safety.get("top_holders"))
        card["holders_known"] = holders
        if not holders:
            errors.append("holders_unknown")
    card["enrich_dex_ok"] = dex_ok
    card["enrich_partial"] = bool(errors) or not safety_ok
    card["enrich_errors"] = errors
    card["enrich_ok"] = bool(safety_ok)
    return card


def rough_priority(card: dict[str, Any]) -> float:
    """Pre-rank before expensive dex enrich — prefer climb/migration + social + devs."""
    mcap = float(card.get("mcap_usd") or 0)
    ath = float(card.get("ath_mcap") or 0)
    bond = float(card.get("bonding_progress") or 0)
    replies = int((card.get("pumpfun") or {}).get("reply_count") or 0)
    ret = (mcap / ath) if ath > 0 else 0.85
    social = card.get("socialSignals") or {}
    edge = float(social.get("edge_score") or 0)
    boost = 0.0
    if card.get("realtime"):
        boost += 12
    if social.get("influencer_tweet"):
        boost += 18
    elif social.get("has_edge"):
        boost += 10
    elif edge >= 30:
        boost += 5
    # Prefer mid-curve climbers (migration path) over sub-$7k lottery for enrich budget
    if mcap >= 28_000 or bond >= 40:
        boost += 16  # near migration
    elif mcap >= 14_000 or bond >= 22:
        boost += 12  # climb
    elif mcap >= 7_000:
        boost += 6  # past survival floor
    else:
        boost -= 8  # lottery — deprioritize enrich
    # Prefer creators with migrate/moon track records for enrich budget
    try:
        from services.dev_risk import attach_dev_risk, dev_priority_boost

        if card.get("safety") or (card.get("pumpfun") or {}).get("creator"):
            if not isinstance(card.get("devRisk"), dict):
                attach_dev_risk(card)
            boost += dev_priority_boost(card)
    except Exception:
        pass
    # Prefer unique tickers; deprioritize heavily reused symbols
    try:
        from services.ticker_registry import attach_ticker_uniqueness, ticker_priority_boost

        if not isinstance(card.get("tickerUniqueness"), dict):
            attach_ticker_uniqueness(card, record=True)
        boost += ticker_priority_boost(card)
    except Exception:
        pass
    return (
        ret * 50
        + min(bond, 80) * 0.55  # bonding progress more weight (migration signal)
        + min(replies, 40) * 0.35
        + min(mcap / 1000, 55)  # higher mcap climbers get enrich priority
        + boost
    )


async def _discover_moon_coins() -> list[dict]:
    """Merge pump.fun sorts so mid-band climbers are not missed."""
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


def _short_moon_reject(reason: str | None) -> str:
    if not reason:
        return "other"
    r = reason.lower()
    if "narrative" in r or "edge" in r or "influencer" in r or "name-jack" in r:
        return "no_edge"
    if "dump" in r or "faded" in r or "crash" in r:
        return "dumped"
    if "too small" in r or "too large" in r:
        return "mcap"
    if "bundl" in r or "sniper" in r:
        return "book"
    if "ticker" in r or "junk" in r:
        return "ticker"
    if "age" in r or "fresh" in r or "young" in r:
        return "age"
    if "honeypot" in r or "rugged" in r or "avoid" in r or "blocklist" in r:
        return "safety"
    return "other"


async def scan_moon_tokens(
    *,
    limit: int = 16,
    max_age_minutes: float = 60.0,
    force: bool = False,
) -> dict[str, Any]:
    """Moon-only feed: multi-source discovery + DexScreener accuracy pass."""
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

        # Honor UI age filter (cap at MAX_AGE); default routes still pass 120.
        from config import MAX_AGE_MINUTES_CAP

        age_cap = min(max(float(max_age_minutes), 5.0), float(MAX_AGE_MINUTES_CAP))
        coins = await _discover_moon_coins()
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
        pre_reject: dict[str, int] = {}
        for coin in coins:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in seen:
                continue
            seen.add(mint)
            scanned += 1
            age = PumpFunClient.coin_age_minutes(coin)
            if age > age_cap or age < 0.35:
                rejected += 1
                pre_reject["age"] = pre_reject.get("age", 0) + 1
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            # Align pre-filter with moon MIN_MCAP (was $3k vs reject $4k)
            if mcap < MIN_MCAP or mcap > MIGRATION_MCAP_MAX_USD:
                rejected += 1
                pre_reject["mcap"] = pre_reject.get("mcap", 0) + 1
                continue
            card = moon_card_from_coin(coin)
            if not card:
                rejected += 1
                pre_reject["card"] = pre_reject.get("card", 0) + 1
                # Soft near-miss from raw coin for empty-state UI
                continue
            if mint in rt_mints:
                card["realtime"] = True
                ev = realtime_bus.has_mint(mint)
                if ev:
                    card["realtime_source"] = ev.source
                    card["realtime_age_ms"] = round(ev.age_ms(), 1)
            cards.append(card)

        cards.sort(key=rough_priority, reverse=True)
        # Extra enrich budget when realtime heat or discovery is deep
        base_enrich = max(limit * 2, 16) + (8 if rt_mints else 0)
        enrich_n = min(len(cards), base_enrich)
        to_enrich = cards[:enrich_n]
        rest = cards[enrich_n:]

        sem = asyncio.Semaphore(6)

        async def _one(c: dict) -> dict:
            async with sem:
                return await enrich_moon_card(c)

        enriched = list(await asyncio.gather(*[_one(c) for c in to_enrich]))
        accurate: list[dict] = []
        near_misses: list[dict] = []
        post_reject: dict[str, int] = {}

        def _consume_enriched(batch: list) -> None:
            nonlocal rejected
            for c in batch:
                _consume_one(c)

        def _consume_one(c: dict) -> None:
            nonlocal rejected
            mint = (c.get("tokenAddress") or c.get("mint") or "").strip()
            errs = c.get("enrich_errors") or []
            pre_reason = c.get("pre_enrich_reject")
            if c.get("enrich_ok") is not True:
                rejected += 1
                if pre_reason or c.get("enrich_skipped_rugcheck"):
                    reason = pre_reason or "filtered before safety enrich"
                    key = _short_moon_reject(reason)
                    post_reject[key] = post_reject.get(key, 0) + 1
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
                                "socialSignals": c.get("socialSignals"),
                            }
                        )
                else:
                    post_reject["enrich"] = post_reject.get("enrich", 0) + 1
                    if len(near_misses) < 10:
                        near_misses.append(
                            {
                                "symbol": c.get("symbol") or "?",
                                "name": c.get("name") or "",
                                "tokenAddress": mint,
                                "mcap_usd": c.get("mcap_usd"),
                                "ath_mcap": c.get("ath_mcap"),
                                "age_minutes": c.get("age_minutes"),
                                "reject": "safety unknown — "
                                + ", ".join(str(e) for e in (errs or ["incomplete"])[:2]),
                                "reject_key": "enrich",
                            }
                        )
                return
            reason = reject_reason(c)
            if reason:
                rejected += 1
                key = _short_moon_reject(reason)
                post_reject[key] = post_reject.get(key, 0) + 1
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
                            "socialSignals": c.get("socialSignals"),
                            "bundleSniper": c.get("bundleSniper"),
                        }
                    )
                return
            accurate.append(c)

        _consume_enriched(enriched)

        # Second-wave enrich when first wave yielded nothing rankable
        if not accurate and rest:
            wave2_n = min(len(rest), max(limit * 2, 12))
            wave2 = rest[:wave2_n]
            rest = rest[wave2_n:]
            enriched2 = list(await asyncio.gather(*[_one(c) for c in wave2]))
            enrich_n += wave2_n
            post_reject["second_wave_enrich"] = wave2_n
            _consume_enriched(enriched2)

        # Count rest as not considered for rank (discovery overflow)
        if rest:
            post_reject["not_enriched_overflow"] = len(rest)

        # Phase 3: adaptive gates from historical dump/win rates
        base_gates = default_rank_gates()
        gates = {
            **base_gates,
            "require_influencer": False,
            "adapted": False,
            "sample_n": 0,
            "reasons": [f"defaults ({moon_mode()})"],
            "mode": moon_mode(),
        }
        try:
            gates = get_moon_outcomes().suggested_gates()
            gates["mode"] = moon_mode()
            # Slight relax when empty feed + small sample (avoid permanent zero-show)
            floor_score = int(base_gates["min_score"])
            floor_conf = int(base_gates["min_confidence"])
            if (
                not accurate
                and int(gates.get("sample_n") or 0) < 8
                and not gates.get("adapted")
            ):
                soft = 3 if moon_mode() == "balanced" else 2
                gates = {
                    **gates,
                    "min_score": max(
                        floor_score - soft, int(gates.get("min_score") or floor_score) - soft
                    ),
                    "min_confidence": max(
                        floor_conf - soft,
                        int(gates.get("min_confidence") or floor_conf) - soft,
                    ),
                    "reasons": list(gates.get("reasons") or [])
                    + ["empty-wave soft floor"],
                    "mode": moon_mode(),
                }
        except Exception as exc:
            logger.debug("suggested_gates failed: %s", exc)

        display = filter_and_rank(
            accurate,
            min_score=int(gates.get("min_score") or base_gates["min_score"]),
            min_confidence=int(gates.get("min_confidence") or base_gates["min_confidence"]),
            max_bundled_pct=float(gates.get("max_bundled_pct") or 12.0),
            require_influencer=bool(gates.get("require_influencer")),
            require_holders=True,
        )[:limit]

        for t in display:
            if not t.get("moon"):
                t["moon"] = evaluate(t)
                t["moon_score"] = t["moon"]["moon_score"]
                t["moon_label"] = t["moon"]["label"]
                t["confidence"] = t["moon"]["confidence"]

        try:
            outs = get_moon_outcomes()
            rec_n = outs.record_shown(display)
            if rec_n:
                logger.info("Moon outcomes recorded %s new recs", rec_n)
            # Sample near-misses for false-negative / precision study
            nm_n = outs.record_near_misses(near_misses, limit=6)
            if nm_n:
                logger.info("Moon near-miss sample recorded %s", nm_n)
        except Exception as exc:
            logger.debug("moon outcomes record failed: %s", exc)

        # Train learning model on what the UI actually showed
        if _learning is not None and display:
            try:
                _learning.observe_feed_cards(display, source="moon", limit=limit)
            except Exception as exc:
                logger.debug("learning observe moon failed: %s", exc)

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

        empty_info = None
        if not display:
            top_rb = sorted(
                {**{f"pre_{k}": v for k, v in pre_reject.items()},
                 **{f"post_{k}": v for k, v in post_reject.items()}}.items(),
                key=lambda kv: -int(kv[1] or 0),
            )[:5]
            empty_info = {
                "intentional": True,
                "mode": moon_mode(),
                "hint": (
                    "Empty Moons is OK — capital protection. "
                    "Use Organic Heat for high-recall risk. "
                    + (
                        "Balanced mode: −15% ATH + organic community path when holders clean."
                        if moon_mode() == "balanced"
                        else "Strict mode: −12% ATH + influencer/community edge only."
                    )
                ),
                "top_rejects": [{"key": k, "n": v} for k, v in top_rb],
                "near_miss_n": len(near_misses),
                "accurate_n": len(accurate),
            }

        payload = {
            "ok": True,
            "mode": f"moon_v3_{moon_mode()}",
            "moon_mode": moon_mode(),
            "scanned_at": time.time(),
            "cached": False,
            "tokens": display,
            "near_misses": near_misses,
            "empty": empty_info,
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
                "band_hits": len(cards),
                "enriched": enrich_n,
                "analyzed": scanned,
                "rejected": rejected,
                "accurate": len(accurate),
            },
            "reject_breakdown": {
                **{f"pre_{k}": v for k, v in pre_reject.items()},
                **{f"post_{k}": v for k, v in post_reject.items()},
            },
            "outcomes": outcome_sum,
            "gates": gates,
            "rule": (
                f"v3 {moon_mode()} capital protection + Phase 3 adaptive gates. "
                f"Gates: score≥{gates.get('min_score')} conf≥{gates.get('min_confidence')} "
                f"bundled≤{gates.get('max_bundled_pct')}%"
                + (
                    " influencer-required"
                    if gates.get("require_influencer")
                    else ""
                )
                + (
                    ". Balanced: near-ATH (−15/−18%) + organic community when holders clean."
                    if moon_mode() == "balanced"
                    else ". Strict: near-ATH (−12/−15%) + narrative edge; dumps/ghosts hidden."
                )
            ),
        }
        _moon_cache["data"] = payload
        _moon_cache["ts"] = time.time()
        # Telegram push (deduped; no-op if not configured)
        if display:
            try:
                from services.telegram_alerts import notify_new_picks

                asyncio.create_task(notify_new_picks("moon", display))
            except Exception:
                pass
        return payload
