"""Predict entry / TP / exit from learned history + live features.

Uses likelihood-ratio scoring so the SCAM-heavy corpus does not dominate
rare MEGA/WINNER signals (accuracy-focused continuous learning).
"""

from __future__ import annotations

import math
from typing import Any

from config import (
    SIXK_ENTRY_SWEET_MAX,
    SIXK_ENTRY_SWEET_MIN,
    TARGET_MCAP_USD,
)
from services.learning.features import extract_features, feature_keys_for_learning
from services.learning.memory import LearningMemory


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
_MEGA = ("MEGA", "SUPER", "WINNER")
_BAD = ("DUMP", "SCAM", "RUGGED")

# High-signal features get extra weight in LR sum
_FEATURE_WEIGHT = {
    "already_crashed": 2.5,
    "fading_from_ath": 1.8,
    "one_way_wash": 2.2,
    "extreme_wash": 2.4,
    "buy_ratio_extreme": 2.0,
    "hard_avoid": 3.0,
    "adult_bait": 2.5,
    "fake_twitter": 1.8,
    "fake_website": 1.6,
    "curve_sol_drained": 2.0,
    "organic_two_way": 1.6,
    "two_way_flow": 1.5,
    "deep_curve_sol": 1.5,
    "curve_sol_ge_15": 1.4,
    "solid_distribution": 1.5,
    "mid_bags_ge_5": 1.3,
    "external_narrative": 1.4,
    "has_viral": 1.3,
    "own_twitter": 1.2,
    "real_website": 1.2,
    "mega_fingerprint:MEGA_10M": 2.0,
    "mega_fingerprint:HIGH_10M": 1.6,
    "bond_bin:bond_ge_55": 1.5,
    "bond_bin:bond_40_55": 1.4,
    "migration_lane:near_migration": 1.5,
    "runner_stage:crashed": 2.5,
    "creator_sold": 1.7,
}


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _likelihood_probs(
    keys: list[str],
    by_feat: dict[str, dict[str, dict]],
    outcome_counts: dict[str, int],
    base_rates: dict[str, float],
) -> tuple[dict[str, float], float, float, list[dict[str, Any]]]:
    """Class-balanced likelihood ratios → outcome probs + P(good) vs P(bad).

    Uses token counts per outcome (not inflated feature sums) so the 85% SCAM
    prior does not erase every positive signal.
    """
    n_good = sum(outcome_counts.get(g, 0) for g in _GOOD) or 1
    n_bad = sum(outcome_counts.get(b, 0) for b in _BAD) or 1
    n_all = sum(outcome_counts.values()) or 1

    # Two-class log-odds (good vs bad) — primary decision signal
    # Mild prior: trench base rate ~ few % good, but not 0.4% mega only
    prior_good = max(0.08, min(0.35, (n_good / n_all) * 3 + 0.05))
    log_odds = math.log(prior_good / (1 - prior_good))

    # Multi-class scores with *balanced* class prior (equal weight per class present)
    present = [oc for oc in _PRIOR if outcome_counts.get(oc, 0) > 0] or list(_PRIOR)
    log_scores = {oc: math.log(1.0 / len(present)) for oc in _PRIOR}
    for oc in present:
        log_scores[oc] = math.log(1.0 / len(present))

    matched: list[dict[str, Any]] = []
    alpha = 1.0
    sample_n = 0

    for fk in keys:
        rows = by_feat.get(fk) or {}
        if not rows:
            continue
        w = 1.0
        for prefix, wt in _FEATURE_WEIGHT.items():
            if fk == prefix or fk.startswith(prefix):
                w = max(w, wt)

        c_good = sum(int((rows.get(g) or {}).get("count") or 0) for g in _GOOD)
        c_bad = sum(int((rows.get(b) or {}).get("count") or 0) for b in _BAD)
        sample_n += c_good + c_bad

        # P(f|good), P(f|bad)
        p_f_good = (c_good + alpha) / (n_good + alpha * 2)
        p_f_bad = (c_bad + alpha) / (n_bad + alpha * 2)
        lr = p_f_good / max(p_f_bad, 1e-12)
        # Skip non-informative features (|log LR| tiny)
        log_lr = math.log(max(lr, 1e-12))
        if abs(log_lr) < 0.05 and w <= 1.2:
            continue
        log_odds += w * log_lr

        feat_info = {
            "feature": fk,
            "weight": w,
            "lr_good_vs_bad": round(lr, 3),
            "p_f_good": round(p_f_good, 4),
            "p_f_bad": round(p_f_bad, 4),
            "p_good_given_f": round(c_good / max(c_good + c_bad, 1), 3),
            "p_bad_given_f": round(c_bad / max(c_good + c_bad, 1), 3),
            "outcomes": {},
        }
        for oc in _PRIOR:
            c = int((rows.get(oc) or {}).get("count") or 0)
            n_oc = max(outcome_counts.get(oc, 0), 1)
            p_f_oc = (c + alpha) / (n_oc + alpha * 2)
            log_scores[oc] += w * math.log(max(p_f_oc, 1e-12))
            feat_info["outcomes"][oc] = {"count": c}
        matched.append(feat_info)

    # P(good) from log-odds
    # odds = p/(1-p) => p = odds/(1+odds)
    odds = math.exp(max(-20, min(20, log_odds)))
    p_good_2 = odds / (1 + odds)
    p_bad_2 = 1 - p_good_2

    # Softmax multi-class for display
    mx = max(log_scores.values())
    exps = {oc: math.exp(log_scores[oc] - mx) for oc in log_scores}
    z = sum(exps.values()) or 1.0
    probs = {oc: exps[oc] / z for oc in exps}
    # Blend multi-class good/bad mass toward two-class estimate (more calibrated)
    mass_good = sum(probs.get(g, 0) for g in _GOOD)
    mass_bad = sum(probs.get(b, 0) for b in _BAD)
    other = max(0.0, 1.0 - mass_good - mass_bad)
    # Renormalize good/bad slices to match p_good_2 / p_bad_2
    if mass_good + mass_bad > 1e-9:
        scale_g = p_good_2 / max(mass_good, 1e-9)
        scale_b = p_bad_2 / max(mass_bad, 1e-9)
        for g in _GOOD:
            probs[g] = probs.get(g, 0) * scale_g
        for b in _BAD:
            probs[b] = probs.get(b, 0) * scale_b
        # leave NEUTRAL as residual
        probs["NEUTRAL"] = other * 0.5
        z2 = sum(probs.values()) or 1.0
        probs = {k: v / z2 for k, v in probs.items()}

    return probs, float(sample_n), p_good_2, matched


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
    migration: dict | None = None,
    runner: dict | None = None,
    mcap: float = 0.0,
    price: float = 0.0,
) -> dict[str, Any]:
    """Return action, confidence, entry band, TPs, SL, exit triggers."""
    safety = safety or {}
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    avoid = avoid or safety.get("avoid") or {}
    alpha = alpha or {}
    migration = migration or {}
    runner = runner or {}

    mcap = mcap or _f(pump.get("usd_market_cap") or pair.get("marketCap"))
    price = price or _f(pair.get("priceUsd"))
    if price <= 0 and mcap > 0:
        price = mcap / 1_000_000_000

    feats = extract_features(
        safety=safety,
        pair=pair,
        pump=pump,
        social=social,
        smart_money=smart_money,
        alpha=alpha,
        avoid=avoid,
        mcap=mcap,
        migration=migration,
        runner=runner,
    )
    keys = feature_keys_for_learning(feats)
    stats = memory.get_feature_stats()
    base_rates = memory.outcome_base_rates()
    outcome_counts = memory.outcome_counts()

    by_feat: dict[str, dict[str, dict]] = {}
    for row in stats:
        by_feat.setdefault(row["feature"], {})[row["outcome"]] = row

    probs, sample_n, p_good_2class, matched_features = _likelihood_probs(
        keys, by_feat, outcome_counts, base_rates
    )
    p_good = max(sum(probs.get(g, 0) for g in _GOOD), p_good_2class * 0.85)
    p_mega = sum(probs.get(g, 0) for g in _MEGA)
    p_bad = max(sum(probs.get(b, 0) for b in _BAD), (1 - p_good_2class) * 0.85)
    # Prefer calibrated two-class for decisions
    p_good = p_good_2class
    p_bad = 1.0 - p_good_2class
    # mega share of good mass
    good_mass = sum(probs.get(g, 0) for g in _GOOD) or 1e-9
    p_mega = p_good * (sum(probs.get(g, 0) for g in _MEGA) / good_mass)

    # Multiples for TP (capped historically)
    mult_sums = {k: 0.0 for k in _PRIOR}
    mult_counts = {k: 0.0 for k in _PRIOR}
    for fk in keys:
        for oc, row in (by_feat.get(fk) or {}).items():
            if oc not in mult_sums:
                continue
            c = int(row.get("count") or 0)
            sm = float(row.get("sum_multiple") or 0)
            mult_sums[oc] += sm
            mult_counts[oc] += c

    good_mult = 4.0
    mega_n = sum(mult_counts.get(k, 0) for k in _MEGA)
    if mega_n > 0:
        good_mult = min(50.0, sum(mult_sums.get(k, 0) for k in _MEGA) / mega_n)
    elif mult_counts.get("RUNNER", 0) > 0:
        good_mult = min(20.0, mult_sums["RUNNER"] / mult_counts["RUNNER"])

    dump_mult = 0.55
    if mult_counts["DUMP"] + mult_counts["SCAM"] > 0:
        dump_mult = max(
            0.3,
            min(
                0.85,
                (mult_sums["DUMP"] + mult_sums["SCAM"])
                / (mult_counts["DUMP"] + mult_counts["SCAM"]),
            ),
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
    bond = _f(feats.get("bonding_pct") or migration.get("bonding_pct"))
    early_ok = SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX or (
        mcap >= 2000 and mcap < SIXK_ENTRY_SWEET_MIN
    )
    climb_ok = bond >= 18 or mcap >= 12_000  # mid-climb entry also valid
    hard_avoid = bool(avoid.get("hard_avoid") or avoid.get("avoid"))
    crashed = bool(feats.get("already_crashed") or runner.get("crashed"))
    # Early lottery: almost nothing clears $7k — never ENTER here
    early_lottery_band = mcap > 0 and mcap < 8_000 and bond < 14
    weak_early = (
        early_lottery_band
        and not is_mega_10m
        and (alpha.get("score") or 0) < 80
        and fp_tier not in ("MEGA_10M",)
    )

    # --- Decision policy (learned LR + structure) ---
    if hard_avoid or safety.get("is_honeypot") or safety.get("rugged") or crashed:
        action = "SKIP"
        confidence = 92
        summary = (
            avoid.get("summary")
            or runner.get("crash_reason")
            or "Hard avoid / crashed — do not enter"
        )
    elif weak_early or (early_lottery_band and p_good < 0.55):
        action = "SKIP" if p_bad >= 0.35 or feats.get("one_way_wash") else "WATCH"
        confidence = 25 if action == "WATCH" else 70
        summary = (
            f"Early lottery ${mcap:,.0f} — most never clear $7k. "
            f"{'SKIP' if action == 'SKIP' else 'WATCH only'}; size zero or dust. "
            f"P(good)≈{p_good*100:.0f}%."
        )
    elif p_bad >= 0.55 and sample_n >= 12:
        action = "SKIP"
        confidence = min(90, int(45 + p_bad * 55))
        summary = (
            f"Learned risk high (P(bad)≈{p_bad*100:.0f}% on similar features, n≈{sample_n})"
        )
    elif feats.get("one_way_wash") or feats.get("extreme_wash"):
        action = "SKIP"
        confidence = 80
        summary = "Wash / one-way tape — learned pattern of dumps & scams"
    elif early_lottery_band:
        # Absolute ban: no ENTER under $8k / low bond (user feedback)
        action = "WATCH"
        confidence = 22
        summary = (
            f"Early lottery ${mcap:,.0f} — ENTER disabled until mid-climb (~$12k+ / 18%+ bond). "
            f"Most die under $7k."
        )
    elif (
        p_mega >= 0.22
        and p_bad < 0.42
        and sample_n >= 8
        and not hard_avoid
        and climb_ok
        and mcap >= 10_000
        and (is_mega or is_mega_10m or bond >= 18)
        and (alpha.get("score") or 0) >= 58
    ):
        action = "ENTER"
        confidence = min(88, int(40 + p_mega * 90 + (10 if is_mega_10m else 0)))
        summary = (
            f"Learned ENTER — P(mega/win)≈{p_mega*100:.0f}% · P(bad)≈{p_bad*100:.0f}% "
            f"(n≈{sample_n}). {alpha.get('ceiling_label') or ceiling}."
        )
    elif (
        is_mega_10m
        and is_mega
        and climb_ok
        and mcap >= 10_000
        and not hard_avoid
        and p_bad < 0.5
    ):
        action = "ENTER"
        confidence = min(90, int(alpha.get("confidence") or 80))
        tags = ", ".join((fp.get("narrative_tags") or [])[:3]) or "multi‑$M structure"
        summary = (
            f"MEGA $10M+ ENTER — FP {fp.get('score', '?')} ({tags}). "
            f"Learned P(mega)≈{p_mega*100:.0f}%."
        )
    elif is_mega and climb_ok and mcap >= 10_000 and not hard_avoid and p_bad < 0.48:
        action = "ENTER"
        confidence = min(86, int(alpha.get("confidence") or 75))
        summary = (
            f"MEGA ENTER — {alpha.get('ceiling_label') or 'high ceiling'}. "
            f"Learned P(mega)≈{p_mega*100:.0f}% · P(bad)≈{p_bad*100:.0f}%."
        )
    elif (
        high_ceil
        and alpha.get("tier") in ("MEGA_MOON", "MOON_SETUP")
        and climb_ok
        and mcap >= 10_000
        and not hard_avoid
        and p_bad < 0.5
    ):
        action = "ENTER" if p_good >= 0.22 else "WATCH"
        confidence = min(82, int(alpha.get("confidence") or 68))
        summary = (
            f"High-ceiling {'ENTER' if action == 'ENTER' else 'WATCH'} — "
            f"{alpha.get('ceiling_label')}. P(good)≈{p_good*100:.0f}%."
        )
    elif alpha.get("tier") == "ALPHA" and (early_ok or climb_ok) and not hard_avoid:
        action = "WATCH"
        confidence = int(alpha.get("confidence") or 55)
        summary = (
            f"Solid alpha — WATCH. P(good)≈{p_good*100:.0f}% · P(bad)≈{p_bad*100:.0f}%."
        )
    else:
        action = "WATCH" if not hard_avoid else "SKIP"
        confidence = max(25, int(30 + p_good * 40 - p_bad * 30))
        summary = (
            f"Not high-conviction (P(good)≈{p_good*100:.0f}% · P(bad)≈{p_bad*100:.0f}%). "
            "Wait for structure + organic flow or near-migration."
        )

    # Blend confidence with LR separation
    separation = abs(p_good - p_bad)
    confidence = int(min(95, max(20, confidence * 0.7 + separation * 100 * 0.3)))

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
        "Price −40% from local ATH / peak",
        "One-way wash tape appears",
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
                "multiple": round(float(tp_m["mega_band_mcap"]) / max(mcap, 1), 1)
                if mcap
                else None,
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
                    "multiple": round(min(float(row.get("max_multiple") or 0), 999), 2),
                }
            )
        if len(similar) >= 8:
            break

    # Top evidence features for UI
    top_evidence = sorted(
        matched_features,
        key=lambda x: abs(math.log(max(x.get("lr_good_vs_bad", 1), 1e-6)))
        * x.get("weight", 1),
        reverse=True,
    )[:8]

    return {
        "action": action,
        "confidence": confidence,
        "summary": summary,
        "probabilities": {k: round(v, 3) for k, v in probs.items()},
        "p_good": round(p_good, 3),
        "p_mega": round(p_mega, 3),
        "p_bad": round(p_bad, 3),
        "sample_size": int(sample_n),
        "model": "likelihood_ratio_v2",
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
        "features_used": keys[:28],
        "matched_feature_count": len(matched_features),
        "top_evidence": top_evidence,
        "similar_history": similar,
        "dev_dump_hint_mcap": round(float(avg_dump), 0) if avg_dump else None,
        "base_rates": {k: round(v, 4) for k, v in base_rates.items()},
    }
