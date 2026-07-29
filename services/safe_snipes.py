"""Safe snipes for ~2× take-profit setups.

Capital-protection first: clean book, no dump, entry band where 2× is
reachable before migration. Narrative edge is a bonus — not required
(unlike the main Moon feed).
"""

from __future__ import annotations

from typing import Any

from services.avoid_filters import BLOCKED_MINTS, analyze_avoid_flags, is_hard_avoid
from services.bundle_sniper import analyze_bundle_and_snipers
from services.moon_picks import extract_ath_mcap, extract_mcap_usd
from services.runner_radar import is_crashed_runner

# Entry zone: 2× still under ~graduation band
SNIPE_MCAP_MIN = 3_500.0
SNIPE_MCAP_MAX = 16_000.0
TARGET_MULT = 2.0
GRAD_SOFT_CAP = 55_000.0  # 2× should not need moonshot migration

MIN_AGE_MIN = 1.5
MAX_AGE_MIN = 75.0
MIN_ATH_RETENTION = 0.70  # hide fades from ATH
# SNIPE grade: clean book only. SETUP can stretch to ~8% bundle.
MAX_BUNDLED_PCT = 5.0
MAX_BUNDLED_PCT_SETUP = 8.0
MAX_SNIPER_WALLET = 12.0
MAX_SNIPER_WALLET_SETUP = 15.0

LABEL_SNIPE = "SNIPE"
LABEL_SETUP = "SETUP"
LABEL_SKIP = "SKIP"


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _bundled_pct(token: dict[str, Any]) -> float | None:
    bun = token.get("bundle") if isinstance(token.get("bundle"), dict) else {}
    bs = token.get("bundleSniper") if isinstance(token.get("bundleSniper"), dict) else {}
    for src in (bun, bs, (bs.get("bundle") or {})):
        if isinstance(src, dict) and src.get("bundled_pct") is not None:
            try:
                return float(src["bundled_pct"])
            except (TypeError, ValueError):
                pass
    return None


def _sniper_level(token: dict[str, Any]) -> str:
    sn = token.get("snipers") if isinstance(token.get("snipers"), dict) else {}
    bs = token.get("bundleSniper") if isinstance(token.get("bundleSniper"), dict) else {}
    if not sn:
        sn = (bs.get("snipers") or {}) if isinstance(bs, dict) else {}
    return str(sn.get("risk_level") or "unknown").lower()


