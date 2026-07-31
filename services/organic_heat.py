"""Organic Heat — high-recall feed for early climbers Moons miss.

Design:
  - Moons = high precision (narrative + near-ATH + holders)
  - Heat  = higher recall (volume/replies/structure), explicit RISKY labels

Never claims "safe". Always warn size risk. Still blocks rugs / hard avoids /
critical bundles / deep dumps.
"""

from __future__ import annotations

from typing import Any

from services.accuracy import holders_known, merge_ath_into_token
from services.avoid_filters import BLOCKED_MINTS, is_hard_avoid
from services.bundle_sniper import analyze_bundle_and_snipers
from services.runner_radar import extract_ath_mcap, extract_mcap_usd, is_crashed_runner
from services.social_signals import analyze_social_narrative

# Wider than moons — catch earlier / later meta
HEAT_MCAP_MIN = 2_500.0
HEAT_MCAP_MAX = 95_000.0
MIN_AGE_MIN = 0.8
MAX_AGE_MIN = 180.0
# Allow deeper pullbacks than moon −12% wall
ATH_HARD_DUMP = 0.55  # −45% = dead for heat too
ATH_SOFT_FLOOR = 0.68  # −32% still visible as RISKY if heat signals

LABEL_HEAT = "HEAT"  # best organic heat (still not "safe moon")
LABEL_WARM = "WARM"  # mid heat
LABEL_RISKY = "RISKY"  # thin book / partial enrich — size dust only
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


def _ensure_social(token: dict[str, Any]) -> dict[str, Any]:
    social = token.get("socialSignals")
    if isinstance(social, dict) and social.get("edge_score") is not None:
        return social
    pf = token.get("pumpfun") or {}
    social = analyze_social_narrative(
        pump_coin=pf,
        name=token.get("name") or pf.get("name") or "",
        symbol=token.get("symbol") or pf.get("symbol") or "",
        description=pf.get("description") or token.get("description") or "",
    )
    token["socialSignals"] = social
    return social


def _bundled_pct(token: dict[str, Any]) -> float | None:
    bun = token.get("bundle") if isinstance(token.get("bundle"), dict) else {}
    bs = token.get("bundleSniper") if isinstance(token.get("bundleSniper"), dict) else {}
    for src in (bun, bs, (bs.get("bundle") or {}) if isinstance(bs, dict) else {}):
        if isinstance(src, dict) and src.get("bundled_pct") is not None:
            try:
                return float(src["bundled_pct"])
            except (TypeError, ValueError):
                pass
    return None


def heat_reject_reason(token: dict[str, Any]) -> str | None:
    """Hard rejects only — intentionally looser than moon/snipe."""
    mint = (token.get("tokenAddress") or token.get("mint") or "").strip()
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

    if mcap > 0 and mcap < HEAT_MCAP_MIN:
        return f"too small ${mcap:,.0f}"
    if mcap > HEAT_MCAP_MAX:
        return f"too large ${mcap:,.0f} for heat band"
    if age < MIN_AGE_MIN:
        return f"too fresh {age:.1f}m"
    if age > MAX_AGE_MIN:
        return f"too old {age:.0f}m"

    crashed, why = is_crashed_runner(token)
    if crashed:
        return why or "crashed"

    # Deep dump only (moons use −12%; heat allows pullbacks)
    if ath >= 3_000 and mcap > 0 and mcap < ath * ATH_HARD_DUMP:
        return f"hard dump −{(1 - mcap / ath) * 100:.0f}% from ATH"

    hard, hard_why = is_hard_avoid(token)
    if hard:
        return hard_why or "hard avoid"

    safety = token.get("safety") or {}
    if safety.get("is_honeypot") or safety.get("rugged") or safety.get("honeypot"):
        return "honeypot / rugged"
    if safety.get("error") and token.get("enrich_ok") is True:
        # enrich claimed ok but safety error — treat as incomplete
        pass

    # After enrich pipeline: allow incomplete as RISKY later, but reject pure fail
    if "enrich_ok" in token and token.get("enrich_ok") is not True:
        errs = token.get("enrich_errors") or []
        # Pre-enrich capital rejects already set; incomplete enrich → soft path
        # Only hard-block if honeypot-ish errors
        joined = " ".join(str(e) for e in errs).lower()
        if "honeypot" in joined or "rugged" in joined:
            return "safety fail"
        # keep going without enrich — scored as RISKY

    pc = token.get("priceChange") or (token.get("market") or {}).get("priceChange") or {}
    if _f(pc.get("m5")) <= -35 or _f(pc.get("h1")) <= -45:
        return "sharp dump candle"

    # Bundle critical only
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
        if bs.get("hard_reject") and (
            str(bs.get("overall") or "").lower() in ("critical", "high")
            or _f((bs.get("bundle") or {}).get("bundled_pct")) >= 25
        ):
            return bs.get("summary") or "bundle/sniper hard reject"
        sn = (bs.get("snipers") or {}) if isinstance(bs.get("snipers"), dict) else {}
        if sn.get("risk_level") == "critical":
            return "sniper critical"

    bun = _bundled_pct(token)
    if bun is not None and bun >= 28:
        return f"bundled {bun:.0f}% — farm"

    return None


