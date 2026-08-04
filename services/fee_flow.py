"""Fee / volume quality — demand trail vs sniper wash.

Total fees paid / volume can signal real interest OR paid sniper wars.
We score **quality of flow**, not raw size:

  good  = sustained volume + two-way txs + sane age velocity
  bad   = flash volume/fees in first minutes + one-way wash

DexScreener volume USD is our free proxy for “fees paid economy”
(actual priority fees need Helius; volume is the correlated public signal).
"""

from __future__ import annotations

from typing import Any

from services.tx_activity import extract_tx_counts, score_tx_activity


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _i(x: Any, d: int = 0) -> int:
    try:
        return int(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _vol_map(market: dict[str, Any] | None) -> dict[str, float]:
    market = market or {}
    vol = market.get("volume") if isinstance(market.get("volume"), dict) else {}
    return {
        "m5": _f(vol.get("m5") or market.get("volume_m5")),
        "h1": _f(vol.get("h1") or market.get("volume_h1")),
        "h6": _f(vol.get("h6") or market.get("volume_h6")),
        "h24": _f(vol.get("h24") or market.get("volume_h24") or market.get("volume24h")),
    }


def _age_minutes(token: dict[str, Any]) -> float | None:
    for src in (
        token,
        token.get("pumpfun") or {},
        token.get("safety") or {},
    ):
        if not isinstance(src, dict):
            continue
        if src.get("age_minutes") is not None:
            try:
                a = float(src["age_minutes"])
                if a >= 0:
                    return a
            except (TypeError, ValueError):
                pass
        created = src.get("created_timestamp") or src.get("createdAt")
        if created is not None:
            try:
                import time

                ts = float(created)
                if ts > 1e12:
                    ts /= 1000.0
                age = (time.time() - ts) / 60.0
                if age >= 0:
                    return age
            except (TypeError, ValueError):
                pass
    return None


def analyze_fee_flow(token: dict[str, Any]) -> dict[str, Any]:
    """Score fee/volume quality 0–100 + flash_fee hard flag."""
    market = token.get("market") or {}
    if not market and token.get("priceChange"):
        market = {
            "priceChange": token.get("priceChange"),
            "txns": token.get("txns") or {},
            "volume": token.get("volume") or {},
        }
    pf = token.get("pumpfun") or {}
    mcap = _f(token.get("mcap_usd") or market.get("marketCap") or pf.get("usd_market_cap"))
    vols = _vol_map(market)
    age = _age_minutes(token)

    tx = token.get("txActivity")
    if not isinstance(tx, dict):
        tx = score_tx_activity(pair=market, pump=pf)
    counts = extract_tx_counts(market, pf)
    buys = _i(tx.get("buys_m5") or counts.get("buys_m5"))
    sells = _i(tx.get("sells_m5") or counts.get("sells_m5"))
    total_tx = buys + sells
    ratio = buys / max(sells, 1)

    vol_m5 = vols["m5"]
    vol_h1 = vols["h1"]
    vol_h24 = vols["h24"]

    # Prefer m5 for flash; fall back to h1 slice
    vol_focus = vol_m5 if vol_m5 > 0 else (vol_h1 / 12.0 if vol_h1 > 0 else 0.0)
    # Turnover = volume / mcap (how much of book traded)
    turnover_m5 = (vol_m5 / mcap) if mcap > 0 and vol_m5 > 0 else None
    turnover_h1 = (vol_h1 / mcap) if mcap > 0 and vol_h1 > 0 else None

    # Volume per minute (flash sniper wars print huge $/min early)
    vpm = None
    if age is not None and age > 0 and vol_focus > 0:
        vpm = vol_focus / max(age, 0.2)

    flags: list[str] = []
    reasons: list[str] = []
    score = 40  # neutral baseline when data thin
    quality = "unknown"  # organic | mixed | flash_sniper | wash | dead
    hard_reject = False

    two_way = sells >= 3 and buys >= 8 and ratio <= 3.5
    one_way = (sells <= 2 and buys >= 20) or ratio >= 5.0
    wash_zone = str(tx.get("zone") or "") in ("wash", "one_way")

    # --- Dead / no fee trail ---
    if vol_m5 <= 0 and vol_h1 <= 0 and total_tx < 8:
        quality = "dead"
        score = 15
        reasons.append("No volume/fee trail — no economic interest yet")
        flags.append("no_fee_trail")
    else:
        # --- Two-way sustained volume = good fee quality ---
        if two_way and not wash_zone:
            score += 22
            reasons.append(f"Two-way flow {buys}B/{sells}S — fees look real")
            flags.append("two_way_fees")
        if total_tx >= 25 and sells >= 4 and ratio <= 2.8:
            score += 12
            reasons.append(f"Healthy tape {total_tx} tx/5m with sells")
        if turnover_h1 is not None and 0.15 <= turnover_h1 <= 4.0 and two_way:
            score += 10
            reasons.append(f"Sustained turnover {turnover_h1:.1f}x mcap/1h")
            flags.append("sustained_turnover")
        if vol_h1 > 0 and vol_m5 > 0 and vol_h1 >= vol_m5 * 1.5 and two_way:
            score += 8
            reasons.append("Volume persisted beyond last 5m — not only a flash")
            flags.append("volume_persisted")

        # --- Flash fee / volume (bad when too young) ---
        flash = False
        if age is not None and age <= 3.0:
            if vol_m5 >= 15_000 or (vpm is not None and vpm >= 8_000):
                flash = True
            if total_tx >= 80 and age <= 2.5:
                flash = True
            if turnover_m5 is not None and turnover_m5 >= 2.0 and age <= 3:
                flash = True
        elif age is not None and age <= 8.0:
            if vpm is not None and vpm >= 12_000 and one_way:
                flash = True
            if vol_m5 >= 40_000 and (one_way or wash_zone):
                flash = True

        if flash:
            hard_reject = True
            quality = "flash_sniper"
            score = min(score, 25)
            score -= 30
            flags.append("flash_fees")
            reasons.append(
                f"Flash fee/volume war @ {age:.1f}m"
                + (f" (~${vpm:,.0f}/min)" if vpm else "")
                + " — snipers paying for fills, not organic demand"
            )

        # --- Wash fees ---
        if one_way or wash_zone:
            score -= 20
            flags.append("wash_fees")
            reasons.append(
                f"One-way fee trail {buys}B/{sells}S — wash/bot fill, not community"
            )
            if quality != "flash_sniper":
                quality = "wash"
            if buys >= 40 and sells <= 3:
                hard_reject = True

        # --- Extreme turnover without two-way ---
        if turnover_m5 is not None and turnover_m5 >= 5 and not two_way:
            score -= 15
            flags.append("churn_fees")
            reasons.append(
                f"Turnover {turnover_m5:.1f}x mcap/5m without two-way — churn farm"
            )

        # --- Positive organic label ---
        if (
            not flash
            and not one_way
            and not wash_zone
            and two_way
            and (vol_m5 >= 500 or vol_h1 >= 3_000 or total_tx >= 20)
        ):
            quality = "organic"
            flags.append("organic_fee_trail")
            if "two_way_fees" not in reasons[0] if reasons else True:
                reasons.insert(0, "Organic fee/volume trail (two-way + activity)")
        elif quality == "unknown" and (vol_m5 > 0 or total_tx >= 15):
            quality = "mixed"

    score = int(max(0, min(100, score)))
    # Soft score boost for moons: organic high quality
    score_boost = 0
    if quality == "organic" and score >= 55:
        score_boost = 6
    elif quality == "organic":
        score_boost = 3
    if hard_reject or quality in ("flash_sniper", "wash"):
        score_boost = -12 if hard_reject else -6

    return {
        "quality": quality,
        "score": score,
        "score_boost": score_boost,
        "hard_reject": hard_reject,
        "flags": flags,
        "reasons": reasons[:6],
        "summary": reasons[0] if reasons else "Fee/volume unknown",
        "vol_m5_usd": round(vol_m5, 2) if vol_m5 else 0,
        "vol_h1_usd": round(vol_h1, 2) if vol_h1 else 0,
        "vol_h24_usd": round(vol_h24, 2) if vol_h24 else 0,
        "vol_per_min_usd": round(vpm, 2) if vpm is not None else None,
        "turnover_m5": round(turnover_m5, 3) if turnover_m5 is not None else None,
        "turnover_h1": round(turnover_h1, 3) if turnover_h1 is not None else None,
        "buys_m5": buys,
        "sells_m5": sells,
        "buy_ratio_m5": round(ratio, 2),
        "two_way": two_way,
        "age_minutes": round(age, 2) if age is not None else None,
        "proxy_note": "volume USD used as public proxy for fee economy (no Helius priority fees)",
    }


def attach_fee_flow(token: dict[str, Any]) -> dict[str, Any]:
    ff = analyze_fee_flow(token)
    token["feeFlow"] = ff
    return ff


def fee_flow_gate(ff: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Money-mode: block flash sniper fee wars."""
    if not isinstance(ff, dict):
        return True, None
    if ff.get("hard_reject") or "flash_fees" in (ff.get("flags") or []):
        return False, ff.get("summary") or "flash fee/volume war"
    if ff.get("quality") == "wash" and int(ff.get("buys_m5") or 0) >= 40:
        return False, ff.get("summary") or "wash fee trail"
    return True, None


def format_fee_telegram(ff: dict[str, Any] | None) -> str:
    if not isinstance(ff, dict):
        return ""
    q = ff.get("quality") or "unknown"
    badge = {
        "organic": "⭐ organic",
        "flash_sniper": "⚠ FLASH",
        "wash": "⚠ wash",
        "dead": "dead",
        "mixed": "mixed",
    }.get(q, q)
    vol = ff.get("vol_m5_usd") or 0
    vpm = ff.get("vol_per_min_usd")
    vpm_s = f" · ~${vpm:,.0f}/min" if vpm else ""
    flow = f"{ff.get('buys_m5', 0)}B/{ff.get('sells_m5', 0)}S"
    return (
        f"\n💸 <b>FLOW</b> {badge} · score {ff.get('score', 0)}\n"
        f"vol5m ${vol:,.0f}{vpm_s} · {flow}"
        + (" · two-way" if ff.get("two_way") else "")
    )


def fee_score_boost(token: dict[str, Any]) -> int:
    ff = token.get("feeFlow")
    if not isinstance(ff, dict):
        try:
            ff = attach_fee_flow(token)
        except Exception:
            return 0
    try:
        return int(ff.get("score_boost") or 0)
    except (TypeError, ValueError):
        return 0
