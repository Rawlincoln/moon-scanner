"""Entry, exit, and unified invest signal generation."""

from __future__ import annotations

import time
from typing import Any

from config import REQUIRE_TRENCH_GATE_FOR_INVEST, TARGET_MCAP_USD
from services.market_analyzer import analyze_market
from services.padre_feed import source_label
from services.trench_analyzer import run_trench_gate


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def generate_entry_signal(
    safety: dict,
    pair: dict,
    moon_score: dict,
    early_mode: bool = False,
) -> dict[str, Any]:
    """Produce entry recommendation with reasoning."""
    total = moon_score.get("total", 0)
    passed = safety.get("passed", False)
    is_honeypot = safety.get("is_honeypot", False)

    changes = pair.get("priceChange") or {}
    h1 = _safe_float(changes.get("h1"))
    m5 = _safe_float(changes.get("m5"))

    txns = pair.get("txns") or {}
    h1_txns = txns.get("h1") or {}
    buys = int(h1_txns.get("buys") or 0)
    sells = int(h1_txns.get("sells") or 0)
    buy_ratio = buys / max(sells, 1)

    liquidity = _safe_float((pair.get("liquidity") or {}).get("usd"))
    vol_h1 = _safe_float((pair.get("volume") or {}).get("h1"))

    reasons: list[str] = []

    if is_honeypot:
        return {
            "signal": "AVOID",
            "confidence": 95,
            "reasons": ["Honeypot — do not buy"],
            "action": "Do not enter. Token cannot be sold.",
        }

    if not passed:
        return {
            "signal": "AVOID",
            "confidence": 85,
            "reasons": safety.get("issues", ["Failed safety checks"])[:5],
            "action": "Wait for safety clearance before considering entry.",
        }

    signal = "WATCH"
    confidence = 40

    if total >= 75 and buy_ratio >= 1.5 and h1 > 5:
        signal = "STRONG_BUY"
        confidence = min(90, int(total))
        reasons.append(f"Moon score {total} with strong buy pressure")
        reasons.append(f"1h price +{h1:.1f}%, buy/sell ratio {buy_ratio:.1f}x")
    elif total >= 65 and buy_ratio >= 1.2:
        signal = "BUY"
        confidence = min(80, int(total))
        reasons.append(f"Solid moon score ({total})")
        reasons.append("Positive buy/sell ratio in last hour")
    elif total >= 50 and passed:
        signal = "WATCH"
        confidence = 55
        reasons.append("Passes safety but needs momentum confirmation")
        reasons.append("Wait for volume spike or breakout above resistance")
    else:
        signal = "WATCH"
        confidence = 35
        reasons.append("Low moon potential score — early or weak momentum")

    if m5 > 15 and vol_h1 > liquidity * 0.3:
        reasons.append("Volume spike detected in last 5 min — early entry window")
        if signal == "WATCH":
            signal = "BUY"
            confidence += 10

    if h1 > 80:
        reasons.append("Already pumped hard — consider waiting for pullback")
        if signal == "STRONG_BUY":
            signal = "BUY"
        confidence -= 10

    pump = pair.get("pumpfun") or {}
    created = pair.get("pairCreatedAt") or pump.get("created_timestamp")
    if created:
        age_min = (time.time() * 1000 - created) / 60_000
        if age_min < 15:
            pump_mcap = float((pair.get("pumpfun") or {}).get("usd_market_cap") or 0)
            if pump_mcap >= 4000:
                reasons.append(
                    f"Young token ({age_min:.0f}m) climbing toward $6k"
                )
            else:
                reasons.append(
                    f"Too early ({age_min:.0f}m, ${pump_mcap:,.0f}) — "
                    "wait for $4k+ approach, high rug risk"
                )
        elif age_min < 60:
            reasons.append(f"Young token ({age_min:.0f}m) — still early")

    if pump and not pump.get("complete"):
        progress = float(pair.get("bonding_progress") or 0)
        if not progress:
            from services.pumpfun import PumpFunClient
            progress = PumpFunClient.bonding_progress(pump)
        reasons.append(f"Bonding curve {progress:.0f}% — pre-graduation")
        if progress < 10:
            reasons.append(
                "Sub-10% curve — sniper rug zone, wait for $6k approach"
            )

    entry_zone = _compute_entry_zone(pair)

    return {
        "signal": signal,
        "confidence": min(95, max(10, confidence)),
        "reasons": reasons[:6],
        "action": _action_text(signal),
        "entry_zone": entry_zone,
    }


