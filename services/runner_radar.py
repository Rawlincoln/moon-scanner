"""Runner radar — multi-stage detection so $10M–$100M paths aren't missed.

Stages (all matter; runners can be caught at any):
  1. early_structure  — $4k–$15k with real social + two-way + mid bags
  2. mid_climb        — $12k–$35k, bonding climbing, organic flow
  3. near_migration   — 40%+ bonded / ~$28k+ toward graduation
  4. post_migration   — just graduated with volume (second chance)

Crashed / dumped tokens are NEVER alerts (e.g. ATH $20k → $2k).
"""

from __future__ import annotations

import time
from typing import Any

from config import (
    GRADUATION_MCAP_USD,
    MIGRATION_NEAR_MIN_PCT,
    SIXK_ENTRY_SWEET_MAX,
    SIXK_ENTRY_SWEET_MIN,
)
from services.tx_activity import score_tx_activity

# Dump thresholds — hide early; user still saw dumped charts
CRASH_FROM_ATH_FRAC = 0.80  # −20% from ATH = hide (mcap < 80% of peak)
CRASH_FROM_PEAK_FRAC = 0.80
SOFT_FADE_FRAC = 0.88  # −12% from peak = not a clean buy
HARD_CRASH_FRAC = 0.60  # −40% hard


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def extract_mcap_usd(token: dict[str, Any]) -> float:
    """Best-effort live mcap from token / pump / market payloads."""
    m = _f(token.get("mcap_usd") or token.get("market_cap_usd") or token.get("marketCap"))
    if m > 0:
        return m
    pf = token.get("pumpfun") or {}
    mkt = token.get("market") or {}
    if not pf:
        pf = mkt.get("pumpfun") or {}
    m = _f(pf.get("usd_market_cap") or pf.get("market_cap"))
    if m > 0:
        return m
    return _f(mkt.get("marketCap") or mkt.get("fdv") or token.get("_mcap"))


def extract_ath_mcap(token: dict[str, Any]) -> float:
    """Best-effort multi-source ATH mcap (pump + peaks + market high-water).

    Uses max across sources so lagged pump ATH does not mark a live high as fade.
    """
    cands: list[float] = []
    for v in (
        token.get("ath_mcap"),
        token.get("ath_market_cap"),
        token.get("_peak_mcap"),
        token.get("peak_mcap"),
    ):
        f = _f(v)
        if f > 0:
            cands.append(f)
    pf = token.get("pumpfun") or {}
    mkt = token.get("market") or {}
    if not pf:
        pf = mkt.get("pumpfun") or {}
    for src in (pf, mkt):
        if not isinstance(src, dict):
            continue
        for k in ("ath_market_cap", "ath_mcap"):
            f = _f(src.get(k))
            if f > 0:
                cands.append(f)
    # Live mcap is a floor for ATH (price at high = ATH)
    live = extract_mcap_usd(token)
    if live > 0:
        cands.append(live)
    # Dex marketCap/fdv as weak high-water when pump ATH missing
    for k in ("marketCap", "fdv"):
        f = _f(mkt.get(k)) if isinstance(mkt, dict) else 0.0
        if f > 0:
            cands.append(f)
    return max(cands) if cands else 0.0