def snipe_reject_reason(token: dict[str, Any]) -> str | None:
    """Hard rejects for safe 2× snipes (no narrative requirement)."""
    mint = (token.get("tokenAddress") or token.get("mint") or "").strip()
    if mint in BLOCKED_MINTS:
        return "blocklisted mint"
    if token.get("skipped"):
        return token.get("skipReason") or "skipped"

    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    age = _f(token.get("age_minutes"))

    if mcap < SNIPE_MCAP_MIN:
        return f"mcap ${mcap:,.0f} below 2× entry band (${SNIPE_MCAP_MIN:,.0f}+)"
    if mcap > SNIPE_MCAP_MAX:
        return f"mcap ${mcap:,.0f} above 2× entry band (max ${SNIPE_MCAP_MAX:,.0f})"
    if age < MIN_AGE_MIN:
        return f"too fresh {age:.1f}m — sniper window"
    if age > MAX_AGE_MIN:
        return f"too old {age:.0f}m for snipe entry"

    # 2× target still “early structure”
    if mcap * TARGET_MULT > GRAD_SOFT_CAP:
        return "2× target too high — not a small snipe"

    crashed, why = is_crashed_runner(token)
    if crashed:
        return why or "already dumped"
    if ath >= 3_000 and mcap > 0 and mcap < ath * MIN_ATH_RETENTION:
        return f"faded from ATH ({100 * mcap / ath:.0f}% retained)"

    hard, hard_why = is_hard_avoid(token)
    if hard:
        return hard_why or "hard avoid"

    # Incomplete enrich → never present as a "safe" snipe
    if token.get("enrich_ok") is False:
        errs = token.get("enrich_errors") or []
        return "safety unknown — " + (
            ", ".join(str(e) for e in errs[:2]) if errs else "enrich incomplete"
        )

    safety = token.get("safety") or {}
    if safety.get("is_honeypot") or safety.get("rugged") or safety.get("honeypot"):
        return "honeypot / rugged"
    if safety.get("passed") is False and safety.get("issues"):
        issues = " ".join(str(i) for i in (safety.get("issues") or [])[:4]).lower()
        if any(k in issues for k in ("honeypot", "rugged", "frozen", "mint authority")):
            return "safety fail: mint/freeze risk"

    deep = token.get("deepAnalysis") or {}
    if deep.get("dump", {}).get("is_dumped"):
        return deep.get("dump", {}).get("reason") or "dumped"
    if deep.get("verdict") == "SKIP":
        return deep.get("summary") or "deep skip"

    pc = token.get("priceChange") or (token.get("market") or {}).get("priceChange") or {}
    if _f(pc.get("m5")) <= -18 or _f(pc.get("h1")) <= -25:
        return "sharp price dump candle"

    tx = token.get("txActivity") or {}
    if tx.get("tilt") == "DOWN" and _f(tx.get("total_m5") or tx.get("total") or 0) >= 8:
        return "tx tilt DOWN"

    # Bundle / snipers — stricter than moon feed
    bs = token.get("bundleSniper")
    if not isinstance(bs, dict):
        bs = analyze_bundle_and_snipers(
            token.get("safety") or {},
            token.get("pumpfun") or {},
            token.get("market") or {},
            age_minutes=age or None,
            mcap_usd=mcap or None,
        )
        token["bundleSniper"] = bs
        token["bundle"] = bs.get("bundle")
        token["snipers"] = bs.get("snipers")

    bun_pct = _bundled_pct(token)
    sn_lv = _sniper_level(token)
    overall = str(bs.get("overall") or "").lower()
    sn = (bs.get("snipers") or {}) if isinstance(bs, dict) else {}
    max_w = _f(sn.get("max_wallet_pct"))

    # Hard wall: critical risk or SETUP band breached (6–8% ok as SETUP, not SNIPE)
    if overall == "critical" or sn_lv == "critical":
        return bs.get("summary") or f"sniper/bundle critical"
    if bun_pct is not None and bun_pct > MAX_BUNDLED_PCT_SETUP:
        return (
            f"bundled {bun_pct:.0f}% > {MAX_BUNDLED_PCT_SETUP:.0f}% "
            "(even SETUP cap)"
        )
    if max_w > MAX_SNIPER_WALLET_SETUP:
        return f"max wallet {max_w:.1f}% — sniper bag"
    # "high" overall / hard_reject from analyzer is soft for SETUP zone when
    # still under 8% bundled — evaluate_snipe will block SNIPE grade.
    if bs.get("hard_reject") and (
        (bun_pct is not None and bun_pct > MAX_BUNDLED_PCT_SETUP)
        or sn_lv == "critical"
        or overall == "critical"
    ):
        return bs.get("summary") or "bundle/sniper hard reject"

    return None


