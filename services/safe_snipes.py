"""Safe snipes for ~2× take-profit setups.

Capital-protection first: clean book, no dump, entry band where 2× is
reachable before migration.

Social policy (bottom line):
  - Social-optional: X / TG / website are not required.
  - Social-honest: spoofed socials hard-reject (status X, media-as-site);
    a real website does not save status-link X + empty description.
  - Real socials are mild boosts only; silence demotes only with red flags.
"""

from __future__ import annotations

from typing import Any

from config import MONEY_ENTRY_MIN_USD, SURVIVAL_MCAP_USD
from services.accuracy import holders_known, learning_soft_adjust, merge_ath_into_token
from services.avoid_filters import BLOCKED_MINTS, analyze_avoid_flags, is_hard_avoid
from services.bundle_sniper import analyze_bundle_and_snipers
from services.moon_picks import extract_ath_mcap, extract_mcap_usd
from services.runner_radar import is_crashed_runner
from services.snipe_social import analyze_snipe_social
from services.social_signals import analyze_social_narrative

# Entry zone: past survival floor (~$7k) so we don't snipe lottery dumps.
# Cap still allows 2× before full migration band.
SNIPE_MCAP_MIN = float(MONEY_ENTRY_MIN_USD or SURVIVAL_MCAP_USD or 7_000)
SNIPE_MCAP_MAX = 22_000.0  # climb band; 2× still under ~grad soft
TARGET_MULT = 2.0
GRAD_SOFT_CAP = 55_000.0  # 2× should not need moonshot migration

