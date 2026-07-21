"""Transaction activity (user: \"taxations\") as interest signal.

From continuous learning (entry features → outcomes, n≈4.7k):

  Total txs / 5m (buys+sells) good-rate:
    0–4     → ~3.2%  (dead interest)
    5–14    → ~6.4%
    15–29   → ~5.3%
    30–59   → ~13.4% ★ best volume band
    60–119  → ~9.6%
    120–249 → ~7.3%
    250+    → ~16% but often wash when one-way

  Buy/sell ratio (when total≥8):
    <0.8–1.5  → ~7%
    1.5–2.5   → ~12.8% ★ sweet
    2.5–4+    → ~6–7% (one-way / wash risk)

  Best combo: 30–59 txs + balanced flow (BR 1.0–2.2) → ~16.8% good rate
  Worst: 0–4 txs balanced → ~1.3%

Use active two-way flow in the sweet band as a buy-interest filter.
"""

from __future__ import annotations

from typing import Any

# Learned sweet spot (5-minute window)
TX_SWEET_TOTAL_MIN = 25
TX_SWEET_TOTAL_MAX = 80
TX_SWEET_BUYS_MIN = 12
TX_SWEET_SELLS_MIN = 4
TX_SWEET_RATIO_MIN = 1.15
TX_SWEET_RATIO_MAX = 2.6

# Acceptable wider band (still positive interest)
TX_OK_TOTAL_MIN = 15
TX_OK_TOTAL_MAX = 150
TX_DEAD_TOTAL_MAX = 7  # below = no interest

# Extreme activity often wash when unbalanced
TX_EXTREME_TOTAL = 200


