"""Graduated / large runners — post-migration multi-million charts.

Moons / Heat / Snipes intentionally skip graduated coins. This lane is for
tokens that already left the curve (or sit well above graduation mcap) and
may still have tradeable structure: runners near ATH or dips with life.
"""

from __future__ import annotations

from typing import Any

from config import GRADUATION_MCAP_USD, PADRE_TRADE_URL
from services.accuracy import holders_known, merge_ath_into_token
from services.avoid_filters import BLOCKED_MINTS, is_hard_avoid
from services.bundle_sniper import analyze_bundle_and_snipers
from services.runner_radar import extract_ath_mcap, extract_mcap_usd, is_crashed_runner
from services.social_signals import analyze_social_narrative

# Post early-entry universe
GRAD_MCAP_MIN = 80_000.0  # above typical heat/snipe bands
GRAD_MCAP_MAX = 150_000_000.0  # $150M cap
# Prefer true graduates or near/post-grad structure
MIN_AGE_MIN = 30.0  # not a fresh sniper chart
MAX_AGE_MIN = 14 * 24 * 60.0  # 14 days
ATH_DEAD = 0.18  # <18% ATH = dead dump
ATH_DIP_LOW = 0.28
ATH_DIP_HIGH = 0.72
ATH_RUNNER = 0.72  # ≥72% ATH = still "running" structure

LABEL_RUNNER = "RUNNER"  # near ATH graduated climber
LABEL_DIP = "DIP"  # pullback with remaining structure
LABEL_WATCH = "WATCH"  # large but mixed signals
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


def _is_graduated(token: dict[str, Any]) -> bool:
    pf = token.get("pumpfun") or {}
    if pf.get("complete") or token.get("complete"):
        return True
    safety = token.get("safety") or {}
    if safety.get("on_bonding_curve") is False:
        return True
    mcap = extract_mcap_usd(token)
    bond = _f(token.get("bonding_progress") or pf.get("bonding_progress"))
    if bond >= 99 or mcap >= GRADUATION_MCAP_USD * 0.95:
        return True
    return False