MIN_AGE_MIN = 4.0  # past sniper flash window
MAX_AGE_MIN = 120.0  # allow slower organic climbers
MIN_ATH_RETENTION = 0.85  # tighter than dump wall — local tops still dump
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
    """Hard rejects for safe 2× snipes (social-optional, social-honest)."""
    mint = (token.get("tokenAddress") or token.get("mint") or "").strip()
    if mint in BLOCKED_MINTS:
        return "blocklisted mint"
    if token.get("skipped"):
        return token.get("skipReason") or "skipped"

    merge_ath_into_token(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    age = _f(token.get("age_minutes"))

    if mcap < SNIPE_MCAP_MIN:
        return f"mcap ${mcap:,.0f} below 2× entry band (${SNIPE_MCAP_MIN:,.0f}+)"
    if mcap > SNIPE_MCAP_MAX:
        return f"mcap ${mcap:,.0f} above 2× entry band (max ${SNIPE_MCAP_MAX:,.0f})"
    if age < MIN_AGE_MIN:
        return f"too fresh {age:.1f}m — sniper window (need {MIN_AGE_MIN:.0f}m+)"
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

    # One-way wash under climb = dumps before next leg
    mkt = token.get("market") or {}
    txns = (mkt.get("txns") or {}).get("m5") or {}
    buys = int(txns.get("buys") or 0)
    sells = int(txns.get("sells") or 0)
    if buys >= 12 and sells == 0 and mcap < 18_000:
        return "one-way wash buys — not a durable snipe"
    if buys >= 8 and sells >= 1 and buys / max(sells, 1) > 8 and mcap < 12_000:
        return "extreme buy skew — wash risk"

    hard, hard_why = is_hard_avoid(token)
    if hard:
        return hard_why or "hard avoid"

    # Social-honest gate (missing socials OK; spoofed socials fatal)
    social_h = analyze_snipe_social(token)
    token["snipeSocial"] = social_h
    if social_h.get("hard_reject"):
        return str(social_h["hard_reject"])

    # After enrich pipeline: enrich_ok must be True. Pre-enrich cards omit the key.
    if "enrich_ok" in token and token.get("enrich_ok") is not True:
        errs = token.get("enrich_errors") or []
        return "safety unknown — " + (
            ", ".join(str(e) for e in errs[:2]) if errs else "enrich incomplete"
        )

    safety = token.get("safety") or {}
    if safety.get("error") or safety.get("is_honeypot") or safety.get("rugged") or safety.get("honeypot"):
        if safety.get("error"):
            return "safety error / incomplete audit"
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

    # Bundle / snipers — match moon capital wall (hard_reject is always fatal)
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

    # Always honor analyzer hard_reject / critical (no SETUP escape hatch)
    if bs.get("hard_reject"):
        return bs.get("summary") or "bundle/sniper hard reject"
    if overall == "critical" or sn_lv == "critical":
        return bs.get("summary") or "sniper/bundle critical"
    if overall == "high" or sn_lv == "high":
        # High book risk is not "safe" — block display entirely
        return bs.get("summary") or f"sniper/bundle high risk ({sn_lv or overall})"
    if bun_pct is not None and bun_pct > MAX_BUNDLED_PCT_SETUP:
        return (
            f"bundled {bun_pct:.0f}% > {MAX_BUNDLED_PCT_SETUP:.0f}% "
            "(even SETUP cap)"
        )
    if max_w > MAX_SNIPER_WALLET_SETUP:
        return f"max wallet {max_w:.1f}% — sniper bag"

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

    # Prefer proved survival + climb (not pre-$7k lottery)
    if 8_000 <= mcap <= 14_000:
        score += 18
        why.append(f"Sweet 2× climb ${mcap:,.0f} → ${target:,.0f}")
    elif SNIPE_MCAP_MIN <= mcap < 8_000:
        score += 10
        why.append(f"Survival band ${mcap:,.0f} — 2× ${target:,.0f}")
    elif 14_000 < mcap <= 22_000:
        score += 14
        why.append(f"Climb band ${mcap:,.0f} — 2× ${target:,.0f}")
    else:
        score += 6
        why.append(f"2× target ${target:,.0f}")

    if ath_ret is not None:
        if ath_ret >= 92:
            score += 16
            why.append(f"Near ATH ({ath_ret}%)")
        elif ath_ret >= 85:
            score += 10
            why.append(f"Holding ATH zone ({ath_ret}%)")
        else:
            score += 2

    if 5 <= age <= 40:
        score += 12
        why.append(f"Survived snipers · {age:.0f}m old")
    elif age <= 75:
        score += 6
    elif age <= 120:
        score += 3

    if 18 <= bond <= 55:
        score += 12
        why.append(f"Bonding {bond:.0f}% — migration room")
    elif 12 <= bond < 18:
        score += 6
        why.append(f"Bonding {bond:.0f}% — early climb")
    elif bond < 12:
        score += 1  # deep early still high fail

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

    # Social-optional / social-honest: boost real links; silence demote only w/ red flags
    snipe_soc = token.get("snipeSocial")
    if not isinstance(snipe_soc, dict):
        snipe_soc = analyze_snipe_social(token)
        token["snipeSocial"] = snipe_soc
    if snipe_soc.get("hard_reject"):
        # Should already be rejected; keep score floor for safety
        score = 0
        why.append(str(snipe_soc["hard_reject"]))
    else:
        delta = int(snipe_soc.get("score_delta") or 0)
        score += delta
        for note in (snipe_soc.get("why") or [])[:3]:
            if note and note not in why:
                why.append(note)

    # Unique ticker = mild snipe signal; reused copycat = demote
    try:
        from services.ticker_registry import attach_ticker_uniqueness

        tu = token.get("tickerUniqueness")
        if not isinstance(tu, dict):
            tu = attach_ticker_uniqueness(token, record=True)
        if tu.get("unique"):
            score += 8
            why.append(tu.get("summary") or "Unique ticker")
        elif int(tu.get("prior_mints") or 0) >= 2:
            score -= 8
            why.append(tu.get("summary") or "Reused ticker")
        elif tu.get("is_hot_meta") and int(tu.get("prior_mints") or 0) >= 1:
            score -= 6
            why.append("Hot ticker reuse — copycat risk")
    except Exception:
        pass

    # Organic fee trail mild boost; flash/wash demote
    try:
        from services.fee_flow import attach_fee_flow, fee_flow_gate

        ff = token.get("feeFlow")
        if not isinstance(ff, dict):
            ff = attach_fee_flow(token)
        ok_f, why_f = fee_flow_gate(ff)
        if not ok_f:
            score -= 20
            why.append(why_f or "Flash fee war")
        elif ff.get("quality") == "organic":
            score += 8
            why.append(ff.get("summary") or "Organic fee/volume trail")
        elif ff.get("quality") == "wash":
            score -= 10
            why.append("Wash fee trail")
    except Exception:
        pass

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
    hk = holders_known(token)
    if not hk:
        score -= 12
        why.append("Holder book unknown — not SNIPE grade")
        # unknown sniper level without holders is not "clean"
        if sn_lv in ("clean", "low", "unknown"):
            sn_lv = "unknown"
    # With holders known: treat missing bundled_pct as unknown (not auto-clean).
    # With holders: allow SNIPE when known bundled_pct is low OR analyzer overall clean.
    clean_book = hk and sn_lv in ("clean", "low") and (bun is None or bun <= 4)
    setup_book_ok = (
        hk
        and sn_lv not in ("critical", "high")
        and (bun is None or bun <= MAX_BUNDLED_PCT_SETUP)
    )
    if (
        score >= 72
        and clean_book
        and hk
        and (bun is None or bun <= MAX_BUNDLED_PCT)
    ):
        label = LABEL_SNIPE
        conf = max(conf, 70)
    elif score >= 52 and setup_book_ok:
        label = LABEL_SETUP
        conf = min(conf, 68)
        if bun is not None and bun > MAX_BUNDLED_PCT:
            conf = min(conf, 58)
        if not hk:
            conf = min(conf, 50)
    else:
        label = LABEL_SKIP
        conf = min(conf, 45)

    # Learning soft blend (demote toxic feature patterns)
    score, conf, learn_meta = learning_soft_adjust(token, score, conf)
    if learn_meta.get("applied") and score < 52:
        label = LABEL_SKIP
        conf = min(conf, 45)
    elif label == LABEL_SNIPE and not hk:
        label = LABEL_SETUP if setup_book_ok else LABEL_SKIP

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
        "holders_known": hk,
        "learning_soft": learn_meta if learn_meta.get("applied") else None,
        "why": why[:8] or ["Passed safe-snipe filters"],
        "plan": plan,
        "bundle_pct": bun,
        "sniper_level": sn_lv,
        "snipe_social": {
            "policy": (snipe_soc or {}).get("policy") or "social-optional, social-honest",
            "honest": (snipe_soc or {}).get("honest", True),
            "has_any_social": (snipe_soc or {}).get("has_any_social"),
            "has_real_social": (snipe_soc or {}).get("has_real_social"),
            "flags": (snipe_soc or {}).get("flags") or [],
            "score_delta": int((snipe_soc or {}).get("score_delta") or 0),
        },
    }


