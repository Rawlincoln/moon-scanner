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
    migration: dict | None = None,
    runner: dict | None = None,
) -> dict[str, int | float | str]:
    """Compact feature vector stored at first sight + each snapshot."""
    safety = safety or {}
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    social = social or {}
    smart_money = smart_money or {}
    alpha = alpha or {}
    avoid = avoid or safety.get("avoid") or {}
    migration = migration or {}
    runner = runner or {}

    mcap = mcap or _f(pump.get("usd_market_cap") or pair.get("marketCap"))
    holders = int(safety.get("total_holders") or 0)
    replies = int(pump.get("reply_count") or 0)
    creator_pct = _f(safety.get("creator_pct"))
    quote_sol = _f(safety.get("lp_quote_sol"))
    on_curve = _b(
        safety.get("on_bonding_curve") or (pump and not pump.get("complete", True))
    )
    ath = _f(pump.get("ath_market_cap") or pump.get("ath_mcap"))

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
    elif mcap <= 45000:
        mcap_bin = "near_mig_25_45k"
    else:
        mcap_bin = "over_45k"

    # Bonding toward graduation (~$69k)
    bond = _f(migration.get("bonding_pct") or pump.get("bonding_progress"))
    if bond <= 0 and mcap > 0:
        bond = min(100.0, (mcap / 69_000) * 100)
    if bond >= 55:
        bond_bin = "bond_ge_55"
    elif bond >= 40:
        bond_bin = "bond_40_55"
    elif bond >= 18:
        bond_bin = "bond_18_40"
    elif bond >= 8:
        bond_bin = "bond_8_18"
    else:
        bond_bin = "bond_lt_8"

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

    # $10M fingerprint flags (from alpha_setup.megaFingerprint)
    fp = alpha.get("megaFingerprint") or {}
    fp_tier = str(fp.get("tier") or alpha.get("metrics", {}).get("fingerprint_tier") or "NONE")
    fp_check = fp.get("checklist") or {}
    feats["mega_fingerprint"] = fp_tier
    feats["organic_two_way"] = _b(fp_check.get("organic_two_way") or sells >= 3 and buys >= 8)
    feats["clean_social_stack"] = _b(fp_check.get("clean_social") or feats.get("own_twitter"))
    feats["deep_curve_sol"] = _b(fp_check.get("deep_curve") or quote_sol >= 10)
    feats["solid_distribution"] = _b(fp_check.get("solid_dist") or (mid >= 5 and holders >= 40))
    feats["external_narrative"] = _b(
        fp_check.get("external_narrative") or feats.get("has_viral")
    )
    for tag in (fp.get("narrative_tags") or [])[:3]:
        feats[f"narrative_{tag}"] = 1

    # Flow quality — wash vs organic (learned: scams often one-way)
    two_way = sells >= 3 and buys >= 6 and 1.05 <= buy_ratio <= 3.5
    one_way_wash = buys >= 15 and sells == 0
    extreme_wash = buy_ratio >= 6.0 and buys >= 25
    feats["two_way_flow"] = _b(two_way)
    feats["one_way_wash"] = _b(one_way_wash)
    feats["extreme_wash"] = _b(extreme_wash)
    feats["bond_bin"] = bond_bin
    feats["bonding_pct"] = round(bond, 1)
    feats["migration_lane"] = str(migration.get("lane") or "unknown")
    feats["migration_score_bin"] = (
        "mig_high"
        if _f(migration.get("score")) >= 55
        else "mig_mid"
        if _f(migration.get("score")) >= 40
        else "mig_low"
    )
    feats["runner_stage"] = str(runner.get("stage") or "none")
    feats["runner_alert"] = _b(runner.get("alert"))
    # Off-ATH risk at observation time
    if ath >= 5000 and mcap > 0 and mcap < ath * 0.55:
        feats["already_crashed"] = 1
    else:
        feats["already_crashed"] = 0
    if ath >= 8000 and mcap > 0 and mcap < ath * 0.75:
        feats["fading_from_ath"] = 1
    else:
        feats["fading_from_ath"] = 0

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
            "bonding_pct",
        ):
            continue
        if isinstance(v, (int, float)) and v in (0, 1):
            if v == 1:
                keys.append(k)
        elif isinstance(v, str) and v and v not in ("none", "unknown", "NONE"):
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
    if qs >= 15:
        keys.append("curve_sol_ge_15")
    elif qs >= 5:
        keys.append("curve_sol_ge_5")
    elif qs >= 2:
        keys.append("curve_sol_ge_2")
    elif qs > 0 and qs < 0.5:
        keys.append("curve_sol_drained")

    br = float(feats.get("buy_ratio") or 0)
    if br >= 6.0 and int(feats.get("buys_m5") or 0) >= 20:
        keys.append("buy_ratio_extreme")
    elif br >= 1.3:
        keys.append("buy_ratio_ge_1.3")
    elif br < 0.95 and int(feats.get("sells_m5") or 0) >= 20:
        keys.append("sell_pressure")

    if feats.get("two_way_flow"):
        keys.append("two_way_flow")
    if feats.get("one_way_wash"):
        keys.append("one_way_wash")
    if feats.get("extreme_wash"):
        keys.append("extreme_wash")
    if feats.get("already_crashed"):
        keys.append("already_crashed")
    if feats.get("fading_from_ath"):
        keys.append("fading_from_ath")

    # Mega fingerprint categorical
    fp_tier = str(feats.get("mega_fingerprint") or "NONE")
    if fp_tier and fp_tier != "NONE":
        keys.append(f"mega_fingerprint:{fp_tier}")
    for flag in (
        "organic_two_way",
        "clean_social_stack",
        "deep_curve_sol",
        "solid_distribution",
        "external_narrative",
    ):
        if feats.get(flag) and flag not in keys:
            keys.append(flag)
    for k, v in feats.items():
        if k.startswith("narrative_") and v:
            tag = f"narrative:{k.replace('narrative_', '', 1)}"
            if tag not in keys:
                keys.append(tag)
            # drop raw narrative_* binary key if present
            if k in keys:
                keys.remove(k)

    # Drop duplicate mega_fingerprint raw if categorical form exists
    keys = list(dict.fromkeys(keys))
    return keys