def is_crashed_runner(
    token: dict[str, Any],
    *,
    mcap: float | None = None,
    ath: float | None = None,
    peak: float | None = None,
) -> tuple[bool, str]:
    """Return (crashed, reason) — purge from runners / near-mig / lottery."""
    mcap = _f(mcap if mcap is not None else extract_mcap_usd(token))
    ath = _f(ath if ath is not None else extract_ath_mcap(token))
    peak = _f(
        peak
        if peak is not None
        else token.get("_peak_mcap") or token.get("peak_mcap")
    )

    # Unknown mcap alone is NOT a dump (preview / loading)
    if mcap <= 0:
        if max(ath, peak) >= 4_000:
            return True, "Lost mcap after peak — dead"
        return False, ""

    # Prefer true peak (ATH), not max(ath, mcap) which hides dumps when mcap is ATH
    high = max(ath, peak)
    if high <= 0:
        high = mcap

    # Price change dumps (DexScreener / nested market)
    mkt = token.get("market") or {}
    pc = mkt.get("priceChange") or token.get("priceChange") or {}
    for key, thr in (("m5", -18), ("h1", -22), ("h6", -28), ("h24", -40)):
        ch = _f(pc.get(key))
        if ch <= thr:
            return True, f"Dumped {ch:.0f}% ({key})"

    avoid = (
        (token.get("safetyReport") or {}).get("avoid")
        or (token.get("safety") or {}).get("avoid")
        or {}
    )
    flags = set(avoid.get("flags") or [])
    if any(
        f in flags
        for f in (
            "flash_pump_dump",
            "post_ath_crash",
            "drained_curve",
            "creator_dumped",
            "blocklist",
            "sell_pressure",
        )
    ):
        return True, avoid.get("summary") or "Avoid crash flags"

    if token.get("skipped") and str(token.get("skipReason") or token.get("skip_reason") or "").lower().find("dump") >= 0:
        return True, str(token.get("skipReason") or "dumped")

    # −22% from ATH/peak (user: never show dumps)
    if high >= 2_000 and mcap < high * CRASH_FROM_ATH_FRAC:
        dump_pct = (1 - mcap / high) * 100
        return True, f"Dumped {dump_pct:.0f}% from peak ${high:,.0f} → ${mcap:,.0f}"

    # Hard crash −40%
    if high >= 1_800 and mcap < high * HARD_CRASH_FRAC:
        return True, f"Hard crash from ${high:,.0f} → ${mcap:,.0f}"

    bond = _f(token.get("bonding_progress"))
    if bond <= 0 and mcap > 0:
        bond = min(100.0, (mcap / GRADUATION_MCAP_USD) * 100)
    was_near = (
        token.get("column") in ("almost_bonded", "recently_bonded")
        or _f(token.get("_peak_mcap")) >= 12_000
        or ath >= 12_000
        or high >= 18_000
    )
    if was_near and mcap < 7_000:
        return True, f"Collapsed to ${mcap:,.0f} after climb — remove"
    if was_near and bond < 18 and high > 0 and mcap < high * 0.55:
        return True, f"Bonding collapsed {bond:.0f}% after peak"

    # Early lottery that already failed
    if high >= 5_000 and mcap < 3_200 and high < 18_000:
        return True, f"Early fail ${high:,.0f}→${mcap:,.0f} — not a runner"

    # Big ATH → small now even if peak field missing
    if ath >= 12_000 and mcap < ath * 0.45:
        return True, f"ATH collapse ${ath:,.0f}→${mcap:,.0f}"

    quote = _f(
        token.get("quote_sol")
        or (token.get("safety") or {}).get("lp_quote_sol")
        or (token.get("safetyReport") or {}).get("lp_quote_sol")
    )
    if 0 < quote < 0.5 and high >= 8_000:
        return True, f"Curve drained ({quote:.2f} SOL) after peak"

    return False, ""


def is_fading_not_runner(
    token: dict[str, Any],
    *,
    mcap: float | None = None,
    peak: float | None = None,
) -> bool:
    """−30% from peak: keep off runner alerts (may still show under dump filter)."""
    mcap = _f(mcap if mcap is not None else token.get("mcap_usd"))
    peak = _f(
        peak
        if peak is not None
        else max(
            extract_ath_mcap(token),
            _f(token.get("_peak_mcap") or token.get("peak_mcap")),
            mcap,
        )
    )
    if peak >= 6_000 and mcap > 0 and mcap < peak * SOFT_FADE_FRAC:
        return True
    return False


