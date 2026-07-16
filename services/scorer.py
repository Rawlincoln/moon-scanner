"""Moon potential scoring engine."""

from __future__ import annotations

import time
from typing import Any


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def compute_safety_score(safety: dict) -> float:
    """0-100 safety subscore."""
    if not safety.get("passed"):
        base = 10.0
    else:
        base = 70.0

    if safety.get("is_honeypot"):
        return 0.0

    if safety.get("type") == "evm":
        sell_tax = safety.get("sell_tax", 0)
        buy_tax = safety.get("buy_tax", 0)
        risk_level = safety.get("risk_level", 100)
        base += max(0, (MAX_SELL_TAX - sell_tax) / MAX_SELL_TAX * 10)
        base += max(0, (MAX_BUY_TAX - buy_tax) / MAX_BUY_TAX * 5)
        base += max(0, (20 - risk_level) / 20 * 15)
        if safety.get("open_source"):
            base += 5
        if not safety.get("is_proxy"):
            base += 5
    elif safety.get("type") == "solana":
        rug_score = safety.get("rug_score", 100)
        lp_locked = safety.get("lp_locked_pct", 0)
        base += max(0, (30 - rug_score) / 30 * 20)
        base += min(lp_locked / 100 * 10, 10)
        if not safety.get("mint_authority"):
            base += 5
        if not safety.get("freeze_authority"):
            base += 5

    return min(100.0, max(0.0, base))


MAX_SELL_TAX = 5.0
MAX_BUY_TAX = 10.0


def compute_momentum_score(pair: dict) -> float:
    """0-100 momentum from price changes."""
    changes = pair.get("priceChange") or {}
    h1 = _safe_float(changes.get("h1"))
    h6 = _safe_float(changes.get("h6"))
    h24 = _safe_float(changes.get("h24"))
    m5 = _safe_float(changes.get("m5"))

    score = 30.0
    if m5 > 5:
        score += min(m5 / 2, 15)
    if h1 > 10:
        score += min(h1 / 3, 20)
    elif h1 > 0:
        score += h1 / 5
    if h6 > 20:
        score += min(h6 / 5, 15)
    if h24 > 50:
        score += min(h24 / 10, 20)
    elif h24 < -30:
        score -= 20

    return min(100.0, max(0.0, score))


def compute_volume_score(pair: dict) -> float:
    """0-100 volume and buy pressure."""
    txns = pair.get("txns") or {}
    h1_txns = txns.get("h1") or {}
    h24_txns = txns.get("h24") or {}
    volume = pair.get("volume") or {}
    liquidity = _safe_float((pair.get("liquidity") or {}).get("usd"))

    buys_h1 = int(h1_txns.get("buys") or 0)
    sells_h1 = int(h1_txns.get("sells") or 0)
    buys_h24 = int(h24_txns.get("buys") or 0)
    sells_h24 = int(h24_txns.get("sells") or 0)
    vol_h1 = _safe_float(volume.get("h1"))
    vol_h24 = _safe_float(volume.get("h24"))

    score = 20.0

    if buys_h1 + sells_h1 > 0:
        ratio = buys_h1 / max(sells_h1, 1)
        if ratio >= 2:
            score += 25
        elif ratio >= 1.3:
            score += 15
        elif ratio < 0.7:
            score -= 15

    if vol_h24 > 50000:
        score += 20
    elif vol_h24 > 10000:
        score += 10
    elif vol_h24 > 1000:
        score += 5

    if liquidity > 0 and vol_h24 > 0:
        vol_liq_ratio = vol_h24 / liquidity
        if vol_liq_ratio > 5:
            score += 15
        elif vol_liq_ratio > 2:
            score += 10

    if buys_h24 > 100:
        score += min(buys_h24 / 50, 10)

    return min(100.0, max(0.0, score))


def compute_early_score(pair: dict, early_mode: bool = False) -> float:
    """0-100 — rewards newly launched tokens in sweet-spot mcap."""
    created = pair.get("pairCreatedAt")
    pump = pair.get("pumpfun") or {}
    mcap = _safe_float(pair.get("marketCap") or pair.get("fdv"))
    if pump.get("usd_market_cap"):
        mcap = float(pump["usd_market_cap"])
    liquidity = _safe_float((pair.get("liquidity") or {}).get("usd"))

    score = 20.0 if early_mode else 30.0
    age_minutes = None
    if created:
        age_minutes = (time.time() * 1000 - created) / 60_000
    elif pump.get("created_timestamp"):
        age_minutes = (time.time() * 1000 - pump["created_timestamp"]) / 60_000

    if age_minutes is not None:
        if age_minutes < 5:
            score += 45
        elif age_minutes < 15:
            score += 40
        elif age_minutes < 30:
            score += 30
        elif age_minutes < 60:
            score += 20
        elif age_minutes < 120:
            score += 10
        else:
            score -= 20

    if early_mode and pump:
        progress = _safe_float(pair.get("bonding_progress"))
        if not progress and pump:
            from services.pumpfun import PumpFunClient
            progress = PumpFunClient.bonding_progress(pump)
        if 3 <= progress <= 40:
            score += 20
        elif progress < 3:
            score += 10
        replies = int(pump.get("reply_count") or 0)
        if replies >= 3:
            score += 10
        elif replies >= 1:
            score += 5
        if not pump.get("complete"):
            score += 10

    if 2_000 <= mcap <= 25_000:
        score += 25
    elif 25_000 < mcap <= 55_000:
        score += 15
    elif mcap < 2_000:
        score += 10
    elif mcap > 10_000_000:
        score -= 15

    return min(100.0, max(0.0, score))


def compute_moon_score(
    safety: dict,
    pair: dict,
    weights: dict | None = None,
    early_mode: bool = False,
) -> dict[str, Any]:
    from config import (
        WEIGHT_EARLY,
        WEIGHT_MOMENTUM,
        WEIGHT_SAFETY,
        WEIGHT_VOLUME,
    )

    if early_mode:
        w = weights or {
            "safety": 0.30,
            "momentum": 0.15,
            "volume": 0.15,
            "early": 0.40,
        }
    else:
        w = weights or {
            "safety": WEIGHT_SAFETY,
            "momentum": WEIGHT_MOMENTUM,
            "volume": WEIGHT_VOLUME,
            "early": WEIGHT_EARLY,
        }

    safety_s = compute_safety_score(safety)
    momentum_s = compute_momentum_score(pair)
    volume_s = compute_volume_score(pair)
    early_s = compute_early_score(pair, early_mode=early_mode)

    total = (
        safety_s * w["safety"]
        + momentum_s * w["momentum"]
        + volume_s * w["volume"]
        + early_s * w["early"]
    )

    grade = "F"
    if total >= 80:
        grade = "A+"
    elif total >= 70:
        grade = "A"
    elif total >= 60:
        grade = "B"
    elif total >= 50:
        grade = "C"
    elif total >= 40:
        grade = "D"

    return {
        "total": round(total, 1),
        "grade": grade,
        "breakdown": {
            "safety": round(safety_s, 1),
            "momentum": round(momentum_s, 1),
            "volume": round(volume_s, 1),
            "early": round(early_s, 1),
        },
    }