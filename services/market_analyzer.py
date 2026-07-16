"""Live market + dev behaviour analysis for invest/exit decisions."""

from __future__ import annotations

from typing import Any


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def analyze_market(
    pair: dict,
    safety: dict,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Compute volume velocity, buy pressure, dev behaviour, source overlap."""
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    changes = pair.get("priceChange") or {}
    pump = pair.get("pumpfun") or {}

    vol_m5 = _safe_float(volume.get("m5"))
    vol_h1 = _safe_float(volume.get("h1"))
    vol_h24 = _safe_float(volume.get("h24"))

    m5_txns = txns.get("m5") or {}
    h1_txns = txns.get("h1") or {}
    m5_buys = int(m5_txns.get("buys") or 0)
    m5_sells = int(m5_txns.get("sells") or 0)
    h1_buys = int(h1_txns.get("buys") or 0)
    h1_sells = int(h1_txns.get("sells") or 0)

    # Volume velocity: m5 rate vs average 5m slice of h1
    h1_per_5m = vol_h1 / 12.0 if vol_h1 > 0 else 0.0
    if h1_per_5m > 0:
        volume_velocity = vol_m5 / h1_per_5m
    elif vol_m5 > 0:
        volume_velocity = 2.0
    else:
        volume_velocity = 0.0

    if volume_velocity >= 1.3:
        volume_trend = "accelerating"
    elif volume_velocity >= 0.7:
        volume_trend = "stable"
    elif volume_velocity >= 0.35:
        volume_trend = "slowing"
    else:
        volume_trend = "dead"

    volume_decay_pct = 0.0
    if h1_per_5m > 0 and vol_m5 < h1_per_5m:
        volume_decay_pct = round((1 - vol_m5 / h1_per_5m) * 100, 1)

    synthetic = bool(pair.get("is_pumpfun_synthetic"))
    buy_ratio_h1 = h1_buys / max(h1_sells, 1)
    buy_ratio_m5 = m5_buys / max(m5_sells, 1)
    buy_pressure_shift = buy_ratio_m5 - buy_ratio_h1

    if synthetic:
        replies = int(pump.get("reply_count") or 0)
        if replies >= 3 or volume_velocity >= 1.2:
            pressure_trend = "buyers_increasing"
            buy_pressure_shift = 0.5
        elif replies == 0 and vol_m5 == 0:
            pressure_trend = "neutral"
            buy_pressure_shift = 0.0
        else:
            pressure_trend = "neutral"
    elif buy_pressure_shift > 0.5:
        pressure_trend = "buyers_increasing"
    elif buy_pressure_shift < -0.5:
        pressure_trend = "sellers_increasing"
    else:
        pressure_trend = "neutral"

    dev = _analyze_dev_behavior(safety, pump, on_bonding_curve=bool(
        safety.get("on_bonding_curve") or (pump and not pump.get("complete"))
    ))
    bonding = _bonding_analysis(pair, pump)

    src_list = sources or []
    has_trending = "padre_trending" in src_list
    has_new_pairs = "padre_new_pairs" in src_list
    has_almost_bonded = "padre_trenches_almost_bonded" in src_list

    # padre_trenches_new duplicates pump.fun — do NOT count as separate source
    real_sources = {
        s for s in src_list if s not in ("padre_trenches_new", "pump.fun")
    }
    real_overlap = len(real_sources) + (1 if "pump.fun" in src_list else 0)

    confidence_boost = 0
    overlap_reasons: list[str] = []
    if has_trending:
        confidence_boost += 12
        overlap_reasons.append("On Padre Trending (DexScreener boosts)")
    if has_new_pairs:
        confidence_boost += 8
        overlap_reasons.append("DexScreener new profile (Padre New Pairs)")
    if has_almost_bonded:
        confidence_boost += 6
        overlap_reasons.append("Approaching bonding graduation")
    if len(real_sources) >= 2:
        confidence_boost += 10
        overlap_reasons.append(f"Multi-feed confirmation ({len(real_sources)} unique)")

    flags: list[str] = []
    if volume_trend in ("slowing", "dead"):
        flags.append(f"volume_{volume_trend}")
    if dev.get("risk_level") in ("high", "critical"):
        flags.append("dev_risk")
    if dev.get("dev_dumping"):
        flags.append("dev_dump")
    if pressure_trend == "sellers_increasing":
        flags.append("sell_pressure")
    if safety.get("mint_authority"):
        flags.append("mint_authority_active")

    return {
        "volume": {
            "m5": vol_m5,
            "h1": vol_h1,
            "h24": vol_h24,
            "velocity": round(volume_velocity, 2),
            "trend": volume_trend,
            "decay_pct": volume_decay_pct,
        },
        "buy_pressure": {
            "ratio_m5": round(buy_ratio_m5, 2),
            "ratio_h1": round(buy_ratio_h1, 2),
            "shift": round(buy_pressure_shift, 2),
            "trend": pressure_trend,
        },
        "price_change": {
            "m5": _safe_float(changes.get("m5")),
            "h1": _safe_float(changes.get("h1")),
            "h24": _safe_float(changes.get("h24")),
        },
        "dev": dev,
        "bonding": bonding,
        "sources": {
            "list": src_list,
            "overlap_count": real_overlap,
            "real_source_count": len(real_sources),
            "confidence_boost": confidence_boost,
            "reasons": overlap_reasons,
        },
        "data_quality": (
            "real_dex"
            if not synthetic
            else "synthetic_unverified"
        ),
        "flags": flags,
    }


def _analyze_dev_behavior(
    safety: dict, pump: dict, on_bonding_curve: bool = False
) -> dict[str, Any]:
    creator = safety.get("creator") or pump.get("creator")
    creator_balance = _safe_float(safety.get("creator_balance"))
    creator_pct = _safe_float(safety.get("creator_pct"))
    top_holders = safety.get("top_holders") or []
    insider_detected = bool(safety.get("insider_detected"))
    insider_networks = int(safety.get("insider_networks") or 0)
    creator_tokens = int(safety.get("creator_token_count") or 0)

    top10_pct = sum(_safe_float(h.get("pct")) for h in top_holders[:10])
    dev_in_top = False
    for h in top_holders[:10]:
        owner = h.get("owner") or h.get("address") or ""
        if creator and creator in str(owner):
            dev_in_top = True
            creator_pct = max(creator_pct, _safe_float(h.get("pct")))

    dev_dumping = False
    dev_dump_reasons: list[str] = []

    if creator_pct > 15 and safety.get("creator_sold"):
        dev_dumping = True
        dev_dump_reasons.append("Creator wallet sold tokens")
    if creator_pct > 20:
        dev_dump_reasons.append(f"Dev still holds {creator_pct:.1f}% — high dump risk")
    if insider_detected:
        dev_dump_reasons.append("Insider wallets detected (RugCheck)")
    if insider_networks > 0:
        dev_dump_reasons.append(f"{insider_networks} insider network(s) linked")
    if safety.get("mint_authority"):
        dev_dump_reasons.append("Mint authority active — dev can inflate supply")
    if creator_tokens > 50:
        dev_dump_reasons.append(f"Serial deployer ({creator_tokens} tokens launched)")

    risk_level = "low"
    if dev_dumping or safety.get("mint_authority") or insider_detected:
        risk_level = "critical"
    elif on_bonding_curve:
        # On pump.fun curve, 100% top-holder is the bonding pool — not a rug signal
        if creator_pct > 15 or insider_networks > 0:
            risk_level = "high"
        elif creator_pct > 8:
            risk_level = "medium"
        else:
            risk_level = "low"
    elif creator_pct > 10 or top10_pct > 60 or insider_networks > 0:
        risk_level = "high"
    elif creator_pct > 5 or top10_pct > 45:
        risk_level = "medium"

    return {
        "creator": creator,
        "creator_pct": round(creator_pct, 2),
        "creator_balance": creator_balance,
        "top10_pct": round(top10_pct, 2),
        "dev_in_top_holders": dev_in_top,
        "insider_detected": insider_detected,
        "insider_networks": insider_networks,
        "creator_token_count": creator_tokens,
        "dev_dumping": dev_dumping,
        "dev_dump_reasons": dev_dump_reasons,
        "risk_level": risk_level,
    }


def _bonding_analysis(pair: dict, pump: dict) -> dict[str, Any]:
    if not pump:
        return {"on_curve": False}

    from services.pumpfun import PumpFunClient

    progress = _safe_float(pair.get("bonding_progress"))
    if not progress:
        progress = PumpFunClient.bonding_progress(pump)

    complete = bool(pump.get("complete"))
    if complete:
        stage = "graduated"
    elif progress >= 75:
        stage = "almost_bonded"
    elif progress >= 30:
        stage = "mid_curve"
    else:
        stage = "early_curve"

    return {
        "on_curve": not complete,
        "progress_pct": round(progress, 1),
        "stage": stage,
        "reply_count": int(pump.get("reply_count") or 0),
        "usd_market_cap": _safe_float(pump.get("usd_market_cap")),
    }