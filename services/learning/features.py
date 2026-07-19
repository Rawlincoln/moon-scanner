"""Extract learnable feature flags from a full token analysis snapshot."""

from __future__ import annotations

from typing import Any


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _b(cond: Any) -> int:
    return 1 if cond else 0


def extract_features(
    safety: dict | None = None,
    pair: dict | None = None,
    pump: dict | None = None,
    social: dict | None = None,
    smart_money: dict | None = None,
    alpha: dict | None = None,
    avoid: dict | None = None,
    mcap: float = 0.0,
) -> dict[str, int | float | str]:
    """Compact feature vector stored at first sight + each snapshot."""
    safety = safety or {}
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    social = social or {}
    smart_money = smart_money or {}
    alpha = alpha or {}
    avoid = avoid or safety.get("avoid") or {}

    mcap = mcap or _f(pump.get("usd_market_cap") or pair.get("marketCap"))
    holders = int(safety.get("total_holders") or 0)
    replies = int(pump.get("reply_count") or 0)
    creator_pct = _f(safety.get("creator_pct"))
    quote_sol = _f(safety.get("lp_quote_sol"))
    on_curve = _b(
        safety.get("on_bonding_curve") or (pump and not pump.get("complete", True))
    )

    top = safety.get("top_holders") or []
    mid = 0
    max_non_pool = 0.0
    for h in top:
        pct = _f(h.get("pct"))
        if pct >= 40:
            continue
        if h.get("insider"):
            continue
        max_non_pool = max(max_non_pool, pct)
        if 0.4 <= pct <= 8:
            mid += 1

    txns = (pair.get("txns") or {}).get("m5") or {}
    buys = int(txns.get("buys") or 0)
    sells = int(txns.get("sells") or 0)
    buy_ratio = buys / max(sells, 1)
    vol_m5 = _f((pair.get("volume") or {}).get("m5"))
    pc_m5 = _f((pair.get("priceChange") or {}).get("m5"))

    twitter = str(pump.get("twitter") or "")
    website = str(pump.get("website") or "")
    desc = (pump.get("description") or "").lower()
    name_blob = f"{pump.get('name') or ''} {pump.get('symbol') or ''}".lower()

    if mcap <= 0:
        mcap_bin = "unknown"
    elif mcap < 3500:
        mcap_bin = "under_3.5k"
    elif mcap <= 7500:
        mcap_bin = "sweet_3.5_7.5k"
    elif mcap <= 12000:
        mcap_bin = "mid_7.5_12k"
    elif mcap <= 25000:
        mcap_bin = "late_12_25k"
    else:
        mcap_bin = "over_25k"

    alpha_score = int(alpha.get("score") or 0)
    if alpha_score >= 72:
        alpha_bin = "alpha_high"
    elif alpha_score >= 48:
        alpha_bin = "alpha_mid"
    else:
        alpha_bin = "alpha_low"

    flags = set(avoid.get("flags") or [])

    feats: dict[str, int | float | str] = {
        "mcap": round(mcap, 2),
        "mcap_bin": mcap_bin,
        "holders": holders,
        "replies": replies,
        "mid_bags": mid,
        "max_wallet_pct": round(max_non_pool, 2),
        "creator_pct": round(creator_pct, 2),
        "creator_sold": _b(safety.get("creator_sold")),
        "creator_balance": _f(safety.get("creator_balance")),
        "quote_sol": round(quote_sol, 3),
        "on_curve": on_curve,
        "buys_m5": buys,
        "sells_m5": sells,
        "buy_ratio": round(buy_ratio, 3),
        "vol_m5": round(vol_m5, 2),
        "pc_m5": round(pc_m5, 2),
        "rug_score": int(safety.get("rug_score") or 0),
        "insider": _b(safety.get("insider_detected")),
        "mint_auth": _b(safety.get("mint_authority")),
        "freeze_auth": _b(safety.get("freeze_authority")),
        "passed_safety": _b(safety.get("passed")),
        "avoid": _b(avoid.get("avoid")),
        "hard_avoid": _b(avoid.get("hard_avoid")),
        "fake_twitter": _b("fake_twitter" in flags or "status/" in twitter.lower()),
        "fake_website": _b("fake_website" in flags),
        "adult_bait": _b("adult_bait" in flags),
        "has_viral": _b(
            any(x in desc or x in website.lower() for x in ("tiktok.com", "youtube.com", "youtu.be"))
        ),
        "own_twitter": _b(
            twitter
            and "status/" not in twitter.lower()
            and ("x.com/" in twitter.lower() or "twitter.com/" in twitter.lower())
        ),
        "real_website": _b(
            website
            and "status/" not in website.lower()
            and not any(
                h in website.lower()
                for h in (
                    "instagram.com",
                    "tiktok.com",
                    "x.com",
                    "twitter.com",
                    "urbandictionary",
                )
            )
        ),
        "whale_signal": _b(smart_money.get("anti_rug_signal")),
        "alpha_score": alpha_score,
        "alpha_bin": alpha_bin,
        "alpha_tier": str(alpha.get("tier") or "NONE"),
        "social_highlight": _b(social.get("highlight")),
        "sniper_critical": _b(
            (safety.get("avoid") or {}).get("flags") and False  # filled below
        ),
        "name_len": len(str(pump.get("name") or "")),
        "has_desc": _b(len((pump.get("description") or "").strip()) >= 8),
    }

    # Sniper from trench-style heuristic
    feats["sniper_risk"] = (
        "high" if max_non_pool > 22 else "med" if max_non_pool > 12 else "low"
    )
    return feats


def feature_keys_for_learning(feats: dict) -> list[str]:
    """Binary/categorical keys used to update outcome tables."""
    keys: list[str] = []
    for k, v in feats.items():
        if k in (
            "mcap",
            "holders",
            "replies",
            "mid_bags",
            "max_wallet_pct",
            "creator_pct",
            "creator_balance",
            "quote_sol",
            "buys_m5",
            "sells_m5",
            "buy_ratio",
            "vol_m5",
            "pc_m5",
            "rug_score",
            "alpha_score",
            "name_len",
        ):
            continue
        if isinstance(v, (int, float)) and v in (0, 1):
            if v == 1:
                keys.append(k)
        elif isinstance(v, str) and v:
            keys.append(f"{k}:{v}")
    # Binned continuous
    holders = int(feats.get("holders") or 0)
    if holders >= 100:
        keys.append("holders_ge_100")
    elif holders >= 40:
        keys.append("holders_ge_40")
    elif holders >= 15:
        keys.append("holders_ge_15")
    else:
        keys.append("holders_lt_15")

    mid = int(feats.get("mid_bags") or 0)
    if mid >= 5:
        keys.append("mid_bags_ge_5")
    elif mid >= 3:
        keys.append("mid_bags_ge_3")
    else:
        keys.append("mid_bags_lt_3")

    qs = float(feats.get("quote_sol") or 0)
    if qs >= 5:
        keys.append("curve_sol_ge_5")
    elif qs >= 2:
        keys.append("curve_sol_ge_2")
    elif qs > 0 and qs < 0.5:
        keys.append("curve_sol_drained")

    br = float(feats.get("buy_ratio") or 0)
    if br >= 1.3:
        keys.append("buy_ratio_ge_1.3")
    elif br < 0.95 and int(feats.get("sells_m5") or 0) >= 20:
        keys.append("sell_pressure")

    return keys