def _heat_signals(token: dict[str, Any]) -> tuple[int, list[str], dict[str, Any]]:
    """Score organic heat 0–100 from replies, structure, flow, mild narrative."""
    why: list[str] = []
    score = 28
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    bond = _f(token.get("bonding_progress"))
    age = _f(token.get("age_minutes"))
    social = _ensure_social(token)
    replies = _i(social.get("replies") or (token.get("pumpfun") or {}).get("reply_count"))
    pc = token.get("priceChange") or (token.get("market") or {}).get("priceChange") or {}
    m5 = _f(pc.get("m5"))
    h1 = _f(pc.get("h1"))
    tx = token.get("txActivity") or {}
    ath_ret = (100 * mcap / ath) if ath > 0 and mcap > 0 else None
    hk = holders_known(token)
    bun = _bundled_pct(token)
    meta: dict[str, Any] = {
        "replies": replies,
        "ath_retention_pct": round(ath_ret, 1) if ath_ret is not None else None,
        "holders_known": hk,
        "bundle_pct": bun,
        "m5": m5,
    }

    # --- Community heat ---
    if replies >= 40:
        score += 22
        why.append(f"{replies} replies — hot chat")
    elif replies >= 18:
        score += 14
        why.append(f"{replies} replies")
    elif replies >= 8:
        score += 8
        why.append(f"{replies} replies")
    elif replies >= 3:
        score += 3
    else:
        score -= 4
        why.append("Thin replies")

    # --- Structure / stage ---
    if 4_000 <= mcap <= 45_000:
        score += 14
        why.append(f"Climb band ${mcap:,.0f}")
    elif 2_500 <= mcap < 4_000:
        score += 8
        why.append(f"Early ${mcap:,.0f}")
    elif mcap > 45_000:
        score += 6
        why.append(f"Late heat ${mcap:,.0f}")

    if bond >= 40:
        score += 10
        why.append(f"Bonding {bond:.0f}%")
    elif bond >= 18:
        score += 6
    elif bond >= 8:
        score += 3

    if 1.5 <= age <= 90:
        score += 6
    elif age < 1.5:
        score += 2
        why.append("Very fresh — sniper risk")

    # --- Momentum (allow mild red if still heated) ---
    if 3 <= m5 <= 80:
        score += 12
        why.append(f"m5 +{m5:.0f}%")
    elif 0 <= m5 < 3:
        score += 4
    elif -12 <= m5 < 0:
        score += 1
        why.append(f"m5 pullback {m5:.0f}%")
    elif m5 > 80:
        score -= 4
        why.append("Parabolic m5 — late")
    elif m5 < -12:
        score -= 10
        why.append(f"m5 {m5:.0f}% weak")

    if h1 >= 10:
        score += 6
    elif h1 <= -25:
        score -= 8

    # ATH retention: heat allows pullbacks
    if ath_ret is not None:
        if ath_ret >= 92:
            score += 10
            why.append(f"Near ATH ({ath_ret:.0f}%)")
        elif ath_ret >= 80:
            score += 7
        elif ath_ret >= ATH_SOFT_FLOOR * 100:
            score += 3
            why.append(f"Pullback {ath_ret:.0f}% ATH — organic watch")
        else:
            score -= 12
            why.append(f"Deep pullback {ath_ret:.0f}% ATH")

    # --- Tx / flow ---
    if tx.get("tilt") == "UP" or tx.get("in_sweet_spot"):
        score += 10
        why.append(tx.get("summary") or "Tx up")
    elif tx.get("tilt") == "DOWN":
        score -= 12
        why.append("Tx tilt DOWN")

    # --- Mild narrative bonus (not required) ---
    if social.get("influencer_tweet"):
        score += 14
        why.append(social.get("summary") or "Influencer edge")
    elif social.get("has_edge"):
        score += 8
        why.append(social.get("summary") or "Narrative edge")
    elif social.get("narratives"):
        score += 4
        why.append(str((social.get("narratives") or ["meta"])[0]))

    if social.get("real_x"):
        score += 4
    if social.get("namejack_risk") and not social.get("influencer_tweet"):
        score -= 8
        why.append("Name-jack packaging")

    # --- Book ---
    if hk:
        score += 8
        if bun is not None and bun <= 5:
            score += 6
            why.append(f"Clean book · bundled {bun:.0f}%")
        elif bun is not None and bun <= 12:
            score += 2
        elif bun is not None and bun > 18:
            score -= 10
            why.append(f"Bundled {bun:.0f}%")
    else:
        score -= 6
        why.append("Holders unknown — RISKY")

    if token.get("realtime"):
        score += 5
        why.append("Realtime hit")

    if token.get("enrich_ok") is not True:
        score -= 8
        why.append("Incomplete safety enrich")

    score = max(0, min(99, score))
    return score, why, meta