def generate_exit_signal(
    safety: dict, pair: dict, moon_score: dict
) -> dict[str, Any]:
    """Produce exit recommendation."""
    changes = pair.get("priceChange") or {}
    h1 = _safe_float(changes.get("h1"))
    h6 = _safe_float(changes.get("h6"))
    h24 = _safe_float(changes.get("h24"))
    m5 = _safe_float(changes.get("m5"))

    txns = pair.get("txns") or {}
    h1_txns = txns.get("h1") or {}
    buys = int(h1_txns.get("buys") or 0)
    sells = int(h1_txns.get("sells") or 0)
    sell_ratio = sells / max(buys, 1)

    liquidity = _safe_float((pair.get("liquidity") or {}).get("usd"))
    vol_h1 = _safe_float((pair.get("volume") or {}).get("h1"))
    price = _safe_float(pair.get("priceUsd"))

    reasons: list[str] = []
    signal = "HOLD"
    confidence = 50

    if safety.get("is_honeypot"):
        return {
            "signal": "EMERGENCY_EXIT",
            "confidence": 99,
            "reasons": ["Honeypot confirmed"],
            "action": "Cannot exit — funds likely lost.",
            "targets": {},
        }

    if not safety.get("passed"):
        signal = "SELL"
        confidence = 80
        reasons.extend(safety.get("issues", [])[:3])
        reasons.append("Safety checks failing — exit recommended")

    elif h1 > 100 and sell_ratio > 1.5:
        signal = "TAKE_PROFIT"
        confidence = 85
        reasons.append(f"Up {h1:.0f}% in 1h with heavy selling — take profits")
    elif h24 > 200:
        signal = "TAKE_PROFIT"
        confidence = 75
        reasons.append(f"Up {h24:.0f}% in 24h — secure gains, trail stop")
    elif h1 < -25 and sell_ratio > 2:
        signal = "STOP_LOSS"
        confidence = 80
        reasons.append(f"Down {abs(h1):.0f}% in 1h with sell pressure")
    elif m5 < -15 and sells > buys * 2:
        signal = "SELL"
        confidence = 70
        reasons.append("Sharp 5m dump with heavy sells")
    elif h6 > 50 and vol_h1 < liquidity * 0.05:
        signal = "TAKE_PROFIT"
        confidence = 65
        reasons.append("Momentum fading — volume declining after pump")
    elif moon_score.get("total", 0) >= 70 and h1 > 0:
        signal = "HOLD"
        confidence = 60
        reasons.append("Trend intact — hold with trailing stop")
    else:
        signal = "HOLD"
        reasons.append("No clear exit trigger — monitor closely")

    targets = _compute_exit_targets(pair, price)

    if signal == "HOLD" and h24 > 50:
        reasons.append(f"Consider trailing stop at -15% from current (${price:.8f})")

    return {
        "signal": signal,
        "confidence": min(95, max(10, confidence)),
        "reasons": reasons[:6],
        "action": _exit_action_text(signal),
        "targets": targets,
    }


def _compute_entry_zone(pair: dict) -> dict:
    price = _safe_float(pair.get("priceUsd"))
    if price <= 0:
        return {}
    return {
        "aggressive": round(price * 1.02, 10),
        "ideal": round(price * 0.97, 10),
        "conservative": round(price * 0.92, 10),
        "current": price,
        "note": "Ideal = small dip entry; aggressive = chase only on STRONG_BUY",
    }


