"""Runner radar — multi-stage detection so $10M–$100M paths aren't missed.

Stages (all matter; runners can be caught at any):
  1. early_structure  — $4k–$15k with real social + two-way + mid bags
  2. mid_climb        — $12k–$35k, bonding climbing, organic flow
  3. near_migration   — 40%+ bonded / ~$28k+ toward graduation
  4. post_migration   — just graduated with volume (second chance)

Alerts are sticky until the token dies, dumps hard, or is avoided.
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


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def score_runner_candidate(token: dict[str, Any]) -> dict[str, Any]:
    """Score a trenches/analysis token for multi-$M runner potential (0–100)."""
    mcap = _f(token.get("mcap_usd"))
    bond = _f(token.get("bonding_progress"))
    if bond <= 0 and mcap > 0:
        bond = min(100.0, (mcap / GRADUATION_MCAP_USD) * 100)

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
        }

    alpha = token.get("alphaSetup") or {}
    mig = token.get("migrationPath") or {}
    plan = token.get("tradePlan") or {}
    fp = alpha.get("megaFingerprint") or {}
    social = token.get("socialSignals") or {}
    sm = token.get("smartMoney") or {}

    alpha_score = int(alpha.get("score") or 0)
    mig_score = int(mig.get("score") or 0)
    fp_score = int(fp.get("score") or 0)
    tier = str(alpha.get("tier") or "")
    age = _f(token.get("age_minutes"), 999)

    score = 0
    reasons: list[str] = []

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

    # Narrative / social (runners almost always have external attention)
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

    # Survival age — pure 30s flashes rarely become $10M
    if 4 <= age <= 120:
        score += 6
    elif age < 2:
        score -= 8
        reasons.append("Too fresh — flash risk")

    # Learned plan
    if plan.get("action") == "ENTER":
        score += 6
    elif plan.get("action") == "SKIP":
        score -= 15

    # Safety
    tier_s = token.get("safetyTier") or ""
    if tier_s in ("UNSAFE", "AVOID"):
        score -= 25
    elif tier_s == "SAFE_ENTRY":
        score += 5

    score = int(max(0, min(100, score)))

    # Alert thresholds — multi-stage so we don't only wake at $40k
    alert = False
    priority = 50
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
        # Only alert early if structure is truly strong
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
        "mcap_usd": round(mcap),
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
    """Rank and package runner alerts from a token list."""
    prev = prev_mints or set()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in tokens:
        mint = t.get("tokenAddress") or ""
        if not mint or mint in seen:
            continue
        seen.add(mint)
        rr = score_runner_candidate(t)
        if not rr.get("alert") and rr.get("score", 0) < 55:
            continue
        if not rr.get("alert"):
            continue
        item = {
            "tokenAddress": mint,
            "name": t.get("name"),
            "symbol": t.get("symbol"),
            "icon": t.get("icon"),
            "mcap_usd": t.get("mcap_usd"),
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