def score_runner_candidate(token: dict[str, Any]) -> dict[str, Any]:
    """Score a trenches/analysis token for multi-$M runner potential (0–100)."""
    mcap = _f(token.get("mcap_usd"))
    bond = _f(token.get("bonding_progress"))
    if bond <= 0 and mcap > 0:
        bond = min(100.0, (mcap / GRADUATION_MCAP_USD) * 100)
    ath = extract_ath_mcap(token)
    peak = max(ath, _f(token.get("_peak_mcap")), mcap)

    crashed, crash_reason = is_crashed_runner(
        token, mcap=mcap, ath=ath, peak=peak
    )
    if crashed:
        return {
            "score": 0,
            "stage": "crashed",
            "alert": False,
            "priority": 99,
            "summary": crash_reason or "Crashed — remove from runners",
            "reasons": [crash_reason],
            "crashed": True,
            "crash_reason": crash_reason,
            "mcap_usd": round(mcap),
            "ath_mcap": round(ath) if ath else None,
            "peak_mcap": round(peak) if peak else None,
            "bonding_pct": round(bond, 1),
        }
    # Early lottery dust — never alert as runner (user: nothing past $7k)
    if mcap > 0 and mcap < 8_000 and peak < 12_000 and bond < 14:
        return {
            "score": max(0, min(40, int(bond * 2))),
            "stage": "early_lottery",
            "alert": False,
            "priority": 90,
            "summary": f"Early lottery ${mcap:,.0f} — most die under $7k; not a runner alert",
            "reasons": ["Sub-$8k lottery — no runner alert"],
            "crashed": False,
            "mcap_usd": round(mcap),
            "ath_mcap": round(ath) if ath else None,
            "peak_mcap": round(peak) if peak else None,
            "bonding_pct": round(bond, 1),
        }
    if is_fading_not_runner(token, mcap=mcap, peak=peak):
        # Allow scoring but never alert
        pass

    avoid = (
        (token.get("safetyReport") or {}).get("avoid")
        or (token.get("safety") or {}).get("avoid")
        or {}
    )
    if avoid.get("hard_avoid") or avoid.get("avoid"):
        return {
            "score": 0,
            "stage": "skip",
            "alert": False,
            "priority": 99,
            "summary": "Avoid — not a runner",
            "reasons": [avoid.get("summary") or "avoid filter"],
            "crashed": False,
        }

    alpha = token.get("alphaSetup") or {}
    mig = token.get("migrationPath") or {}
    plan = token.get("tradePlan") or {}
    fp = alpha.get("megaFingerprint") or {}
    social = token.get("socialSignals") or {}
    sm = token.get("smartMoney") or {}

    mig_score = int(mig.get("score") or 0)
    fp_score = int(fp.get("score") or 0)
    tier = str(alpha.get("tier") or "")
    age = _f(token.get("age_minutes"), 999)

    score = 0
    reasons: list[str] = []

    # Soft penalty if already off ATH but not full crash (still climbing risk)
    if ath >= 8_000 and mcap > 0 and mcap < ath * 0.75:
        score -= 12
        reasons.append(
            f"−{(1 - mcap / ath) * 100:.0f}% from ATH ${ath:,.0f} — late / weak"
        )

    # Stage detection
    if bond >= 99 or token.get("column") == "recently_bonded":
        stage = "post_migration"
        score += 35
        reasons.append("Just migrated / graduated")
    elif bond >= MIGRATION_NEAR_MIN_PCT or mig.get("lane") == "near_migration":
        stage = "near_migration"
        score += 42
        reasons.append(f"Near migration {bond:.0f}%")
    elif mcap >= 12_000 or bond >= 18:
        stage = "mid_climb"
        score += 28
        reasons.append(f"Mid-climb ${mcap:,.0f} · {bond:.0f}%")
    elif SIXK_ENTRY_SWEET_MIN <= mcap <= 15_000:
        stage = "early_structure"
        score += 18
        reasons.append(f"Early structure band ${mcap:,.0f}")
    else:
        stage = "too_early"
        score += 5

    # Transaction interest (learned sweet spot ~30–60 tx/5m, ratio 1.5–2.5)
    pair_like = {
        "txns": token.get("txns")
        or {
            "m5": {
                "buys": (token.get("metrics") or {}).get("buys_m5")
                or (alpha.get("metrics") or {}).get("buys_m5"),
                "sells": (token.get("metrics") or {}).get("sells_m5")
                or (alpha.get("metrics") or {}).get("sells_m5"),
            }
        },
        "priceChange": token.get("priceChange") or {},
    }
    # Prefer alpha txActivity if already computed
    tx_act = alpha.get("txActivity") or score_tx_activity(pair=pair_like)
    if tx_act.get("in_sweet_spot"):
        score += 16
        reasons.append("Tx sweet spot — active two-way interest")
    elif tx_act.get("tilt") == "UP":
        score += 10
        reasons.append(tx_act.get("summary") or "Healthy tx interest")
    elif tx_act.get("tilt") == "DOWN" or tx_act.get("zone") in ("dead", "wash", "one_way"):
        score -= 20
        reasons.append(tx_act.get("summary") or "Dead/wash txs — low interest")

    # Structure / mega fingerprint
    if tier in ("MEGA_MOON", "MOON_SETUP"):
        score += 18
        reasons.append(f"Alpha {tier}")
    elif tier == "ALPHA":
        score += 10
    if fp_score >= 70:
        score += 14
        reasons.append(f"$10M fingerprint {fp_score}")
    elif fp_score >= 55:
        score += 8
    if mig_score >= 55:
        score += 12
    elif mig_score >= 40:
        score += 6

    tags = fp.get("narrative_tags") or []
    if tags:
        score += 8
        reasons.append("Narrative: " + ", ".join(tags[:2]))
    if social.get("highlight"):
        score += 6
        reasons.append("Social highlight")
    if sm.get("anti_rug_signal"):
        score += 8
        reasons.append(f"Smart money: {sm.get('signal')}")

    if 4 <= age <= 120:
        score += 6
    elif age < 2:
        score -= 8
        reasons.append("Too fresh — flash risk")

    if plan.get("action") == "ENTER":
        score += 6
    elif plan.get("action") == "SKIP":
        score -= 15

    tier_s = token.get("safetyTier") or ""
    if tier_s in ("UNSAFE", "AVOID"):
        score -= 25
    elif tier_s == "SAFE_ENTRY":
        score += 5

    # Require still above a floor of its peak for mid/near stages
    if stage in ("mid_climb", "near_migration", "post_migration") and peak >= 8_000:
        if mcap < peak * SOFT_FADE_FRAC:
            score -= 25
            reasons.append("Fading from peak — not a clean runner")

    # Early structure / lottery: very hard to alert (most die under $7k)
    if stage in ("early_structure", "too_early") and mcap < 10_000:
        score = min(score, 58)
        reasons.append("Early band — historically rarely clears $7k")

    score = int(max(0, min(100, score)))

    alert = False
    priority = 50
    fading = is_fading_not_runner(token, mcap=mcap, peak=peak)
    tx_supports = tx_act.get("tilt") == "UP" or tx_act.get("in_sweet_spot")
    if not fading and stage != "early_lottery" and tx_supports:
        if stage == "near_migration" and score >= 52 and mcap >= peak * 0.75:
            alert = True
            priority = 0
        elif stage == "mid_climb" and score >= 60 and mcap >= 12_000:
            alert = True
            priority = 1
        elif (
            stage == "early_structure"
            and score >= 78
            and tier in ("MEGA_MOON", "MOON_SETUP")
            and fp_score >= 72
            and mcap >= 5_000
            and tx_act.get("in_sweet_spot")
        ):
            # Only extreme structure early with live interest
            alert = True
            priority = 2
        elif stage == "post_migration" and score >= 55 and mcap < 500_000 and mcap >= 40_000:
            alert = True
            priority = 3
        elif (
            fp_score >= 80
            and mcap >= 10_000
            and mcap <= 40_000
            and score >= 65
            and not fading
        ):
            alert = True
            priority = 1

    summary = (
        f"RUNNER {stage.replace('_', ' ').upper()} · score {score} · "
        f"${mcap:,.0f} · {bond:.0f}% bonded"
    )
    if tags:
        summary += f" · {tags[0]}"

    return {
        "score": score,
        "stage": stage,
        "alert": alert,
        "priority": priority,
        "summary": summary,
        "reasons": reasons[:8],
        "crashed": False,
        "mcap_usd": round(mcap),
        "ath_mcap": round(ath) if ath else None,
        "peak_mcap": round(peak) if peak else None,
        "bonding_pct": round(bond, 1),
        "fingerprint_score": fp_score,
        "migration_score": mig_score,
        "alpha_tier": tier,
        "narrative_tags": tags[:4],
        "txActivity": {
            "score": tx_act.get("score"),
            "zone": tx_act.get("zone"),
            "tilt": tx_act.get("tilt"),
            "total_m5": tx_act.get("total_m5"),
            "in_sweet_spot": tx_act.get("in_sweet_spot"),
            "buy_ratio_m5": tx_act.get("buy_ratio_m5"),
        },
    }