def _compute_exit_targets(pair: dict, price: float) -> dict:
    if price <= 0:
        return {}
    changes = pair.get("priceChange") or {}
    h24 = _safe_float(changes.get("h24"))

    tp1_mult = 1.5 if h24 < 30 else 1.25
    tp2_mult = 2.5 if h24 < 50 else 2.0
    tp3_mult = 5.0

    return {
        "take_profit_1": round(price * tp1_mult, 10),
        "take_profit_2": round(price * tp2_mult, 10),
        "take_profit_3": round(price * tp3_mult, 10),
        "stop_loss": round(price * 0.80, 10),
        "trailing_stop_pct": 15,
        "current": price,
    }


def _action_text(signal: str) -> str:
    actions = {
        "STRONG_BUY": "Enter now with defined position size. Use ideal entry zone on dips.",
        "BUY": "Enter on confirmation candle or small pullback to ideal zone.",
        "WATCH": "Add to watchlist. Set alerts for volume breakout.",
        "AVOID": "Do not trade this token.",
    }
    return actions.get(signal, "Monitor.")


def _exit_action_text(signal: str) -> str:
    actions = {
        "TAKE_PROFIT": "Scale out 30-50% at TP1, trail remainder.",
        "SELL": "Exit position. Do not average down.",
        "STOP_LOSS": "Cut losses immediately. Preserve capital.",
        "HOLD": "Maintain position with trailing stop.",
        "EMERGENCY_EXIT": "Funds may be unrecoverable.",
    }
    return actions.get(signal, "Monitor exit levels.")