def _i(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def extract_tx_counts(
    pair: dict | None = None,
    pump: dict | None = None,
) -> dict[str, Any]:
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    txns = pair.get("txns") or {}
    m5 = txns.get("m5") or {}
    h1 = txns.get("h1") or {}
    buys_m5 = _i(m5.get("buys"))
    sells_m5 = _i(m5.get("sells"))
    buys_h1 = _i(h1.get("buys"))
    sells_h1 = _i(h1.get("sells"))
    # Some pump feeds only have reply_count as weak activity proxy
    if buys_m5 == 0 and sells_m5 == 0 and pump.get("reply_count"):
        # don't invent txs from replies
        pass
    total_m5 = buys_m5 + sells_m5
    total_h1 = buys_h1 + sells_h1
    ratio_m5 = buys_m5 / max(sells_m5, 1)
    return {
        "buys_m5": buys_m5,
        "sells_m5": sells_m5,
        "total_m5": total_m5,
        "buys_h1": buys_h1,
        "sells_h1": sells_h1,
        "total_h1": total_h1,
        "buy_ratio_m5": round(ratio_m5, 2),
    }


def score_tx_activity(
    pair: dict | None = None,
    pump: dict | None = None,
    *,
    buys_m5: int | None = None,
    sells_m5: int | None = None,
) -> dict[str, Any]:
    """Score 0–100 how healthy transaction interest looks (learned sweet spot)."""
    if buys_m5 is None or sells_m5 is None:
        counts = extract_tx_counts(pair, pump)
        buys_m5 = counts["buys_m5"]
        sells_m5 = counts["sells_m5"]
        total = counts["total_m5"]
        ratio = counts["buy_ratio_m5"]
        buys_h1 = counts["buys_h1"]
        sells_h1 = counts["sells_h1"]
    else:
        buys_m5 = int(buys_m5)
        sells_m5 = int(sells_m5)
        total = buys_m5 + sells_m5
        ratio = buys_m5 / max(sells_m5, 1)
        buys_h1 = sells_h1 = 0

    score = 0
    reasons: list[str] = []
    badges: list[dict[str, str]] = []
    zone = "dead"

    # --- Total activity band (learned) ---
    if total < TX_DEAD_TOTAL_MAX:
        score += 0
        zone = "dead"
        reasons.append(f"Dead book {total} tx/5m — no interest (learned ~3% win rate)")
    elif TX_SWEET_TOTAL_MIN <= total <= TX_SWEET_TOTAL_MAX:
        score += 42
        zone = "sweet"
        reasons.append(
            f"Sweet activity {total} tx/5m ({buys_m5}B/{sells_m5}S) — learned best band ~30–60"
        )
        badges.append(
            {"id": "tx_sweet", "label": f"{total} tx/5m sweet", "type": "tx"}
        )
    elif TX_OK_TOTAL_MIN <= total < TX_SWEET_TOTAL_MIN:
        score += 22
        zone = "building"
        reasons.append(f"Building interest {total} tx/5m — watch for 25–80 band")
    elif TX_SWEET_TOTAL_MAX < total <= TX_OK_TOTAL_MAX:
        score += 28
        zone = "hot"
        reasons.append(f"Hot tape {total} tx/5m — OK if two-way, not wash")
        badges.append({"id": "tx_hot", "label": f"{total} tx hot", "type": "tx"})
    elif total > TX_EXTREME_TOTAL:
        score += 8
        zone = "extreme"
        reasons.append(f"Extreme {total} tx/5m — wash risk unless balanced")
    else:
        score += 18
        zone = "elevated"
        reasons.append(f"Elevated {total} tx/5m")

    # --- Ratio band (learned 1.5–2.5 best) ---
    if sells_m5 == 0 and buys_m5 >= 10:
        score -= 35
        reasons.append("Zero sells / all buys — wash, not real interest")
        zone = "wash"
    elif ratio > 4.0 and buys_m5 >= 15:
        score -= 22
        reasons.append(f"One-way buys {ratio:.1f}x — fake interest pattern")
        if zone != "wash":
            zone = "one_way"
    elif TX_SWEET_RATIO_MIN <= ratio <= TX_SWEET_RATIO_MAX and sells_m5 >= TX_SWEET_SELLS_MIN:
        score += 30
        reasons.append(
            f"Sweet buy/sell {ratio:.1f}x (learned ~1.5–2.5 best) · real interest"
        )
        badges.append(
            {"id": "tx_ratio", "label": f"{ratio:.1f}x flow", "type": "tx"}
        )
    elif 1.05 <= ratio < TX_SWEET_RATIO_MIN and sells_m5 >= 2:
        score += 12
        reasons.append(f"Mild buy pressure {ratio:.1f}x with sells")
    elif ratio < 0.95 and sells_m5 >= 8:
        score -= 15
        reasons.append(f"Sell-heavy {ratio:.1f}x — distribution, price pressure down")
        zone = "sell_heavy" if zone not in ("wash", "one_way") else zone
    elif ratio > TX_SWEET_RATIO_MAX and ratio <= 3.5 and sells_m5 >= 3:
        score += 10
        reasons.append(f"Buy-led {ratio:.1f}x with some sells — OK short-term")

    # Absolute min organic
    if buys_m5 >= TX_SWEET_BUYS_MIN and sells_m5 >= TX_SWEET_SELLS_MIN:
        score += 12
        reasons.append(f"Two-way depth {buys_m5}B/{sells_m5}S")
    elif buys_m5 >= 8 and sells_m5 >= 2:
        score += 5

    # H1 sustain if available
    if buys_h1 >= 40 and sells_h1 >= 15:
        h1_ratio = buys_h1 / max(sells_h1, 1)
        if 1.1 <= h1_ratio <= 3.0:
            score += 8
            reasons.append(f"Sustained 1h interest {buys_h1 + sells_h1} tx")

    score = int(max(0, min(100, score)))

    # Recommendation tilt from activity alone
    if zone == "sweet" and score >= 70:
        tilt = "UP"  # interest favors continuation if structure clean
        rec = "activity_supports_buy"
    elif zone in ("hot", "building") and score >= 55 and sells_m5 >= 3:
        tilt = "UP"
        rec = "activity_watch_buy"
    elif zone in ("wash", "one_way", "sell_heavy") or score < 30:
        tilt = "DOWN"
        rec = "activity_avoid"
    elif zone == "dead":
        tilt = "DOWN"
        rec = "activity_no_interest"
    else:
        tilt = "NEUTRAL"
        rec = "activity_neutral"

    in_sweet = (
        TX_SWEET_TOTAL_MIN <= total <= TX_SWEET_TOTAL_MAX
        and TX_SWEET_RATIO_MIN <= ratio <= TX_SWEET_RATIO_MAX
        and sells_m5 >= TX_SWEET_SELLS_MIN
        and buys_m5 >= TX_SWEET_BUYS_MIN
    )

    return {
        "score": score,
        "zone": zone,
        "tilt": tilt,  # UP / DOWN / NEUTRAL — price bias from activity
        "recommendation": rec,
        "in_sweet_spot": in_sweet,
        "buys_m5": buys_m5,
        "sells_m5": sells_m5,
        "total_m5": total,
        "buy_ratio_m5": round(ratio, 2),
        "sweet_band": {
            "total_tx_m5": f"{TX_SWEET_TOTAL_MIN}–{TX_SWEET_TOTAL_MAX}",
            "buy_sell_ratio": f"{TX_SWEET_RATIO_MIN}–{TX_SWEET_RATIO_MAX}",
            "min_buys": TX_SWEET_BUYS_MIN,
            "min_sells": TX_SWEET_SELLS_MIN,
            "learned": "30–59 tx/5m + ratio ~1.5–2.5 → highest historical good rate",
        },
        "reasons": reasons[:8],
        "badges": badges[:4],
        "summary": (
            f"Tx activity {zone} ({score}): {total} tx/5m · {buys_m5}B/{sells_m5}S · "
            f"ratio {ratio:.1f}x → tilt {tilt}"
        ),
    }