def build_runner_alerts(
    tokens: list[dict[str, Any]],
    *,
    prev_mints: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank and package runner alerts from a token list — never include crashes."""
    prev = prev_mints or set()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in tokens:
        mint = t.get("tokenAddress") or ""
        if not mint or mint in seen:
            continue
        seen.add(mint)
        rr = score_runner_candidate(t)
        t["runnerRadar"] = rr
        if rr.get("crashed") or rr.get("stage") == "crashed":
            continue
        if not rr.get("alert"):
            continue
        item = {
            "tokenAddress": mint,
            "name": t.get("name"),
            "symbol": t.get("symbol"),
            "icon": t.get("icon"),
            "mcap_usd": t.get("mcap_usd"),
            "ath_mcap": rr.get("ath_mcap") or extract_ath_mcap(t),
            "bonding_progress": t.get("bonding_progress") or rr.get("bonding_pct"),
            "column": t.get("column"),
            "padre": t.get("padre"),
            "pump_url": t.get("pump_url"),
            "safetyTier": t.get("safetyTier"),
            "alphaSetup": t.get("alphaSetup"),
            "migrationPath": t.get("migrationPath"),
            "tradePlan": t.get("tradePlan"),
            "runnerRadar": rr,
            "is_new_alert": mint not in prev,
            "alerted_at": time.time(),
            "_peak_mcap": max(
                _f(t.get("_peak_mcap")),
                _f(rr.get("peak_mcap")),
                _f(t.get("mcap_usd")),
                extract_ath_mcap(t),
            ),
        }
        out.append(item)
    out.sort(
        key=lambda x: (
            x["runnerRadar"].get("priority", 99),
            -(x["runnerRadar"].get("score") or 0),
            -(float(x.get("bonding_progress") or 0)),
        )
    )
    return out[:20]