def generate_invest_signal(
    safety: dict,
    pair: dict,
    moon_score: dict,
    sources: list[str] | None = None,
    early_mode: bool = True,
) -> dict[str, Any]:
    """Unified invest/exit recommendation from live market + dev behaviour."""
    market = analyze_market(pair, safety, sources=sources)
    trench = run_trench_gate(safety, pair, sources=sources)
    entry = generate_entry_signal(safety, pair, moon_score, early_mode=early_mode)
    exit_sig = generate_exit_signal(safety, pair, moon_score)

    vol = market["volume"]
    dev = market["dev"]
    bonding = market["bonding"]
    pressure = market["buy_pressure"]
    src = market["sources"]
    snipers = trench.get("snipers") or {}

    reasons: list[str] = []
    exit_reasons: list[str] = []
    signal = "WATCH"
    confidence = 40

    # Hard avoids
    if safety.get("is_honeypot"):
        return _invest_result(
            "AVOID", 95, ["Honeypot — cannot sell"],
            "Do not enter.", market, entry, exit_sig, early_mode, trench=trench
        )
    if safety.get("rugged"):
        return _invest_result(
            "AVOID", 95, ["RugCheck flagged as rugged"],
            "Do not enter — already rugged.", market, entry, exit_sig, early_mode,
            trench=trench,
        )
    if not safety.get("passed"):
        return _invest_result(
            "AVOID", 85,
            safety.get("issues", ["Failed safety checks"])[:4],
            "Wait for safety clearance.", market, entry, exit_sig, early_mode,
            trench=trench,
        )

    if snipers.get("risk_level") == "critical":
        return _invest_result(
            "AVOID", 92,
            [f"Insider/sniper wallets: {snipers.get('insider_count', 0)} detected"],
            "Bundled snipers — instant rug risk.",
            market, entry, exit_sig, early_mode, trench=trench,
        )

    # Exit triggers (market-time analysis)
    if dev.get("dev_dumping"):
        return _invest_result(
            "DEV_DUMP_WARNING", 90,
            dev.get("dev_dump_reasons", ["Dev wallet activity detected"]),
            "Exit immediately — dev selling or insider risk.",
            market, entry, exit_sig, early_mode,
            exit_trigger=True, trench=trench,
        )

    if vol["trend"] == "dead" and vol["decay_pct"] >= 70:
        exit_reasons.append(
            f"Volume dead — down {vol['decay_pct']:.0f}% vs 1h average"
        )
        return _invest_result(
            "EXIT_NOW", 85, exit_reasons,
            "Volume collapsed. Exit before liquidity dries up.",
            market, entry, exit_sig, early_mode, exit_trigger=True, trench=trench,
        )

    if vol["trend"] == "slowing" and vol["decay_pct"] >= 55:
        exit_reasons.append(
            f"Volume slowing — {vol['decay_pct']:.0f}% below 1h pace"
        )
        if pressure["trend"] == "sellers_increasing":
            exit_reasons.append("Sell pressure rising in last 5m")
            return _invest_result(
                "EXIT_NOW", 80, exit_reasons,
                "Momentum fading + sellers dominating. Take profit or exit.",
                market, entry, exit_sig, early_mode, exit_trigger=True, trench=trench,
            )

    if dev.get("risk_level") == "critical":
        exit_reasons.extend(dev.get("dev_dump_reasons", [])[:3])
        return _invest_result(
            "DEV_DUMP_WARNING", 88, exit_reasons,
            "Critical dev risk — do not hold.",
            market, entry, exit_sig, early_mode, exit_trigger=True, trench=trench,
        )

    if exit_sig["signal"] in ("STOP_LOSS", "SELL", "EMERGENCY_EXIT"):
        return _invest_result(
            "EXIT_NOW", exit_sig["confidence"],
            exit_sig.get("reasons", [])[:4],
            exit_sig.get("action", "Exit position."),
            market, entry, exit_sig, early_mode, exit_trigger=True, trench=trench,
        )

    if exit_sig["signal"] == "TAKE_PROFIT":
        return _invest_result(
            "TAKE_PROFIT", exit_sig["confidence"],
            exit_sig.get("reasons", [])[:4],
            exit_sig.get("action", "Scale out profits."),
            market, entry, exit_sig, early_mode, exit_trigger=True, trench=trench,
        )

    # Trench gate — mandatory for any INVEST recommendation
    if REQUIRE_TRENCH_GATE_FOR_INVEST and not trench.get("passed"):
        failures = trench.get("failures", ["Failed trench checks"])
        return _invest_result(
            "WATCH",
            max(20, int(trench.get("trench_score", 30))),
            failures[:6],
            f"Not ready — wait for ${TARGET_MCAP_USD:,} approach with real buyers. "
            f"{trench.get('verdict', '')}",
            market, entry, exit_sig, early_mode, trench=trench,
        )

    # Invest scoring (only reached if trench gate passed)
    trench_score = float(trench.get("trench_score", 0))
    confidence = int(trench_score * 0.7) + src.get("confidence_boost", 0)

    mcap = trench.get("mcap_usd", 0)
    reasons.append(
        f"MCap ${mcap:,.0f} approaching ${TARGET_MCAP_USD:,} "
        f"({trench.get('price_change_m5', 0):+.1f}% in 5m)"
    )
    reasons.append(
        f"Real Dex volume ${trench.get('volume_m5', 0):,.0f} in 5m "
        f"— not synthetic data"
    )

    comm = trench.get("community") or {}
    if comm.get("reply_count", 0) > 0:
        reasons.append(f"{comm['reply_count']} pump.fun replies — community present")
    reasons.append(
        f"{comm.get('buys_m5', 0)} buys / {comm.get('sells_m5', 0)} sells in 5m"
    )

    if snipers.get("risk_level") == "low":
        reasons.append(
            f"No insider snipers — largest wallet {snipers.get('max_wallet_pct', 0):.1f}%"
        )
    holders = safety.get("total_holders", 0)
    reasons.append(f"{holders} holders — distributed beyond snipers")

    if vol["trend"] == "accelerating" and market.get("data_quality") == "real_dex":
        confidence += 8
        reasons.append(f"Volume accelerating ({vol['velocity']:.1f}x)")

    for r in src.get("reasons", [])[:2]:
        if r not in reasons:
            reasons.append(r)

    confidence = min(95, max(10, confidence))

    dist = trench.get("mcap_distance_pct", 100)
    if (
        trench.get("passed")
        and confidence >= 70
        and dist <= 20
        and trench.get("price_change_m5", 0) >= 10
        and snipers.get("risk_level") == "low"
    ):
        signal = "STRONG_INVEST"
    elif trench.get("passed") and confidence >= 55:
        signal = "INVEST"
    else:
        signal = "WATCH"
        reasons.extend(trench.get("failures", [])[:3])

    action_map = {
        "STRONG_INVEST": (
            f"Enter as mcap climbs through ${TARGET_MCAP_USD:,}. "
            "Size 0.5-1%. Stop -15%. Exit if volume decays 50%+ or dev sells."
        ),
        "INVEST": (
            f"Qualified ${TARGET_MCAP_USD:,} approach — enter small, "
            "exit immediately on volume slowdown or sniper dump."
        ),
        "WATCH": (
            f"Wait for ${TARGET_MCAP_USD:,} approach with real Dex volume. "
            "Do NOT enter sub-$4k launches."
        ),
        "AVOID": "Do not enter — rug risk too high.",
    }

    return _invest_result(
        signal, confidence, reasons[:8],
        action_map.get(signal, "Monitor."),
        market, entry, exit_sig, early_mode, trench=trench,
    )


