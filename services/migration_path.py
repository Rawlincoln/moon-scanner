"""Migration / graduation readiness — tokens that can actually leave the curve.

pump.fun migrates around ~$69k mcap. Most $3–8k "MEGA" picks never get there.
This module scores how close a token is to a real migration path.
"""

from __future__ import annotations

from typing import Any

from config import (
    GRADUATION_MCAP_USD,
    MIGRATION_ALMOST_MIN_PCT,
    MIGRATION_CLIMBING_MIN_PCT,
    MIGRATION_NEAR_MIN_PCT,
)


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def bonding_pct_from_mcap(mcap: float) -> float:
    if mcap <= 0:
        return 0.0
    return min(100.0, (mcap / GRADUATION_MCAP_USD) * 100.0)


def analyze_migration_path(
    *,
    mcap_usd: float = 0.0,
    bonding_progress: float | None = None,
    safety: dict | None = None,
    pair: dict | None = None,
    pump: dict | None = None,
    avoid: dict | None = None,
    alpha: dict | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    """Score 0–100 migration readiness + lane for UI sections."""
    safety = safety or {}
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    avoid = avoid or safety.get("avoid") or {}
    alpha = alpha or {}

    mcap = mcap_usd or _f(pump.get("usd_market_cap") or pair.get("marketCap"))
    bond = bonding_progress
    if bond is None:
        bond = _f(pump.get("bonding_progress"))
    if bond <= 0 and mcap > 0:
        bond = bonding_pct_from_mcap(mcap)

    if complete or pump.get("complete"):
        return {
            "score": 70,
            "lane": "migrated",
            "bonding_pct": 100.0,
            "summary": "Already migrated / graduated — different trade (post-curve)",
            "recommend": "WATCH",
            "badges": [{"id": "migrated", "label": "Migrated", "type": "migration"}],
        }

    if avoid.get("hard_avoid") or avoid.get("avoid"):
        return {
            "score": 0,
            "lane": "skip",
            "bonding_pct": round(bond, 1),
            "summary": "Avoid filter — not a migration candidate",
            "recommend": "SKIP",
            "badges": [],
        }

    quote_sol = _f(safety.get("lp_quote_sol"))
    holders = int(safety.get("total_holders") or 0)
    txns = (pair.get("txns") or {}).get("m5") or {}
    buys = int(txns.get("buys") or 0)
    sells = int(txns.get("sells") or 0)
    buy_ratio = buys / max(sells, 1)
    two_way = buys >= 6 and sells >= 2 and buy_ratio <= 4.0
    one_way = buys >= 15 and sells == 0

    score = 0
    reasons: list[str] = []
    badges: list[dict[str, str]] = []

    # Bonding progress is the main signal for "can this migrate?"
    if bond >= MIGRATION_ALMOST_MIN_PCT:
        score += 40
        reasons.append(f"Almost bonded {bond:.0f}% — near migration")
        badges.append({"id": "almost", "label": f"{bond:.0f}% bonded", "type": "migration"})
    elif bond >= MIGRATION_NEAR_MIN_PCT:
        score += 32
        reasons.append(f"Near migration {bond:.0f}% (~${mcap:,.0f})")
        badges.append({"id": "near", "label": f"{bond:.0f}% path", "type": "migration"})
    elif bond >= MIGRATION_CLIMBING_MIN_PCT:
        score += 20
        reasons.append(f"Climbing curve {bond:.0f}%")
        badges.append({"id": "climb", "label": f"{bond:.0f}% climb", "type": "migration"})
    elif bond >= 15:
        score += 8
        reasons.append(f"Early curve {bond:.0f}% — most never migrate")
    else:
        score += 2
        reasons.append(f"Deep early {bond:.0f}% — lottery; rare to migrate")

    # Curve still has exit SOL
    if quote_sol >= 20:
        score += 14
        reasons.append(f"Deep curve {quote_sol:.0f} SOL")
    elif quote_sol >= 8:
        score += 10
    elif quote_sol >= 3:
        score += 5
    elif 0 < quote_sol < 0.5:
        score -= 25
        reasons.append("Curve drained — migration unlikely")

    if holders >= 80:
        score += 10
    elif holders >= 40:
        score += 7
    elif holders >= 20:
        score += 4
    elif holders < 10 and mcap >= 10_000:
        score -= 8
        reasons.append("Few holders for size — weak migration base")

    if one_way:
        score -= 20
        reasons.append("One-way buys — wash risk, not real path")
    elif two_way:
        score += 12
        reasons.append("Two-way flow (organic)")
    elif sells >= 1:
        score += 4

    # Social / structure from alpha when present
    tier = str(alpha.get("tier") or "")
    if tier in ("MEGA_MOON", "MOON_SETUP"):
        score += 8
    elif tier == "ALPHA":
        score += 4
    fp = alpha.get("megaFingerprint") or {}
    if fp.get("tier") in ("MEGA_10M", "HIGH_10M"):
        score += 6

    twitter = str(pump.get("twitter") or "")
    website = str(pump.get("website") or "")
    if twitter and "status/" not in twitter.lower():
        score += 4
    if website and not any(
        h in website.lower() for h in ("instagram.com", "tiktok.com", "x.com/status")
    ):
        score += 3

    score = int(max(0, min(100, score)))

    # Lane for UI sections
    if bond >= MIGRATION_NEAR_MIN_PCT and score >= 48:
        lane = "near_migration"
        # BUY-quality only: high score + two-way + not one-way (user losses on weak near-mig)
        recommend = (
            "ENTER"
            if score >= 72 and two_way and not one_way and holders >= 40
            else "WATCH"
        )
        summary = (
            f"Near migration ({bond:.0f}% · ${mcap:,.0f}) — score {score}. "
            f"{'High-quality path' if recommend == 'ENTER' else 'Watch only — need stronger flow/holders'}."
        )
    elif bond >= MIGRATION_CLIMBING_MIN_PCT and mcap <= 25_000:
        lane = "under_25k"
        recommend = "WATCH" if score >= 45 else "SPEC"
        summary = (
            f"Under $25k climber ({bond:.0f}%) — score {score}. "
            f"Better than pure lottery; still must hold structure to migrate."
        )
    elif mcap > 0 and mcap <= 10_000:
        lane = "early_lottery"
        recommend = "SPEC"
        summary = (
            f"Early lottery (${mcap:,.0f} · {bond:.0f}% bonded). "
            f"Most never migrate — tiny size only."
        )
    elif mcap <= 25_000:
        lane = "under_25k"
        recommend = "WATCH" if score >= 40 else "SPEC"
        summary = f"Under $25k · {bond:.0f}% bonded · score {score}"
    else:
        lane = "near_migration" if bond >= MIGRATION_CLIMBING_MIN_PCT else "under_25k"
        recommend = "WATCH"
        summary = f"${mcap:,.0f} · {bond:.0f}% bonded · score {score}"

    # Distance to graduation
    to_grad = max(0.0, GRADUATION_MCAP_USD - mcap) if mcap else GRADUATION_MCAP_USD

    return {
        "score": score,
        "lane": lane,
        "bonding_pct": round(bond, 1),
        "mcap_usd": round(mcap),
        "to_graduation_usd": round(to_grad),
        "graduation_mcap": GRADUATION_MCAP_USD,
        "summary": summary,
        "recommend": recommend,
        "reasons": reasons[:8],
        "badges": badges,
        "two_way": two_way,
        "quote_sol": round(quote_sol, 2),
        "holders": holders,
    }