def evaluate_snipe(token: dict[str, Any]) -> dict[str, Any]:
    reason = snipe_reject_reason(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    age = _f(token.get("age_minutes"))
    bond = _f(token.get("bonding_progress"))
    target = round(mcap * TARGET_MULT, 0) if mcap > 0 else None
    ath_ret = round(100 * mcap / ath, 1) if ath > 0 and mcap > 0 else None

    if reason:
        return {
            "eligible": False,
            "reject": reason,
            "snipe_score": 0,
            "label": LABEL_SKIP,
            "confidence": 0,
            "target_2x_usd": target,
            "invalidation_usd": round(mcap * 0.72, 0) if mcap else None,
            "ath_retention_pct": ath_ret,
            "why": [reason],
            "plan": None,
        }

    # Score pillars for snipe quality
    score = 40
    why: list[str] = []

    # Sweet 2× entry (~$5–10k)
    if 5_000 <= mcap <= 10_000:
        score += 18
        why.append(f"Sweet 2× entry ${mcap:,.0f} → ${target:,.0f}")
    elif 3_500 <= mcap < 5_000:
        score += 12
        why.append(f"Early band ${mcap:,.0f} — 2× ${target:,.0f}")
    else:
        score += 8
        why.append(f"2× target ${target:,.0f}")

    if ath_ret is not None:
        if ath_ret >= 92:
            score += 16
            why.append(f"Near ATH ({ath_ret}%)")
        elif ath_ret >= 80:
            score += 10
            why.append(f"Holding ATH zone ({ath_ret}%)")
        else:
            score += 4

    if 2.5 <= age <= 25:
        score += 12
        why.append(f"Survived snipers · {age:.0f}m old")
    elif age <= 45:
        score += 6

    if 15 <= bond <= 55:
        score += 8
        why.append(f"Bonding {bond:.0f}% — room to climb")
    elif bond < 15:
        score += 3

    bun = _bundled_pct(token)
    if bun is not None and bun < 3:
        score += 10
        why.append(f"Clean launch · bundled {bun:.0f}%")
    elif bun is not None and bun <= MAX_BUNDLED_PCT:
        score += 5
        why.append(f"Bundled {bun:.0f}% (under SNIPE cap)")
    elif bun is not None and bun <= MAX_BUNDLED_PCT_SETUP:
        score -= 6
        why.append(
            f"Bundled {bun:.0f}% — SETUP only (≤{MAX_BUNDLED_PCT_SETUP:.0f}%)"
        )

    sn_lv = _sniper_level(token)
    if sn_lv in ("clean", "low", "unknown"):
        score += 8
    elif sn_lv == "medium":
        score -= 8
        why.append("Medium sniper book — size small")
    elif sn_lv == "high":
        score -= 14
        why.append("High sniper book — SETUP only, tiny size")

    social = token.get("socialSignals") or {}
    if social.get("influencer_tweet") or social.get("has_edge"):
        score += 10
        why.append(social.get("summary") or "Narrative edge bonus")

    pc = token.get("priceChange") or (token.get("market") or {}).get("priceChange") or {}
    m5 = _f(pc.get("m5"))
    if 2 <= m5 <= 40:
        score += 6
        why.append(f"m5 +{m5:.0f}% climb")
    elif m5 > 55:
        score -= 6
        why.append("Parabolic m5 — late for snipe")

    score = max(0, min(99, score))
    conf = score
    holders_known = bool(
        (token.get("bundleSniper") or {}).get("holders_known")
        or (token.get("safety") or {}).get("top_holders")
    )
    if not holders_known:
        score -= 10
        why.append("Holder book unknown — SETUP only")
    clean_book = sn_lv in ("clean", "low", "unknown") and (
        bun is None or bun <= 4
    )
    setup_book_ok = sn_lv != "critical" and (
        bun is None or bun <= MAX_BUNDLED_PCT_SETUP
    )
    if (
        score >= 72
        and clean_book
        and holders_known
        and (bun is None or bun <= MAX_BUNDLED_PCT)
    ):
        label = LABEL_SNIPE
        conf = max(conf, 70)
    elif score >= 52 and setup_book_ok:
        label = LABEL_SETUP
        conf = min(conf, 68)
        if bun is not None and bun > MAX_BUNDLED_PCT:
            conf = min(conf, 58)
        if not holders_known:
            conf = min(conf, 55)
    else:
        label = LABEL_SKIP
        conf = min(conf, 45)

    plan = {
        "entry_usd": round(mcap, 0),
        "take_profit_2x_usd": target,
        "invalidation_usd": round(mcap * 0.72, 0),
        "size_advice": (
            "Small size only. Exit at ~2× or if price loses 28% from entry / sharp m5 dump."
        ),
        "rule": "2× target — not a moon hold. Book gains; do not re-chase.",
    }

    return {
        "eligible": label != LABEL_SKIP,
        "reject": None if label != LABEL_SKIP else "score too low for safe snipe",
        "snipe_score": score,
        "label": label,
        "confidence": conf,
        "target_2x_usd": target,
        "invalidation_usd": plan["invalidation_usd"],
        "ath_retention_pct": ath_ret,
        "why": why[:6] or ["Passed safe-snipe filters"],
        "plan": plan,
        "bundle_pct": bun,
        "sniper_level": sn_lv,
    }


def filter_and_rank_snipes(
    tokens: list[dict[str, Any]],
    *,
    min_score: int = 55,
    limit: int = 12,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        ev = evaluate_snipe(t)
        if not ev.get("eligible"):
            continue
        if int(ev.get("snipe_score") or 0) < min_score:
            continue
        row = dict(t)
        row["snipe"] = ev
        row["snipe_score"] = ev["snipe_score"]
        row["snipe_label"] = ev["label"]
        row["confidence"] = ev["confidence"]
        row["target_2x_usd"] = ev.get("target_2x_usd")
        row["ath_retention_pct"] = ev.get("ath_retention_pct")
        out.append(row)

    out.sort(
        key=lambda x: (
            0 if x.get("snipe_label") == LABEL_SNIPE else 1,
            -(x.get("snipe_score") or 0),
            -(x.get("confidence") or 0),
            abs((x.get("mcap_usd") or 0) - 7_000),  # prefer ~$7k sweet
        )
    )
    return out[:limit]


def snipe_card_from_coin(coin: dict, *, source: str = "pump.fun") -> dict[str, Any] | None:
    """Pre-enrich card for snipe scan (softer than moon narrative gate)."""
    from config import PADRE_TRADE_URL
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
    if mcap < SNIPE_MCAP_MIN * 0.85 or mcap > SNIPE_MCAP_MAX * 1.15:
        return None
    if age < 0.8 or age > MAX_AGE_MIN + 15:
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
    if avoid.get("avoid") and set(avoid.get("flags") or []) & {
        "blocklist",
        "banned",
        "honeypot",
        "rugged",
        "flash_pump_dump",
        "drained_curve",
    }:
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
    }
