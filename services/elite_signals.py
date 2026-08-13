"""Elite buy-signal evaluation — smart-money hit + full safety stack.

A signal is only eligible when:
  1. ≥1 elite wallet is on the book (holder match)
  2. Token passes capital hard-avoids / honeypot / flash / wash
  3. Survival floor (~$7k), age, near-ATH soft floors
  4. Not critical bundle / serial farm

Labels:
  ELITE — 2+ elites or S-tier hit + clean book
  COPY  — single elite buy, clean enough to alert
  WATCH — thin signal / partial enrich
"""

from __future__ import annotations

from typing import Any

from services.accuracy import holders_known, merge_ath_into_token
from services.avoid_filters import BLOCKED_MINTS, is_hard_avoid
from services.bundle_sniper import analyze_bundle_and_snipers
from services.elite_traders import match_elites_on_token
from services.runner_radar import extract_ath_mcap, extract_mcap_usd, is_crashed_runner

try:
    from config import MONEY_ENTRY_MIN_USD, SURVIVAL_MCAP_USD
except Exception:
    MONEY_ENTRY_MIN_USD = 7_000.0
    SURVIVAL_MCAP_USD = 7_000.0

ELITE_MCAP_MIN = float(MONEY_ENTRY_MIN_USD or SURVIVAL_MCAP_USD or 7_000)
ELITE_MCAP_MAX = 80_000.0
MIN_AGE_MIN = 2.0
MAX_AGE_MIN = 180.0
ATH_HARD = 0.55  # −45%
ATH_SOFT = 0.72

LABEL_ELITE = "ELITE"
LABEL_COPY = "COPY"
LABEL_WATCH = "WATCH"
LABEL_SKIP = "SKIP"


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