def _invest_result(
    signal: str,
    confidence: int,
    reasons: list[str],
    action: str,
    market: dict,
    entry: dict,
    exit_sig: dict,
    early_mode: bool,
    exit_trigger: bool = False,
    trench: dict | None = None,
) -> dict[str, Any]:
    sources = market.get("sources", {})
    source_badges = [
        {"id": s, "label": source_label(s)} for s in sources.get("list", [])
    ]

    timing = "now"
    if signal in ("STRONG_INVEST", "INVEST"):
        if market["bonding"].get("stage") == "early_curve" and early_mode:
            timing = "immediate_early"
        elif market["volume"]["trend"] == "accelerating":
            timing = "immediate"
        else:
            timing = "on_dip"
    elif signal in ("EXIT_NOW", "DEV_DUMP_WARNING", "TAKE_PROFIT"):
        timing = "exit_immediately"
    else:
        timing = "wait"

    return {
        "signal": signal,
        "confidence": confidence,
        "reasons": reasons,
        "action": action,
        "timing": timing,
        "exit_trigger": exit_trigger,
        "market": market,
        "trench": trench,
        "entry": entry,
        "exit": exit_sig,
        "source_badges": source_badges,
        "summary": _invest_summary(signal, market, early_mode, trench),
    }


def _invest_summary(
    signal: str, market: dict, early_mode: bool, trench: dict | None = None
) -> str:
    vol = market["volume"]["trend"]
    dev = market["dev"]["risk_level"]
    trench = trench or {}
    mcap = trench.get("mcap_usd", 0)
    dq = market.get("data_quality", "unknown")

    if signal == "STRONG_INVEST":
        return (
            f"Approaching ${TARGET_MCAP_USD:,} — mcap ${mcap:,.0f}, "
            f"vol {vol}, dev {dev}, real {dq}"
        )
    if signal == "INVEST":
        return f"Qualified $6k climb — mcap ${mcap:,.0f}, vol {vol}, dev {dev}"
    if signal == "WATCH" and trench.get("failures"):
        return trench.get("verdict", "Waiting for $6k approach")
    if signal == "EXIT_NOW":
        return f"Exit — volume {vol} ({market['volume']['decay_pct']:.0f}% decay)"
    if signal == "DEV_DUMP_WARNING":
        return f"Dev risk critical — {', '.join(market['dev'].get('dev_dump_reasons', [])[:2])}"
    if signal == "TAKE_PROFIT":
        return "Take profits — momentum peaked or fading"
    if signal == "WATCH":
        return f"Not ready — wait for volume pickup or safer dev profile"
    return "Avoid — safety or market conditions unfavourable"