def graduated_reject_reason(token: dict[str, Any]) -> str | None:
    mint = (token.get("tokenAddress") or token.get("mint") or "").strip()
    if not mint:
        return "missing mint"
    if mint in BLOCKED_MINTS:
        return "blocklist"
    if token.get("skipped"):
        return token.get("skipReason") or "skipped"

    # P0: never rank without successful enrich (parity with moons/snipes)
    if "enrich_ok" in token and token.get("enrich_ok") is not True:
        errs = token.get("enrich_errors") or []
        return "safety unknown — " + (
            ", ".join(str(e) for e in errs[:2]) if errs else "enrich incomplete"
        )

    merge_ath_into_token(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    age = _f(token.get("age_minutes"))

    if mcap > 0 and mcap < GRAD_MCAP_MIN:
        return f"too small ${mcap:,.0f} for graduated lane (need ≥${GRAD_MCAP_MIN:,.0f})"
    if mcap > GRAD_MCAP_MAX:
        return f"too large ${mcap:,.0f}"

    if age < MIN_AGE_MIN:
        return f"too fresh {age:.0f}m — use Heat/Snipes"
    if age > MAX_AGE_MIN:
        return f"too old {age / 1440:.1f}d for active runner lane"

    if not _is_graduated(token) and mcap < GRADUATION_MCAP_USD:
        return "still on early curve — use Moons/Heat"

    # Dead dumps from ATH
    if ath >= GRAD_MCAP_MIN and mcap > 0 and mcap < ath * ATH_DEAD:
        return f"dead dump −{(1 - mcap / ath) * 100:.0f}% from ATH"

    hard, hard_why = is_hard_avoid(token)
    if hard:
        avoid = (
            token.get("avoid")
            or (token.get("safetyReport") or {}).get("avoid")
            or (token.get("safety") or {}).get("avoid")
            or {}
        )
        flags = set(avoid.get("flags") or [])
        # Hard capital threats only
        block = {
            "blocklist",
            "banned",
            "rugged",
            "honeypot",
            "mint_authority",
            "freeze_authority",
            "adult_bait",
            "spam_deploy_tool",
        }
        if flags & block or not flags:
            return hard_why or "hard avoid"

    safety = token.get("safety") or {}
    if safety.get("is_honeypot") or safety.get("rugged") or safety.get("honeypot"):
        return "honeypot / rugged"
    if safety.get("error"):
        return "safety error / incomplete audit"

    # Extreme one-way crash candles
    pc = token.get("priceChange") or (token.get("market") or {}).get("priceChange") or {}
    if _f(pc.get("h1")) <= -55 or _f(pc.get("h6")) <= -70:
        return "violent dump candle"

    crashed, why = is_crashed_runner(token)
    # For graduated, only honor hard crash if also dead from ATH
    if crashed and ath > 0 and mcap < ath * ATH_DIP_LOW:
        return why or "crashed runner"

    # Bundle critical wall (weaker than snipes, stronger than none)
    bs = token.get("bundleSniper")
    if isinstance(bs, dict):
        if bs.get("hard_reject") and str(bs.get("overall") or "").lower() in (
            "critical",
            "high",
        ):
            return bs.get("summary") or "bundle/sniper hard reject"
        sn = (bs.get("snipers") or {}) if isinstance(bs.get("snipers"), dict) else {}
        if sn.get("risk_level") == "critical":
            return "sniper critical"

    return None


def _score_graduated(token: dict[str, Any]) -> tuple[int, list[str], dict[str, Any]]:
    why: list[str] = []
    score = 35
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    age = _f(token.get("age_minutes"))
    ath_ret = (100 * mcap / ath) if ath > 0 and mcap > 0 else None
    pc = token.get("priceChange") or (token.get("market") or {}).get("priceChange") or {}
    m5, h1, h6, h24 = _f(pc.get("m5")), _f(pc.get("h1")), _f(pc.get("h6")), _f(pc.get("h24"))
    tx = token.get("txActivity") or {}
    social = token.get("socialSignals")
    if not isinstance(social, dict):
        pf = token.get("pumpfun") or {}
        social = analyze_social_narrative(
            pump_coin=pf,
            name=token.get("name") or "",
            symbol=token.get("symbol") or "",
            description=pf.get("description") or "",
        )
        token["socialSignals"] = social
    replies = _i(social.get("replies") or (token.get("pumpfun") or {}).get("reply_count"))
    graduated = _is_graduated(token)
    hk = holders_known(token)

    meta = {
        "ath_retention_pct": round(ath_ret, 1) if ath_ret is not None else None,
        "graduated": graduated,
        "replies": replies,
        "holders_known": hk,
        "m5": m5,
        "h1": h1,
    }

    if graduated:
        score += 12
        why.append("Graduated / off bonding curve")
    else:
        score += 4
        why.append("Large mcap near graduation size")

    # Size bands
    if 100_000 <= mcap <= 5_000_000:
        score += 12
        why.append(f"Large-runner band ${mcap:,.0f}")
    elif 5_000_000 < mcap <= 50_000_000:
        score += 10
        why.append(f"Mega size ${mcap:,.0f}")
    elif mcap > 50_000_000:
        score += 6
        why.append(f"Blue-chip size ${mcap:,.0f}")
    else:
        score += 4

    # ATH structure
    if ath_ret is not None:
        if ath_ret >= 85:
            score += 18
            why.append(f"Near ATH ({ath_ret:.0f}%) — runner structure")
        elif ath_ret >= ATH_RUNNER * 100:
            score += 12
            why.append(f"Holding ATH zone ({ath_ret:.0f}%)")
        elif ATH_DIP_LOW * 100 <= ath_ret < ATH_RUNNER * 100:
            score += 10
            why.append(f"Dip zone {ath_ret:.0f}% ATH — bounce candidate")
        elif ath_ret >= ATH_DEAD * 100:
            score -= 6
            why.append(f"Deep fade {ath_ret:.0f}% ATH")
        else:
            score -= 20

    # Momentum
    if -5 <= m5 <= 25:
        score += 6
    elif m5 > 25:
        score += 3
        why.append(f"Hot m5 +{m5:.0f}%")
    elif m5 < -20:
        score -= 8
        why.append(f"m5 {m5:.0f}%")

    if h1 >= 5:
        score += 8
        why.append(f"h1 +{h1:.0f}%")
    elif -15 <= h1 < 5:
        score += 2
    elif h1 < -25:
        score -= 10

    if h6 >= 10:
        score += 6
    if h24 >= 20:
        score += 4
        why.append(f"h24 +{h24:.0f}%")
    elif h24 <= -40:
        score -= 8
        why.append(f"h24 {h24:.0f}% heavy")

    if tx.get("tilt") == "UP" or tx.get("in_sweet_spot"):
        score += 10
        why.append(tx.get("summary") or "Tx up")
    elif tx.get("tilt") == "DOWN":
        score -= 10
        why.append("Tx tilt DOWN")

    if replies >= 50:
        score += 8
        why.append(f"{replies} replies")
    elif replies >= 15:
        score += 4
    elif replies == 0 and not social.get("real_x"):
        score -= 4
        why.append("Thin social")

    if social.get("has_edge") or social.get("influencer_tweet"):
        score += 8
        why.append(social.get("summary") or "Narrative edge")

    if hk:
        score += 4
    else:
        score -= 3
        why.append("Holders unknown")

    # Age: graduated runners often hours–days old
    if 60 <= age <= 3 * 24 * 60:
        score += 6
    elif age > 7 * 24 * 60:
        score -= 4
        why.append("Aging chart")

    score = max(0, min(99, score))
    return score, why, meta


def evaluate_graduated(token: dict[str, Any]) -> dict[str, Any]:
    reason = graduated_reject_reason(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    ath_ret = round(100 * mcap / ath, 1) if ath > 0 and mcap > 0 else None

    if reason:
        return {
            "eligible": False,
            "reject": reason,
            "grad_score": 0,
            "label": LABEL_SKIP,
            "confidence": 0,
            "risk_level": "skip",
            "why": [reason],
            "ath_retention_pct": ath_ret,
            "plan": None,
        }

    score, why, meta = _score_graduated(token)
    graduated = bool(meta.get("graduated"))
    conf = score

    hk = holders_known(token)
    if (
        score >= 68
        and ath_ret is not None
        and ath_ret >= ATH_RUNNER * 100
        and hk
    ):
        label = LABEL_RUNNER
        conf = max(conf, 62)
        risk = "elevated"
    elif score >= 55 and ath_ret is not None and ATH_DIP_LOW * 100 <= ath_ret < ATH_RUNNER * 100:
        label = LABEL_DIP
        conf = min(conf, 58)
        risk = "high"
        why = why + ["Dip-buy structure — not a bottom call"]
        if not hk:
            conf = min(conf, 50)
            why = why + ["Holders unknown — not RUNNER grade"]
    elif score >= 48:
        label = LABEL_WATCH
        conf = min(conf, 52)
        risk = "very_high"
        why = why + ["Large runner watch — mixed signals"]
        if not hk:
            conf = min(conf, 48)
    else:
        return {
            "eligible": False,
            "reject": "weak graduated structure",
            "grad_score": score,
            "label": LABEL_SKIP,
            "confidence": min(conf, 40),
            "risk_level": "skip",
            "why": why[:5],
            "ath_retention_pct": ath_ret,
            "plan": None,
        }

    plan = {
        "entry_usd": round(mcap, 0) if mcap else None,
        "invalidation_usd": round(mcap * 0.75, 0) if mcap else None,
        "ath_usd": round(ath, 0) if ath else None,
        "size_advice": (
            "GRADUATED / LARGE lane — not early heat. Size smaller than moons. "
            "Prefer RUNNER near ATH or DIP with rising h1/tx. Cut if new local low."
        ),
        "rule": "Post-migration / large mcap only. Different game than $6k snipes.",
    }

    return {
        "eligible": True,
        "reject": None,
        "grad_score": score,
        "label": label,
        "confidence": max(0, min(99, int(conf))),
        "risk_level": risk,
        "why": why[:8],
        "ath_retention_pct": ath_ret,
        "graduated": graduated,
        "holders_known": meta.get("holders_known"),
        "replies": meta.get("replies"),
        "plan": plan,
        "meta": meta,
    }


def filter_and_rank_graduated(
    tokens: list[dict[str, Any]],
    *,
    min_score: int = 48,
    limit: int = 16,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        ev = evaluate_graduated(t)
        if not ev.get("eligible"):
            continue
        if int(ev.get("grad_score") or 0) < min_score:
            continue
        row = dict(t)
        row["grad"] = ev
        row["grad_score"] = ev["grad_score"]
        row["grad_label"] = ev["label"]
        row["confidence"] = ev["confidence"]
        row["risk_level"] = ev.get("risk_level")
        row["ath_retention_pct"] = ev.get("ath_retention_pct")
        out.append(row)

    rank = {LABEL_RUNNER: 0, LABEL_DIP: 1, LABEL_WATCH: 2}
    out.sort(
        key=lambda x: (
            rank.get(x.get("grad_label") or "", 9),
            -(x.get("grad_score") or 0),
            -(x.get("confidence") or 0),
        )
    )
    return out[:limit]


def graduated_card_from_coin(coin: dict, *, source: str = "pump.fun") -> dict[str, Any] | None:
    """Pre-enrich card for graduated / large runners (allows complete=true)."""
    from services.avoid_filters import analyze_avoid_flags
    from services.pumpfun import PumpFunClient

    mint = (coin.get("mint") or "").strip()
    if not mint or mint in BLOCKED_MINTS:
        return None
    if coin.get("is_banned"):
        return None
    mcap = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or 0)
    bond = PumpFunClient.bonding_progress(coin)
    age = PumpFunClient.coin_age_minutes(coin)
    complete = bool(coin.get("complete"))

    if mcap < GRAD_MCAP_MIN * 0.9 or mcap > GRAD_MCAP_MAX * 1.05:
        return None
    if age < MIN_AGE_MIN * 0.5 or age > MAX_AGE_MIN + 60:
        return None
    # Must look graduated-ish
    if not complete and mcap < GRADUATION_MCAP_USD * 0.9 and bond < 95:
        return None
    if ath >= GRAD_MCAP_MIN and mcap > 0 and mcap < ath * ATH_DEAD:
        return None

    avoid = analyze_avoid_flags(
        safety={
            "mint": mint,
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
            "description": coin.get("description"),
        },
        pump=coin,
        mint=mint,
    )
    hard, _ = is_hard_avoid({"avoid": avoid})
    flags = set(avoid.get("flags") or [])
    if hard and flags & {"blocklist", "banned", "rugged", "honeypot", "adult_bait"}:
        return None

    social = analyze_social_narrative(
        pump_coin=coin,
        name=coin.get("name") or "",
        symbol=coin.get("symbol") or "",
        description=coin.get("description") or "",
    )
    return {
        "tokenAddress": mint,
        "chainId": "solana",
        "name": coin.get("name") or "Unknown",
        "symbol": coin.get("symbol") or "?",
        "icon": coin.get("image_uri"),
        "mcap_usd": mcap,
        "ath_mcap": ath or None,
        "bonding_progress": round(bond, 1),
        "age_minutes": round(age, 1),
        "complete": complete,
        "pumpfun": {
            "usd_market_cap": mcap,
            "ath_market_cap": ath or None,
            "bonding_progress": bond,
            "twitter": coin.get("twitter"),
            "telegram": coin.get("telegram"),
            "website": coin.get("website"),
            "description": coin.get("description"),
            "reply_count": coin.get("reply_count", 0),
            "creator": coin.get("creator"),
            "image_uri": coin.get("image_uri"),
            "complete": complete,
        },
        "safetyReport": {"avoid": avoid},
        "avoid": avoid,
        "socialSignals": social,
        "pump_url": f"https://pump.fun/coin/{mint}",
        "padre_url": f"{PADRE_TRADE_URL}/trade/solana/{mint}",
        "source": source,
        "mode": "graduated_runners",
    }