def elite_reject_reason(token: dict[str, Any]) -> str | None:
    """Hard rejects — same capital walls as money feeds + elite-specific."""
    mint = str(token.get("tokenAddress") or token.get("mint") or "").strip()
    if not mint:
        return "missing mint"
    if mint in BLOCKED_MINTS:
        return "blocklist"
    if token.get("skipped"):
        return token.get("skipReason") or "skipped"

    merge_ath_into_token(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    age = _f(token.get("age_minutes"))

    if mcap > 0 and mcap < ELITE_MCAP_MIN:
        return f"below survival ${ELITE_MCAP_MIN:,.0f} (${mcap:,.0f})"
    if mcap > ELITE_MCAP_MAX:
        return f"above elite band ${mcap:,.0f}"
    if 0 < age < MIN_AGE_MIN:
        return f"too fresh {age:.1f}m"
    if age > MAX_AGE_MIN:
        return f"too old {age:.0f}m"

    # Flash holders / fees
    try:
        from services.avoid_filters import flash_holders_reason

        holders = _i((token.get("safety") or {}).get("total_holders"))
        fh = flash_holders_reason(holders, age)
        if fh:
            return fh
    except Exception:
        pass
    try:
        from services.fee_flow import attach_fee_flow, fee_flow_gate

        ff = attach_fee_flow(token)
        ok_f, why_f = fee_flow_gate(ff)
        if not ok_f:
            return why_f or "flash fee war"
    except Exception:
        pass

    crashed, why = is_crashed_runner(token)
    if crashed:
        return why or "dumped"
    if ath >= 3_000 and mcap > 0 and mcap < ath * ATH_HARD:
        return f"hard dump −{(1 - mcap / ath) * 100:.0f}% ATH"

    hard, hard_why = is_hard_avoid(token)
    if hard:
        return hard_why or "hard avoid"

    safety = token.get("safety") or {}
    if safety.get("is_honeypot") or safety.get("rugged") or safety.get("honeypot"):
        return "honeypot / rugged"

    # Dev risk
    try:
        from services.dev_risk import attach_dev_risk, dev_risk_gate

        dr = attach_dev_risk(token)
        ok_d, why_d = dev_risk_gate(dr)
        if not ok_d:
            return why_d or "dev risk"
    except Exception:
        pass

    # Bundle critical
    bs = token.get("bundleSniper")
    if not isinstance(bs, dict) and (safety.get("top_holders") or token.get("enrich_ok")):
        try:
            bs = analyze_bundle_and_snipers(
                safety,
                token.get("pumpfun") or {},
                token.get("market") or {},
                age_minutes=age or None,
                mcap_usd=mcap or None,
            )
            token["bundleSniper"] = bs
        except Exception:
            bs = {}
    if isinstance(bs, dict):
        if bs.get("hard_reject") or str(bs.get("overall") or "").lower() in (
            "critical",
            "high",
        ):
            return bs.get("summary") or "bundle/sniper high risk"
        bun = _f((bs.get("bundle") or {}).get("bundled_pct") or bs.get("bundled_pct"))
        if bun >= 18:
            return f"bundled {bun:.0f}%"

    # One-way wash
    mkt = token.get("market") or {}
    txns = (mkt.get("txns") or {}).get("m5") or {}
    buys = _i(txns.get("buys"))
    sells = _i(txns.get("sells"))
    if buys >= 15 and sells == 0 and mcap < 25_000:
        return "one-way wash"

    if "enrich_ok" in token and token.get("enrich_ok") is not True:
        errs = " ".join(str(e) for e in (token.get("enrich_errors") or [])).lower()
        if "honeypot" in errs or "rugged" in errs:
            return "safety fail"

    # Must have elite match
    hits = match_elites_on_token(token)
    token["_elite_hits"] = hits
    if not hits:
        return "no elite wallet on book"

    return None


def evaluate_elite(token: dict[str, Any]) -> dict[str, Any]:
    reason = elite_reject_reason(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    ath_ret = round(100 * mcap / ath, 1) if ath > 0 and mcap > 0 else None
    hits = token.get("_elite_hits") or match_elites_on_token(token)

    if reason:
        return {
            "eligible": False,
            "reject": reason,
            "elite_score": 0,
            "label": LABEL_SKIP,
            "confidence": 0,
            "why": [reason],
            "elite_hits": hits,
            "elite_count": len(hits),
            "plan": None,
        }

    score = 40
    why: list[str] = []
    n = len(hits)
    s_tiers = sum(1 for h in hits if str(h.get("tier") or "") == "S")
    a_tiers = sum(1 for h in hits if str(h.get("tier") or "") == "A")

    why.append(
        f"{n} elite wallet(s): "
        + ", ".join(str(h.get("label") or h.get("address", "")[:6]) for h in hits[:3])
    )
    score += min(30, n * 12)
    score += s_tiers * 10
    score += a_tiers * 5

    total_pct = sum(_f(h.get("pct")) for h in hits)
    if total_pct >= 3:
        score += 8
        why.append(f"Elite bags ~{total_pct:.1f}% combined")
    elif total_pct >= 1:
        score += 4

    hk = holders_known(token)
    if hk:
        score += 8
    else:
        score -= 8
        why.append("Holders incomplete")

    if ath_ret is not None:
        if ath_ret >= 90:
            score += 12
            why.append(f"Near ATH {ath_ret}%")
        elif ath_ret >= ATH_SOFT * 100:
            score += 6
        else:
            score -= 10
            why.append(f"Pullback {ath_ret}% ATH")

    age = _f(token.get("age_minutes"))
    if 4 <= age <= 60:
        score += 6
    bond = _f(token.get("bonding_progress"))
    if bond >= 25:
        score += 6
        why.append(f"Bond {bond:.0f}%")

    # Mild organic extras
    social = token.get("socialSignals") or {}
    if social.get("has_edge") or social.get("influencer_tweet"):
        score += 6
        why.append("Narrative edge")
    replies = _i(social.get("replies") or (token.get("pumpfun") or {}).get("reply_count"))
    if replies >= 15:
        score += 5

    if token.get("enrich_ok") is not True:
        score -= 8
        why.append("Partial enrich")

    score = max(0, min(99, score))
    enrich_ok = token.get("enrich_ok") is True
    ath_ok = ath_ret is None or ath_ret >= ATH_SOFT * 100

    if (
        score >= 72
        and n >= 2
        and hk
        and enrich_ok
        and ath_ok
        and (s_tiers >= 1 or n >= 2)
    ):
        label = LABEL_ELITE
        conf = max(score, 70)
        risk = "elevated"
    elif score >= 62 and n >= 1 and hk and ath_ok and (enrich_ok or s_tiers):
        label = LABEL_COPY
        conf = min(score, 72)
        risk = "high"
    elif score >= 52 and n >= 1 and ath_ok:
        label = LABEL_WATCH
        conf = min(score, 55)
        risk = "very_high"
        why.append("Thin elite signal — size dust")
    else:
        return {
            "eligible": False,
            "reject": "elite signal too weak after safety",
            "elite_score": score,
            "label": LABEL_SKIP,
            "confidence": min(score, 40),
            "why": why[:6],
            "elite_hits": hits,
            "elite_count": n,
            "plan": None,
        }

    tp2x = round(mcap * 2.0, 0) if mcap else None
    plan = {
        "entry_usd": round(mcap, 0) if mcap else None,
        "take_profit_2x_usd": tp2x,
        "invalidation_usd": round(mcap * 0.78, 0) if mcap else None,
        "size_advice": (
            "Copy-trade signal only. Elite wallets can be wrong / late. "
            "Size small, cut −22%, book into 1.5–2×. Full safety already applied."
        ),
        "rule": "Elite buy signal + capital filters. Not auto-buy. DYOR.",
        "elites": [h.get("label") for h in hits[:5]],
    }

    return {
        "eligible": True,
        "reject": None,
        "elite_score": score,
        "label": label,
        "confidence": max(0, min(99, int(conf))),
        "risk_level": risk,
        "why": why[:8],
        "ath_retention_pct": ath_ret,
        "holders_known": hk,
        "elite_hits": hits,
        "elite_count": n,
        "target_2x_usd": tp2x,
        "plan": plan,
    }


def filter_and_rank_elite(
    tokens: list[dict[str, Any]],
    *,
    min_score: int = 52,
    limit: int = 16,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        ev = evaluate_elite(t)
        if not ev.get("eligible"):
            continue
        if int(ev.get("elite_score") or 0) < min_score:
            continue
        row = dict(t)
        row["elite"] = ev
        row["elite_score"] = ev["elite_score"]
        row["elite_label"] = ev["label"]
        row["confidence"] = ev["confidence"]
        row["elite_hits"] = ev.get("elite_hits")
        out.append(row)

    rank = {LABEL_ELITE: 0, LABEL_COPY: 1, LABEL_WATCH: 2}
    out.sort(
        key=lambda x: (
            rank.get(x.get("elite_label") or "", 9),
            -int(x.get("elite_score") or 0),
            -int((x.get("elite") or {}).get("elite_count") or 0),
            -float(x.get("mcap_usd") or 0),
        )
    )
    return out[:limit]