def filter_and_rank_snipes(
    tokens: list[dict[str, Any]],
    *,
    min_score: int = 55,
    limit: int = 12,
    require_holders: bool = True,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        ev = evaluate_snipe(t)
        if not ev.get("eligible"):
            continue
        if require_holders and not holders_known(t) and not ev.get("holders_known"):
            continue
        if int(ev.get("snipe_score") or 0) < min_score:
            continue
        ls = ev.get("learning_soft") or {}
        if ls.get("applied") and str(ls.get("action") or "").upper() == "SKIP":
            if float(ls.get("p_good") or 1) < 0.28:
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
            abs((x.get("mcap_usd") or 0) - 10_000),  # prefer ~$10k climb sweet
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
    # Align pre-age with hard MIN_AGE_MIN so we don't waste enrich on sniper window
    if age < MIN_AGE_MIN or age > MAX_AGE_MIN + 15:
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
    # Same hard-avoid early drop as moon (full HARD_AVOID_FLAGS / hard_avoid)
    hard, _ = is_hard_avoid({"avoid": avoid})
    if hard or avoid.get("hard_avoid"):
        return None
    # Pre-build card shell for social-honesty gate (no enrich yet)
    pre = {
        "tokenAddress": mint,
        "pumpfun": {
            "twitter": coin.get("twitter"),
            "telegram": coin.get("telegram"),
            "website": coin.get("website"),
            "description": coin.get("description"),
            "reply_count": coin.get("reply_count", 0),
        },
        "avoid": avoid,
    }
    snipe_soc = analyze_snipe_social(pre)
    if snipe_soc.get("hard_reject"):
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
        "snipeSocial": snipe_soc,
        "pump_url": f"https://pump.fun/coin/{mint}",
        "padre_url": f"{PADRE_TRADE_URL}/trade/solana/{mint}",
        "source": source,
    }
