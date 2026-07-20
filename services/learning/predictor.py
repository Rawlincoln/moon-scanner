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
    "MEGA": 0.5,
    "SUPER": 0.8,
    "WINNER": 1.0,
    "RUNNER": 2.0,
    "NEUTRAL": 3.0,
    "DUMP": 3.0,
    "SCAM": 4.0,
    "RUGGED": 1.0,
}

_GOOD = ("MEGA", "SUPER", "WINNER", "RUNNER")
_MEGA = ("MEGA", "SUPER", "WINNER")  # real moons, not weak 1.5x runners
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
    p_good = sum(probs.get(g, 0) for g in _GOOD)
    p_mega = sum(probs.get(g, 0) for g in _MEGA)
    p_bad = sum(probs.get(b, 0) for b in _BAD)
    sample_n = sum(
        int(r["count"])
        for r in stats
        if r["feature"] in keys
    )

    # Average historical multiple for MEGA/WINNER (not weak runners)
    good_mult = 0.0
    mega_n = sum(mult_counts.get(k, 0) for k in _MEGA)
    if mega_n > 0:
        good_mult = sum(mult_sums.get(k, 0) for k in _MEGA) / mega_n
    elif mult_counts.get("RUNNER", 0) > 0:
        good_mult = mult_sums["RUNNER"] / mult_counts["RUNNER"]
    else:
        good_mult = 4.0  # prior: aim higher than 2x

    dump_mult = 0.55
    if mult_counts["DUMP"] + mult_counts["SCAM"] > 0:
        dump_mult = max(
            0.3,
            (mult_sums["DUMP"] + mult_sums["SCAM"])
            / (mult_counts["DUMP"] + mult_counts["SCAM"]),
        )

    ceiling = alpha.get("ceiling") or "low"
    is_mega = bool(alpha.get("is_mega") or alpha.get("tier") == "MEGA_MOON")
    fp = alpha.get("megaFingerprint") or {}
    fp_tier = str(fp.get("tier") or "")
    is_mega_10m = bool(
        alpha.get("is_mega_10m")
        or fp_tier in ("MEGA_10M", "HIGH_10M")
        or ceiling in ("10M_to_100M", "1M_to_10M")
    )
    high_ceil = ceiling in (
        "10M_to_100M",
        "1M_to_10M",
        "100k_to_1M",
        "50k_to_250k",
    ) or is_mega
    early_ok = SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX or (
        mcap >= 2000 and mcap < SIXK_ENTRY_SWEET_MIN
    )

    # Hard skip
    hard_avoid = bool(avoid.get("hard_avoid") or avoid.get("avoid"))
    if hard_avoid or safety.get("is_honeypot") or safety.get("rugged"):
        action = "SKIP"
        confidence = 90
        summary = avoid.get("summary") or "Hard avoid — do not enter"
    elif p_bad >= 0.50 and sample_n >= 8:
        action = "SKIP"
        confidence = min(88, int(40 + p_bad * 60))
        summary = f"Learned risk high ({p_bad*100:.0f}% bad outcomes on similar features)"
    elif is_mega_10m and is_mega and early_ok and not hard_avoid:
        action = "ENTER"
        confidence = min(92, int(alpha.get("confidence") or 85))
        tags = ", ".join((fp.get("narrative_tags") or [])[:3]) or "multi‑$M structure"
        summary = (
            f"MEGA $10M+ ENTER — fingerprint {fp.get('score', '?')} "
            f"({tags}). Ceiling {alpha.get('ceiling_label') or '$10M–$100M'}. "
            f"Learned mega/win rate ~{p_mega*100:.0f}% (n≈{sample_n})."
        )
    elif is_mega and early_ok and not hard_avoid:
        action = "ENTER"
        confidence = min(90, int(alpha.get("confidence") or 80))
        summary = (
            f"MEGA ENTER — stacked for 100k–1M path. "
            f"{alpha.get('ceiling_label') or 'high ceiling'}. "
            f"Learned mega/win rate ~{p_mega*100:.0f}% on similar (n≈{sample_n})."
        )
    elif (
        high_ceil
        and alpha.get("tier") in ("MEGA_MOON", "MOON_SETUP")
        and early_ok
        and not hard_avoid
    ):
        action = "ENTER"
        confidence = min(85, int(alpha.get("confidence") or 70))
        summary = (
            f"High-ceiling ENTER — {alpha.get('ceiling_label')}. "
            f"Only size these; small tops under $20k are filtered harder now."
        )
    elif (
        p_mega >= 0.25
        and sample_n >= 10
        and early_ok
        and not hard_avoid
        and (alpha.get("score") or 0) >= 65
    ):
        action = "ENTER"
        confidence = min(80, int(40 + p_mega * 80))
        summary = (
            f"Learned mega-leaning setup (~{p_mega*100:.0f}% mega/win features). "
            f"Ceiling aim 50k–1M if structure holds."
        )
    elif alpha.get("tier") == "ALPHA" and early_ok and not hard_avoid:
        action = "WATCH"
        confidence = int(alpha.get("confidence") or 55)
        summary = (
            f"Solid alpha but not full mega stack — WATCH for deeper SOL/holders. "
            f"{alpha.get('ceiling_label') or ''}"
        )
    else:
        action = "WATCH" if not hard_avoid else "SKIP"
        confidence = 35
        summary = (
            "Not a mega candidate — most coins die under $20k. "
            "Wait for deep SOL + distributed holders + organic two-way flow."
        )

    # TP / SL — mega path uses absolute mcap targets when available
    tp_m = alpha.get("tp_mcap_targets") or {}
    if is_mega_10m or ceiling in ("10M_to_100M", "1M_to_10M"):
        tp1_m = max(2.5, min(5.0, (tp_m.get("tp1_mcap") or 15000) / max(mcap, 1)))
        tp2_m = max(8.0, min(40.0, (tp_m.get("tp2_mcap") or 100000) / max(mcap, 1)))
        tp3_m = max(20.0, min(200.0, (tp_m.get("tp3_mcap") or 1_000_000) / max(mcap, 1)))
    elif is_mega or ceiling == "100k_to_1M":
        tp1_m = max(2.5, min(4.0, (tp_m.get("tp1_mcap") or 15000) / max(mcap, 1)))
        tp2_m = max(8.0, min(20.0, (tp_m.get("tp2_mcap") or 100000) / max(mcap, 1)))
        tp3_m = max(15.0, min(50.0, (tp_m.get("tp3_mcap") or 350000) / max(mcap, 1)))
    elif ceiling == "50k_to_250k":
        tp1_m = max(2.0, min(3.5, (tp_m.get("tp1_mcap") or 15000) / max(mcap, 1)))
        tp2_m = max(5.0, min(12.0, (tp_m.get("tp2_mcap") or 50000) / max(mcap, 1)))
        tp3_m = max(10.0, min(25.0, (tp_m.get("tp3_mcap") or 150000) / max(mcap, 1)))
    else:
        # Weak setups: still show TPs but action should rarely be ENTER
        tp1_m = min(2.0, max(1.35, 1.0 + (good_mult - 1) * 0.25))
        tp2_m = min(3.5, max(1.8, 1.0 + (good_mult - 1) * 0.5))
        tp3_m = min(6.0, max(2.5, good_mult * 0.9 if good_mult > 1 else 3.0))
    sl_m = min(0.80, max(0.55, dump_mult if dump_mult < 0.9 else 0.70))

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

    def _tp_mcap(mult: float, absolute: float | None) -> float | None:
        if absolute and absolute > 0:
            return round(float(absolute), 0)
        return _mc(mult)

    sell_pct = tp_m.get("sell_pct") or {}
    take_profits = [
        {
            "label": "TP1",
            "multiple": round(tp1_m, 2),
            "price": _px(tp1_m),
            "mcap": _tp_mcap(tp1_m, tp_m.get("tp1_mcap")),
            "action": f"Sell {sell_pct.get('tp1', 30)}%" if is_mega_10m else "Sell 25–35%",
        },
        {
            "label": "TP2",
            "multiple": round(tp2_m, 2),
            "price": _px(tp2_m),
            "mcap": _tp_mcap(tp2_m, tp_m.get("tp2_mcap")),
            "action": f"Sell {sell_pct.get('tp2', 25)}%" if is_mega_10m else "Sell 30–40%",
        },
        {
            "label": "TP3 / Moon",
            "multiple": round(tp3_m, 2),
            "price": _px(tp3_m),
            "mcap": _tp_mcap(tp3_m, tp_m.get("tp3_mcap") or tp_m.get("moon_mcap")),
            "action": (
                f"Sell {sell_pct.get('tp3', 20)}% · trail core toward $10M–$100M"
                if is_mega_10m
                else "Trail remainder toward mega"
            ),
        },
    ]
    if is_mega_10m and tp_m.get("mega_band_mcap"):
        take_profits.append(
            {
                "label": "Mega band",
                "multiple": round(
                    float(tp_m["mega_band_mcap"]) / max(mcap, 1), 1
                ) if mcap else None,
                "price": None,
                "mcap": round(float(tp_m["mega_band_mcap"])),
                "action": f"Hold core ~{sell_pct.get('core', 25)}% only if narrative+volume expand",
            }
        )
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
        "p_mega": round(p_mega, 3),
        "p_bad": round(p_bad, 3),
        "sample_size": sample_n,
        "learned_avg_winner_multiple": round(good_mult, 2),
        "ceiling": ceiling,
        "ceiling_label": alpha.get("ceiling_label") or ceiling,
        "is_mega_10m": is_mega_10m,
        "fingerprint_score": fp.get("score"),
        "fingerprint_tier": fp_tier or None,
        "narrative_tags": fp.get("narrative_tags") or [],
        "entry": entry_band,
        "take_profit": take_profits,
        "stop_loss": stop_loss,
        "exit_triggers": exit_triggers,
        "features_used": keys[:24],
        "matched_feature_count": len(matched_features),
        "similar_history": similar,
        "dev_dump_hint_mcap": round(float(avg_dump), 0) if avg_dump else None,
    }