def evaluate_heat(token: dict[str, Any]) -> dict[str, Any]:
    reason = heat_reject_reason(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    ath_ret = round(100 * mcap / ath, 1) if ath > 0 and mcap > 0 else None
    social = _ensure_social(token)

    if reason:
        return {
            "eligible": False,
            "reject": reason,
            "heat_score": 0,
            "label": LABEL_SKIP,
            "confidence": 0,
            "risk_level": "skip",
            "why": [reason],
            "ath_retention_pct": ath_ret,
            "plan": None,
        }

    score, why, meta = _heat_signals(token)
    hk = holders_known(token)
    bun = _bundled_pct(token)
    enrich_ok = token.get("enrich_ok") is True
    replies = _i(meta.get("replies"))
    ath_ok = ath_ret is None or ath_ret >= ATH_SOFT_FLOOR * 100

    # Labels: HEAT needs real heat + not garbage book
    conf = score
    if (
        score >= 68
        and replies >= 12
        and ath_ok
        and (enrich_ok or hk)
        and (bun is None or bun <= 15)
    ):
        label = LABEL_HEAT
        conf = max(conf, 62)
        risk = "elevated"
    elif score >= 52 and ath_ok and (replies >= 5 or token.get("realtime") or social.get("has_edge")):
        label = LABEL_WARM
        conf = min(conf, 58)
        risk = "high"
    elif score >= 42 and ath_ok:
        label = LABEL_RISKY
        conf = min(conf, 48)
        risk = "very_high"
        why = why + ["Dust size only — high false-positive rate"]
    else:
        return {
            "eligible": False,
            "reject": "not enough organic heat",
            "heat_score": score,
            "label": LABEL_SKIP,
            "confidence": min(conf, 40),
            "risk_level": "skip",
            "why": why[:5] or ["Weak heat"],
            "ath_retention_pct": ath_ret,
            "plan": None,
        }

    if not hk:
        label = LABEL_RISKY if label == LABEL_HEAT else label
        if label == LABEL_WARM and score < 60:
            label = LABEL_RISKY
        conf = min(conf, 50)
        risk = "very_high"

    if not enrich_ok:
        label = LABEL_RISKY
        conf = min(conf, 45)
        risk = "very_high"

    plan = {
        "entry_usd": round(mcap, 0) if mcap else None,
        "invalidation_usd": round(mcap * 0.70, 0) if mcap else None,
        "size_advice": (
            "HIGH RECALL mode — many of these dump. Dust size only. "
            "Not a moon rec. Cut fast on −30% or dead replies."
        ),
        "rule": "Organic heat ≠ safe. Prefer Moons tab for capital protection.",
    }

    return {
        "eligible": True,
        "reject": None,
        "heat_score": score,
        "label": label,
        "confidence": max(0, min(99, int(conf))),
        "risk_level": risk,
        "why": why[:7],
        "ath_retention_pct": ath_ret,
        "holders_known": hk,
        "bundle_pct": bun,
        "replies": replies,
        "narrative": social.get("summary") or "",
        "badges": social.get("badges") or [],
        "plan": plan,
        "meta": meta,
    }


def filter_and_rank_heat(
    tokens: list[dict[str, Any]],
    *,
    min_score: int = 42,
    limit: int = 16,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        ev = evaluate_heat(t)
        if not ev.get("eligible"):
            continue
        if int(ev.get("heat_score") or 0) < min_score:
            continue
        row = dict(t)
        row["heat"] = ev
        row["heat_score"] = ev["heat_score"]
        row["heat_label"] = ev["label"]
        row["confidence"] = ev["confidence"]
        row["risk_level"] = ev.get("risk_level")
        row["ath_retention_pct"] = ev.get("ath_retention_pct")
        out.append(row)

    rank = {LABEL_HEAT: 0, LABEL_WARM: 1, LABEL_RISKY: 2}
    out.sort(
        key=lambda x: (
            rank.get(x.get("heat_label") or "", 9),
            -(x.get("heat_score") or 0),
            -(x.get("confidence") or 0),
            -_i((x.get("heat") or {}).get("replies")),
        )
    )
    return out[:limit]


def heat_card_from_coin(coin: dict, *, source: str = "pump.fun") -> dict[str, Any] | None:
    """Pre-enrich card — wider band than moon/snipe."""
    from config import PADRE_TRADE_URL
    from services.avoid_filters import analyze_avoid_flags
    from services.pumpfun import PumpFunClient

    mint = (coin.get("mint") or "").strip()
    if not mint or mint in BLOCKED_MINTS:
        return None
    if coin.get("complete") or coin.get("is_banned"):
        return None
    mcap = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or 0)
    bond = PumpFunClient.bonding_progress(coin)
    age = PumpFunClient.coin_age_minutes(coin)
    if mcap < HEAT_MCAP_MIN * 0.9 or mcap > HEAT_MCAP_MAX * 1.1:
        return None
    if age < 0.5 or age > MAX_AGE_MIN + 20:
        return None
    # Hard dump prefilter
    if ath >= 3_000 and mcap > 0 and mcap < ath * ATH_HARD_DUMP:
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
    if hard or avoid.get("hard_avoid"):
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
        },
        "safetyReport": {"avoid": avoid},
        "avoid": avoid,
        "socialSignals": social,
        "pump_url": f"https://pump.fun/coin/{mint}",
        "padre_url": f"{PADRE_TRADE_URL}/trade/solana/{mint}",
        "source": source,
        "mode": "organic_heat",
    }
