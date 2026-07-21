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

# Dump thresholds — once hit, drop from runners tab / sticky
CRASH_FROM_ATH_FRAC = 0.45  # −55% from ATH = dead as a runner
CRASH_FROM_PEAK_FRAC = 0.50  # −50% from our tracked peak
HARD_CRASH_FRAC = 0.35  # −65% from ATH = hard crash


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def extract_ath_mcap(token: dict[str, Any]) -> float:
    """Best-effort ATH mcap from token / pump / market payloads."""
    ath = _f(token.get("ath_mcap") or token.get("ath_market_cap"))
    if ath > 0:
        return ath
    pf = token.get("pumpfun") or {}
    if not pf:
        mkt = token.get("market") or {}
        pf = mkt.get("pumpfun") or {}
    ath = _f(pf.get("ath_market_cap") or pf.get("ath_mcap"))
    if ath > 0:
        return ath
    # Tracked peak from sticky stores
    return _f(token.get("_peak_mcap") or token.get("peak_mcap"))


def is_crashed_runner(
    token: dict[str, Any],
    *,
    mcap: float | None = None,
    ath: float | None = None,
    peak: float | None = None,
) -> tuple[bool, str]:
    """Return (crashed, reason) — used to purge runners tab."""
    mcap = _f(mcap if mcap is not None else token.get("mcap_usd"))
    ath = _f(ath if ath is not None else extract_ath_mcap(token))
    peak = _f(peak if peak is not None else token.get("_peak_mcap") or token.get("peak_mcap"))
    high = max(ath, peak, mcap)

    if mcap <= 0:
        return True, "No mcap — dead / unknown"

    # Price change dumps (DexScreener)
    mkt = token.get("market") or {}
    pc = mkt.get("priceChange") or token.get("priceChange") or {}
    for key in ("m5", "h1", "h6"):
        ch = _f(pc.get(key))
        if ch <= -40:
            return True, f"Crashed {ch:.0f}% ({key})"

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
        )
    ):
        return True, avoid.get("summary") or "Avoid crash flags"

    if high >= 5_000 and mcap < high * CRASH_FROM_ATH_FRAC:
        dump_pct = (1 - mcap / high) * 100
        return True, f"Crashed {dump_pct:.0f}% from peak ${high:,.0f} → ${mcap:,.0f}"

    # Bonding collapsed while sticky near-mig (was climbing, now dust)
    bond = _f(token.get("bonding_progress"))
    if bond <= 0 and mcap > 0:
        bond = min(100.0, (mcap / GRADUATION_MCAP_USD) * 100)
    was_near = (
        token.get("column") in ("almost_bonded", "recently_bonded")
        or _f(token.get("_peak_mcap")) >= 15_000
        or ath >= 15_000
    )
    if was_near and mcap < 4_000 and bond < 12:
        return True, f"Collapsed to ${mcap:,.0f} after climb — not a runner"

    # Curve drained
    quote = _f(
        token.get("quote_sol")
        or (token.get("safety") or {}).get("lp_quote_sol")
        or (token.get("safetyReport") or {}).get("lp_quote_sol")
    )
    if 0 < quote < 0.4 and mcap < 8_000 and high >= 10_000:
        return True, f"Curve drained ({quote:.2f} SOL) after peak"

    return False, ""


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
    if stage in ("mid_climb", "near_migration", "post_migration") and peak >= 10_000:
        if mcap < peak * 0.70:
            score -= 20
            reasons.append("Fading from peak — not a clean runner")

    score = int(max(0, min(100, score)))

    alert = False
    priority = 50
    # Never alert if soft-fading hard
    fading = peak >= 10_000 and mcap < peak * 0.70
    if not fading:
        if stage == "near_migration" and score >= 48:
            alert = True
            priority = 0
        elif stage == "mid_climb" and score >= 55:
            alert = True
            priority = 1
        elif stage == "early_structure" and score >= 62 and tier in (
            "MEGA_MOON",
            "MOON_SETUP",
            "ALPHA",
        ):
            alert = True
            priority = 2
        elif stage == "post_migration" and score >= 50 and mcap < 500_000:
            alert = True
            priority = 3
        elif fp_score >= 78 and mcap <= 20_000 and score >= 58:
            alert = True
            priority = 2

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
