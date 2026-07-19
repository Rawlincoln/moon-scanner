"""Predict entry / TP / exit from learned history + live features."""

from __future__ import annotations

from typing import Any

from config import (
    SIXK_ENTRY_SWEET_MAX,
    SIXK_ENTRY_SWEET_MIN,
    TARGET_MCAP_USD,
)
from services.learning.features import extract_features, feature_keys_for_learning
from services.learning.memory import LearningMemory


# Prior pseudo-counts (Bayesian smoothing)
_PRIOR = {
    "WINNER": 1.0,
    "RUNNER": 2.0,
    "NEUTRAL": 3.0,
    "DUMP": 3.0,
    "SCAM": 4.0,
    "RUGGED": 1.0,
}

_GOOD = ("WINNER", "RUNNER")
_BAD = ("DUMP", "SCAM", "RUGGED")


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def predict_trade(
    memory: LearningMemory,
    *,
    safety: dict | None = None,
    pair: dict | None = None,
    pump: dict | None = None,
    social: dict | None = None,
    smart_money: dict | None = None,
    alpha: dict | None = None,
    avoid: dict | None = None,
    mcap: float = 0.0,
    price: float = 0.0,
) -> dict[str, Any]:
    """Return action, confidence, entry band, TPs, SL, exit triggers."""
    safety = safety or {}
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    avoid = avoid or safety.get("avoid") or {}
    alpha = alpha or {}

    mcap = mcap or _f(pump.get("usd_market_cap") or pair.get("marketCap"))
    price = price or _f(pair.get("priceUsd"))
    if price <= 0 and mcap > 0:
        # rough unit price if missing
        price = mcap / 1_000_000_000  # placeholder scale; UI uses multiples mostly

    feats = extract_features(
        safety=safety,
        pair=pair,
        pump=pump,
        social=social,
        smart_money=smart_money,
        alpha=alpha,
        avoid=avoid,
        mcap=mcap,
    )
    keys = feature_keys_for_learning(feats)
    stats = memory.get_feature_stats()

    # Aggregate counts per outcome across matching features
    outcome_scores: dict[str, float] = {k: _PRIOR[k] for k in _PRIOR}
    mult_sums: dict[str, float] = {k: 0.0 for k in _PRIOR}
    mult_counts: dict[str, float] = {k: 0.0 for k in _PRIOR}
    matched_features: list[dict[str, Any]] = []

    by_feat: dict[str, dict[str, dict]] = {}
    for row in stats:
        by_feat.setdefault(row["feature"], {})[row["outcome"]] = row

    for fk in keys:
        rows = by_feat.get(fk) or {}
        total_f = sum(int(r["count"]) for r in rows.values()) or 1
        feat_info = {"feature": fk, "outcomes": {}}
        for oc, prior in _PRIOR.items():
            c = int((rows.get(oc) or {}).get("count") or 0)
            sm = float((rows.get(oc) or {}).get("sum_multiple") or 0)
            # weight contribution
            outcome_scores[oc] += c
            if c > 0:
                mult_sums[oc] += sm
                mult_counts[oc] += c
            feat_info["outcomes"][oc] = {
                "count": c,
                "rate": round(c / total_f, 3) if total_f else 0,
            }
        if rows:
            matched_features.append(feat_info)

    total = sum(outcome_scores.values()) or 1.0
    probs = {oc: outcome_scores[oc] / total for oc in outcome_scores}
    p_good = probs.get("WINNER", 0) + probs.get("RUNNER", 0)
    p_bad = sum(probs.get(b, 0) for b in _BAD)
    sample_n = sum(
        int(r["count"])
        for r in stats
        if r["feature"] in keys
    )

    # Average historical multiple for good outcomes
    good_mult = 0.0
    if mult_counts["WINNER"] + mult_counts["RUNNER"] > 0:
        good_mult = (mult_sums["WINNER"] + mult_sums["RUNNER"]) / (
            mult_counts["WINNER"] + mult_counts["RUNNER"]
        )
    else:
        good_mult = 2.2  # prior

    dump_mult = 0.55
    if mult_counts["DUMP"] + mult_counts["SCAM"] > 0:
        dump_mult = max(
            0.3,
            (mult_sums["DUMP"] + mult_sums["SCAM"])
            / (mult_counts["DUMP"] + mult_counts["SCAM"]),
        )

    # Hard skip
    hard_avoid = bool(avoid.get("hard_avoid") or avoid.get("avoid"))
    if hard_avoid or safety.get("is_honeypot") or safety.get("rugged"):
        action = "SKIP"
        confidence = 90
        summary = avoid.get("summary") or "Hard avoid — do not enter"
    elif p_bad >= 0.55 and sample_n >= 8:
        action = "SKIP"
        confidence = min(88, int(40 + p_bad * 60))
        summary = f"Learned risk high ({p_bad*100:.0f}% bad outcomes on similar features)"
    elif p_good >= 0.45 and sample_n >= 5 and not hard_avoid:
        if SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX:
            action = "ENTER"
            confidence = min(88, int(45 + p_good * 50 + (alpha.get("score") or 0) * 0.15))
            summary = (
                f"Learned ENTER — similar setups win/run ~{p_good*100:.0f}% "
                f"(n≈{sample_n}). Sweet $6k zone."
            )
        elif mcap < SIXK_ENTRY_SWEET_MIN and mcap >= 1500:
            action = "ENTER"
            confidence = min(82, int(40 + p_good * 45))
            summary = (
                f"Early ENTER candidate — similar history +{p_good*100:.0f}% good "
                f"before ${TARGET_MCAP_USD//1000}k"
            )
        else:
            action = "WATCH"
            confidence = min(70, int(35 + p_good * 40))
            summary = "Similar history is decent but mcap past ideal entry band"
    elif (alpha.get("is_alpha") or (alpha.get("score") or 0) >= 60) and not hard_avoid:
        action = "ENTER" if mcap <= SIXK_ENTRY_SWEET_MAX else "WATCH"
        confidence = int(alpha.get("confidence") or 55)
        summary = alpha.get("summary") or "Rule-based alpha setup"
    else:
        action = "WATCH" if not hard_avoid else "SKIP"
        confidence = 40
        summary = "Insufficient similar history — wait for clearer setup"

    # TP / SL from learned multiples + live structure
    # Scale TP by historical good_mult
    tp1_m = min(2.0, max(1.35, 1.0 + (good_mult - 1) * 0.35))
    tp2_m = min(3.5, max(1.8, 1.0 + (good_mult - 1) * 0.7))
    tp3_m = min(6.0, max(2.5, good_mult * 1.1 if good_mult > 1 else 3.0))
    sl_m = min(0.85, max(0.55, dump_mult if dump_mult < 0.9 else 0.72))

    def _px(mult: float) -> float | None:
        if price <= 0:
            return None
        return round(price * mult, 12)

    def _mc(mult: float) -> float | None:
        if mcap <= 0:
            return None
        return round(mcap * mult, 0)

    # Historical creator dump level
    hist = memory.get_outcomes_summary().get("by_outcome") or {}
    avg_dump = None
    if "DUMP" in hist and hist["DUMP"].get("avg_dump_mcap"):
        avg_dump = hist["DUMP"]["avg_dump_mcap"]
    if "SCAM" in hist and hist["SCAM"].get("avg_dump_mcap"):
        avg_dump = avg_dump or hist["SCAM"]["avg_dump_mcap"]

    exit_triggers = [
        "Creator wallet sells / balance → 0",
        "Sells > buys for 2+ minutes",
        f"MCap drops below SL (~{sl_m*100:.0f}% of entry)",
        "Curve SOL < 0.5 (exit liquidity drained)",
        "Price −40% from local ATH",
    ]
    if avg_dump:
        exit_triggers.insert(
            0,
            f"Historical dev-dump cluster near mcap ~${float(avg_dump):,.0f}",
        )

    entry_band = {
        "ideal_min_mcap": SIXK_ENTRY_SWEET_MIN,
        "ideal_max_mcap": SIXK_ENTRY_SWEET_MAX,
        "target_mcap": TARGET_MCAP_USD,
        "current_mcap": round(mcap, 0) if mcap else None,
        "in_sweet_zone": SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX,
        "price_now": price if price > 0 else None,
        "ideal_entry_price": _px(0.97),
        "aggressive_entry_price": _px(1.02),
    }

    take_profits = [
        {
            "label": "TP1",
            "multiple": round(tp1_m, 2),
            "price": _px(tp1_m),
            "mcap": _mc(tp1_m),
            "action": "Sell 30–40%",
        },
        {
            "label": "TP2",
            "multiple": round(tp2_m, 2),
            "price": _px(tp2_m),
            "mcap": _mc(tp2_m),
            "action": "Sell 30–40%",
        },
        {
            "label": "TP3",
            "multiple": round(tp3_m, 2),
            "price": _px(tp3_m),
            "mcap": _mc(tp3_m),
            "action": "Trail remainder",
        },
    ]
    stop_loss = {
        "multiple": round(sl_m, 2),
        "price": _px(sl_m),
        "mcap": _mc(sl_m),
        "action": "Full exit — preserve capital",
    }

    # Similar finalized tokens
    similar = []
    for row in memory.recent_finalized(40):
        if row.get("outcome") in _GOOD or row.get("outcome") in _BAD:
            similar.append(
                {
                    "mint": row["mint"],
                    "name": row.get("name"),
                    "symbol": row.get("symbol"),
                    "outcome": row.get("outcome"),
                    "entry_mcap": row.get("first_mcap"),
                    "ath_mcap": row.get("ath_mcap"),
                    "dev_dump_mcap": row.get("creator_dump_mcap"),
                    "multiple": round(float(row.get("max_multiple") or 0), 2),
                }
            )
        if len(similar) >= 8:
            break

    return {
        "action": action,
        "confidence": confidence,
        "summary": summary,
        "probabilities": {k: round(v, 3) for k, v in probs.items()},
        "p_good": round(p_good, 3),
        "p_bad": round(p_bad, 3),
        "sample_size": sample_n,
        "learned_avg_winner_multiple": round(good_mult, 2),
        "entry": entry_band,
        "take_profit": take_profits,
        "stop_loss": stop_loss,
        "exit_triggers": exit_triggers,
        "features_used": keys[:24],
        "matched_feature_count": len(matched_features),
        "similar_history": similar,
        "dev_dump_hint_mcap": round(float(avg_dump), 0) if avg_dump else None,
    }
