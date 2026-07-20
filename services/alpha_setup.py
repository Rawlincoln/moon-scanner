"""Detect early "moon setup" profiles like FUY6…KIWI (Addiction Bird).

Winner traits (from FUY6RbdfrDfa82y1AS5ZQRtaoSr1ZVTGD2EkN11bpump):
  - Viral content (TikTok / YouTube) in description
  - Real project website (not random tweet / urbandictionary)
  - Own Twitter account (not a single status link)
  - Healthy holder distribution (many mid-size bags, no whale dump)
  - No insiders / clean authorities
  - Curve still has exit SOL (not drained)
  - Buy pressure + climbing mcap while still early

Goal: surface these EARLY (sub-$12k, ideally $2k–$8k) for max profit.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from config import SCAN_MCAP_FOCUS_MAX_USD, SCAN_MCAP_MAX_USD, TARGET_MCAP_USD
from services.mega_fingerprint import analyze_mega_fingerprint

# Known launch communities that produced runners
KNOWN_LAUNCH_DISCORD = (
    "discord.gg/uxento",
    "uxento",
)

VIRAL_HOSTS = (
    "tiktok.com",
    "vm.tiktok.com",
    "youtube.com",
    "youtu.be",
    "instagram.com",
)

FAKE_SITE_HOSTS = (
    "x.com",
    "twitter.com",
    "t.me",
    "telegram.me",
    "urbandictionary.com",
    "pump.fun",
    "j7tracker.io",
    "instagram.com",  # reels are NOT project sites (Baby Corn scam)
    "tiktok.com",  # OK in description; NOT as "website" field
    "vm.tiktok.com",
    "youtube.com",
    "youtu.be",
)

_POOL_PCT = 40.0
_MID_MIN = 0.4
_MID_MAX = 8.0


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return 0.0


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def _is_own_twitter(url: str) -> bool:
    """x.com/ProjectName  ✓   x.com/random/status/123  ✗"""
    if not url:
        return False
    u = url.lower().strip()
    if "status/" in u or "/i/status" in u:
        return False
    return bool(re.search(r"(?:x|twitter)\.com/[A-Za-z0-9_]{2,30}/?$", u.rstrip("/")))


def _is_real_website(url: str) -> bool:
    if not url:
        return False
    h = _host(url)
    if not h or any(h == f or h.endswith("." + f) for f in FAKE_SITE_HOSTS):
        return False
    return True


def _has_viral_content(pump: dict, description: str) -> list[str]:
    hits: list[str] = []
    blob = " ".join(
        [
            description or "",
            str(pump.get("twitter") or ""),
            str(pump.get("website") or ""),
            str(pump.get("telegram") or ""),
        ]
    ).lower()
    for host in VIRAL_HOSTS:
        if host in blob:
            hits.append(host.split(".")[0])
    return hits


def analyze_alpha_setup(
    safety: dict | None = None,
    pair: dict | None = None,
    pump: dict | None = None,
    social: dict | None = None,
    smart_money: dict | None = None,
    mcap_usd: float = 0.0,
) -> dict[str, Any]:
    """Score early moon-setup potential (0–100) and recommendation band."""
    safety = safety or {}
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    social = social or {}
    smart_money = smart_money or {}

    if safety.get("avoid", {}).get("avoid") or safety.get("rugged") or safety.get(
        "is_honeypot"
    ):
        return {
            "is_alpha": False,
            "highlight": False,
            "score": 0,
            "tier": "SKIP",
            "signal": "SKIP",
            "summary": "Fails avoid/safety — not an alpha setup",
            "reasons": [],
            "badges": [],
            "entry_window": "none",
            "confidence": 0,
        }

    mcap = mcap_usd or _safe_float(
        pump.get("usd_market_cap") or pair.get("marketCap") or pair.get("fdv")
    )
    desc = (pump.get("description") or "").strip()
    twitter = str(pump.get("twitter") or "")
    website = str(pump.get("website") or "")
    replies = int(pump.get("reply_count") or 0)
    on_curve = bool(
        safety.get("on_bonding_curve") or (pump and not pump.get("complete", True))
    )
    quote_sol = _safe_float(safety.get("lp_quote_sol"))
    holders = int(safety.get("total_holders") or 0)

    # Age
    age_min = None
    created = pair.get("pairCreatedAt") or pump.get("created_timestamp")
    if created:
        import time

        age_min = (time.time() * 1000 - float(created)) / 60_000

    txns = pair.get("txns") or {}
    m5 = txns.get("m5") or {}
    h1 = txns.get("h1") or {}
    buys_m5 = int(m5.get("buys") or 0)
    sells_m5 = int(m5.get("sells") or 0)
    buys_h1 = int(h1.get("buys") or 0)
    sells_h1 = int(h1.get("sells") or 0)
    buy_ratio = buys_m5 / max(sells_m5, 1)
    vol_m5 = _safe_float((pair.get("volume") or {}).get("m5"))
    pc_m5 = _safe_float((pair.get("priceChange") or {}).get("m5"))

    # Holder distribution (ex-pool)
    mid_bags: list[float] = []
    max_non_pool = 0.0
    for h in safety.get("top_holders") or []:
        pct = _safe_float(h.get("pct"))
        if pct >= _POOL_PCT:
            continue
        if h.get("insider"):
            continue
        max_non_pool = max(max_non_pool, pct)
        if _MID_MIN <= pct <= _MID_MAX:
            mid_bags.append(pct)

    score = 0
    reasons: list[str] = []
    badges: list[dict[str, str]] = []

    # --- Narrative / viral (KIWI-style) ---
    viral = _has_viral_content(pump, desc)
    if viral:
        score += 22
        reasons.append(f"Viral content: {', '.join(viral)}")
        badges.append({"id": "viral", "label": "Viral (TikTok/YT)", "type": "viral"})

    if _is_own_twitter(twitter):
        score += 12
        reasons.append("Own Twitter account (not a random status link)")
        badges.append({"id": "twitter", "label": "Own X account", "type": "social"})
    elif twitter and "status/" in twitter.lower():
        score -= 18  # Baby Corn-style social spoof
        reasons.append("Twitter is a status link only — scam packaging risk")

    if _is_real_website(website):
        score += 14
        reasons.append(f"Real website: {_host(website)}")
        badges.append({"id": "website", "label": "Real website", "type": "social"})
    elif website:
        score -= 12
        reasons.append(
            f"Website is media link ({_host(website)}) — not a real project site"
        )

    # Flash P&D already in progress — never treat as alpha
    ath = _safe_float(pump.get("ath_market_cap"))
    if ath >= 5000 and mcap > 0 and mcap < ath * 0.5:
        score -= 30
        reasons.append(
            f"Already −{(1 - mcap / ath) * 100:.0f}% from ATH ${ath:,.0f} — late/scam exit"
        )

    # Adult-bait names — never moon setups
    name_blob = f"{pump.get('name') or ''} {pump.get('symbol') or ''}".lower()
    if any(
        k in name_blob
        for k in ("sex", "porn", "nude", "onlyfans", "xxx", "nsfw", "milf", "hentai")
    ):
        score -= 40
        reasons.append("Adult-bait name — skip (attention rug pattern)")

    # Empty description + status twitter = entry trap even if mcap looks good
    if twitter and "status/" in twitter.lower() and len(desc) < 8:
        score -= 25
        reasons.append("Entry trap socials (tweet link + no description)")

    # Long AI pitch with no socials (CUBEMAN) — never a mega
    if len(desc) >= 120 and not twitter and not website and not pump.get("telegram"):
        score -= 35
        reasons.append("Long pitch + zero socials — marketing shell / wash setup")

    # Launch community (uxento etc.)
    blob = f"{desc} {twitter} {website}".lower()
    if any(k in blob for k in KNOWN_LAUNCH_DISCORD):
        score += 10
        reasons.append("Known launch community (e.g. uxento)")
        badges.append({"id": "launch_comm", "label": "Launch community", "type": "narrative"})

    # TikTok specifically in description = Addiction Bird pattern
    if "tiktok.com" in desc.lower():
        score += 6
        reasons.append("TikTok link in description — narrative fuel")

    # Charity / pump special flags
    if pump.get("is_charity"):
        score += 4
        reasons.append("pump.fun charity flag")

    # --- Distribution health ---
    if len(mid_bags) >= 5:
        score += 18
        reasons.append(f"{len(mid_bags)} mid-size holders (healthy distribution)")
        badges.append(
            {"id": "distributed", "label": f"{len(mid_bags)} mid bags", "type": "holders"}
        )
    elif len(mid_bags) >= 3:
        score += 12
        reasons.append(f"{len(mid_bags)} mid-size holders forming")
        badges.append(
            {"id": "distributed", "label": f"{len(mid_bags)} mid bags", "type": "holders"}
        )
    elif len(mid_bags) >= 1:
        score += 5

    if holders >= 100:
        score += 10
        reasons.append(f"{holders} holders — real crowd")
    elif holders >= 40:
        score += 6
        reasons.append(f"{holders} holders")
    elif holders >= 15:
        score += 3

    if 0 < max_non_pool <= 12:
        score += 8
        reasons.append(f"Max wallet {max_non_pool:.1f}% — not a sniper bag")
    elif max_non_pool > 20:
        score -= 12
        reasons.append(f"Max wallet {max_non_pool:.1f}% — sniper risk")

    if not safety.get("insider_detected"):
        score += 6
    else:
        score -= 20

    if not safety.get("mint_authority") and not safety.get("freeze_authority"):
        score += 5

    # --- Curve / liquidity health ---
    if on_curve:
        if quote_sol >= 5:
            score += 12
            reasons.append(f"Curve healthy — {quote_sol:.1f} SOL exit liquidity")
            badges.append(
                {"id": "curve_sol", "label": f"{quote_sol:.0f} SOL curve", "type": "liq"}
            )
        elif quote_sol >= 2:
            score += 7
            reasons.append(f"Curve OK — {quote_sol:.1f} SOL left")
        elif quote_sol > 0 and quote_sol < 0.5:
            score -= 25
            reasons.append(f"Curve nearly dead ({quote_sol:.2f} SOL)")
    else:
        # Graduated — still interesting if LP locked + liquid
        if _safe_float(safety.get("lp_locked_pct")) >= 95:
            score += 6
        if _safe_float(safety.get("lp_quote_sol")) >= 20:
            score += 8
            reasons.append("Graduated with deep SOL liquidity")

    # --- Momentum (require two-way market — "all green no sellers" is a trap) ---
    real_two_way = buys_m5 >= 8 and sells_m5 >= 3 and 1.05 <= buy_ratio <= 3.2
    if buys_m5 >= 10 and sells_m5 == 0:
        score -= 30
        reasons.append("All buys / zero sells — wash or no exit (not a moon)")
    elif buy_ratio >= 4.0 and buys_m5 >= 20:
        score -= 20
        reasons.append(f"One-way buys {buy_ratio:.1f}x — wash risk, not organic moon")
    elif real_two_way:
        score += 16
        reasons.append(
            f"Real two-way market {buy_ratio:.1f}x · {buys_m5}B/{sells_m5}S — organic flow"
        )
        badges.append({"id": "buys", "label": f"{buys_m5}B/{sells_m5}S", "type": "momentum"})
    elif buy_ratio >= 1.15 and buys_m5 >= 5 and sells_m5 >= 1:
        score += 8
        reasons.append(f"Positive flow {buy_ratio:.1f}x with some sells")

    if vol_m5 >= 5000 and real_two_way:
        score += 10
        reasons.append(f"Strong organic vol ${vol_m5:,.0f}/5m")
    elif vol_m5 >= 2000 and sells_m5 >= 2:
        score += 6
        reasons.append(f"5m volume ${vol_m5:,.0f}")
    elif vol_m5 >= 500:
        score += 2

    if pc_m5 >= 5 and real_two_way:
        score += 6
        reasons.append(f"Climbing +{pc_m5:.0f}% (5m)")
    elif pc_m5 <= -40:
        score -= 10

    # Smart money boost
    if smart_money.get("anti_rug_signal"):
        score += 8
        reasons.append(f"Whale/major: {smart_money.get('signal')}")

    if social.get("highlight"):
        score += 5
        reasons.append("Social narrative highlight")

    if replies >= 5:
        score += 5
        reasons.append(f"{replies} pump.fun replies")
    elif replies >= 1:
        score += 2

    # --- Entry window (profit max when early) ---
    entry_window = "late"
    if mcap <= 0:
        entry_window = "unknown"
    elif mcap <= 3500:
        entry_window = "ultra_early"
        score += 10
        reasons.append(f"Ultra early mcap ${mcap:,.0f}")
    elif mcap <= TARGET_MCAP_USD:
        entry_window = "early"
        score += 14
        reasons.append(f"Early window ${mcap:,.0f} (pre-${TARGET_MCAP_USD//1000}k)")
        badges.append({"id": "early", "label": "Early entry", "type": "timing"})
    elif mcap <= SCAN_MCAP_FOCUS_MAX_USD:
        entry_window = "sweet"
        score += 8
        reasons.append(f"Sweet zone ${mcap:,.0f} (approaching/just past $6k)")
        badges.append({"id": "sweet", "label": "Sweet zone", "type": "timing"})
    elif mcap <= SCAN_MCAP_MAX_USD:
        entry_window = "extended"
        score += 2
        reasons.append(f"Still under ${SCAN_MCAP_MAX_USD//1000}k — late-early")
    else:
        entry_window = "late"
        score -= 15
        reasons.append(f"MCap ${mcap:,.0f} — late for max profit")

    if age_min is not None:
        if 3 <= age_min <= 25:
            score += 6
            reasons.append(f"Age {age_min:.0f}m — survived snipers, still early")
        elif age_min < 2:
            score -= 6  # flash pumps die; real moons need a few minutes
            reasons.append(f"Age {age_min:.1f}m — too fresh for mega conviction")
        elif age_min > 90:
            score -= 8

    # --- Mega-moon structural stack (learned: big ATH had deep SOL + mid bags + holders) ---
    # Historical big ATH (≥$25k) avg: quote_sol~18, mid_bags~4, holders~54
    deep_curve = (on_curve and quote_sol >= 10) or (
        not on_curve and quote_sol >= 40
    )
    solid_dist = len(mid_bags) >= 5 and holders >= 40 and 0 < max_non_pool <= 15
    clean_social = (
        bool(viral)
        or _is_own_twitter(twitter)
        or _is_real_website(website)
    ) and not (twitter and "status/" in twitter.lower())
    organic_flow = real_two_way or (
        buys_m5 >= 12 and sells_m5 >= 4 and buy_ratio <= 3.5
    )
    mega_stack = deep_curve and solid_dist and clean_social and organic_flow
    high_stack = (
        (quote_sol >= 6 or (not on_curve and quote_sol >= 25))
        and len(mid_bags) >= 4
        and holders >= 25
        and clean_social
        and sells_m5 >= 2
    )

    if mega_stack:
        score += 18
        reasons.append(
            "MEGA stack: deep SOL + distributed holders + clean social + two-way flow"
        )
        badges.append({"id": "mega", "label": "MEGA structure", "type": "mega"})
    elif high_stack:
        score += 10
        reasons.append("High-ceiling structure forming (SOL + bags + social)")
        badges.append({"id": "high", "label": "High ceiling", "type": "mega"})

    # --- $10M–$100M fingerprint (historical multi‑$M common factors) ---
    fingerprint = analyze_mega_fingerprint(
        safety=safety,
        pair=pair,
        pump=pump,
        social=social,
        mcap_usd=mcap,
        precomputed={
            "own_twitter": _is_own_twitter(twitter),
            "real_website": _is_real_website(website),
            "viral": viral,
            "on_curve": on_curve,
            "quote_sol": quote_sol,
            "holders": holders,
            "mid_bags": len(mid_bags),
            "max_wallet_pct": max_non_pool,
            "buys_m5": buys_m5,
            "sells_m5": sells_m5,
            "age_min": age_min,
        },
    )
    fp_score = int(fingerprint.get("score") or 0)
    fp_tier = str(fingerprint.get("tier") or "NONE")
    if fp_score >= 65:
        score += 12 if fp_tier == "MEGA_10M" else 8
        reasons.append(fingerprint.get("summary") or f"$10M fingerprint {fp_score}")
        for b in fingerprint.get("badges") or []:
            badges.append(b)
    elif fp_score >= 52:
        score += 4
        reasons.append(f"$10M fingerprint building ({fp_score})")
    for tag in (fingerprint.get("narrative_tags") or [])[:2]:
        reasons.append(f"Narrative tag: {tag.replace('_', ' ')}")

    # Cap score if missing mega ingredients (prevents false 100s that top <$20k)
    if not deep_curve and score > 70:
        score = min(score, 68)
        reasons.append("Capped — need deeper curve SOL for 100k+ moons")
    if not solid_dist and score > 65:
        score = min(score, 62)
        reasons.append("Capped — need more mid-size holders for mega run")

    score = int(max(0, min(100, score)))

    # Ceiling estimate (what this setup can reach if it works)
    if fp_tier == "MEGA_10M" and mega_stack and entry_window in (
        "ultra_early", "early", "sweet"
    ):
        ceiling = "10M_to_100M"
        ceiling_label = "$10M–$100M+"
    elif fp_tier == "HIGH_10M" and (mega_stack or high_stack) and entry_window != "late":
        ceiling = "1M_to_10M"
        ceiling_label = "$1M–$10M path"
    elif mega_stack and score >= 78 and entry_window in (
        "ultra_early", "early", "sweet"
    ):
        ceiling = "100k_to_1M"
        ceiling_label = "$100K–$1M+"
    elif high_stack and score >= 70 and entry_window != "late":
        ceiling = "50k_to_250k"
        ceiling_label = "$50K–$250K"
    elif score >= 62 and entry_window in ("ultra_early", "early", "sweet"):
        ceiling = "20k_to_80k"
        ceiling_label = "$20K–$80K"
    elif score >= 48:
        ceiling = "under_25k"
        ceiling_label = "likely under $25K"
    else:
        ceiling = "low"
        ceiling_label = "low ceiling / skip"

    # Tier — only ENTER on high/mega ceiling (user complaint: only 1–2 hit 50k)
    early_ok = entry_window in ("ultra_early", "early", "sweet")
    if (
        fp_tier == "MEGA_10M"
        and mega_stack
        and score >= 80
        and early_ok
        and on_curve
    ):
        tier = "MEGA_MOON"
        signal = "STRONG_INVEST"
        summary = (
            f"MEGA $10M+ ({score}) — fingerprint matches multi‑$M historicals "
            f"(narrative + deep SOL + distribution + organic flow). "
            f"Ceiling {ceiling_label}. Enter ${mcap:,.0f}."
        )
    elif mega_stack and score >= 78 and early_ok and on_curve:
        tier = "MEGA_MOON"
        signal = "STRONG_INVEST"
        summary = (
            f"MEGA MOON ({score}) — structure matches historical big runners "
            f"(deep SOL + distribution + organic flow). "
            f"Ceiling {ceiling_label}. Enter ${mcap:,.0f}."
        )
    elif high_stack and score >= 72 and early_ok:
        tier = "MOON_SETUP"
        signal = "STRONG_INVEST"
        summary = (
            f"MOON SETUP ({score}) — high ceiling {ceiling_label}. "
            f"Enter early at ${mcap:,.0f}."
        )
    elif score >= 68 and early_ok and solid_dist and quote_sol >= 5:
        tier = "ALPHA"
        signal = "INVEST"
        summary = (
            f"ALPHA ({score}) — solid but not full mega stack. "
            f"Ceiling {ceiling_label}."
        )
    elif score >= 55 and entry_window != "late" and (
        high_stack or fp_tier in ("HIGH_10M", "BUILDING_10M")
    ):
        tier = "WATCH_ALPHA"
        signal = "WATCH"
        summary = (
            f"Building mega ({score}) — wait for more holders/SOL. "
            f"Potential {ceiling_label}."
        )
    elif score >= 48 and entry_window != "late":
        tier = "SPEC"
        signal = "WATCH"
        summary = (
            f"Spec only ({score}) — ceiling {ceiling_label}. "
            "Most of these die under $20k; do not size large."
        )
    else:
        tier = "WEAK"
        signal = "SKIP"
        summary = f"Weak ({score}) — {ceiling_label}. Pass."

    # Only highlight high-conviction moons (not every mediocre alpha)
    is_alpha = tier in ("MEGA_MOON", "MOON_SETUP", "ALPHA")
    is_mega = tier == "MEGA_MOON"
    is_mega_10m = fp_tier in ("MEGA_10M", "HIGH_10M") and is_mega
    confidence = min(92, score + 2) if is_mega_10m else (
        min(90, score) if is_mega else (
            min(82, score - 5) if is_alpha else max(15, score // 2)
        )
    )

    # TP mcap targets — prefer $10M ladder when fingerprint matches
    tp_targets = {
        "tp1_mcap": round(max(mcap * 2.5, 15_000)) if mcap else 15_000,
        "tp2_mcap": round(max(mcap * 8, 50_000)) if mcap else 50_000,
        "tp3_mcap": round(max(mcap * 25, 150_000)) if mcap else 150_000,
        "moon_mcap": 500_000 if is_mega else 100_000,
    }
    fp_ladder = fingerprint.get("tp_ladder") or {}
    if ceiling == "10M_to_100M" and fp_ladder:
        tp_targets.update(
            {
                "tp1_mcap": fp_ladder.get("tp1_mcap", 15_000),
                "tp2_mcap": fp_ladder.get("tp2_mcap", 100_000),
                "tp3_mcap": fp_ladder.get("tp3_mcap", 1_000_000),
                "moon_mcap": fp_ladder.get("moon_mcap", 10_000_000),
                "mega_band_mcap": fp_ladder.get("mega_band_mcap", 100_000_000),
                "sell_pct": fp_ladder.get("sell_pct"),
                "notes": fp_ladder.get("notes"),
            }
        )
    elif ceiling == "1M_to_10M" and fp_ladder:
        tp_targets.update(
            {
                "tp1_mcap": fp_ladder.get("tp1_mcap", 15_000),
                "tp2_mcap": fp_ladder.get("tp2_mcap", 100_000),
                "tp3_mcap": fp_ladder.get("tp3_mcap", 500_000),
                "moon_mcap": fp_ladder.get("moon_mcap", 10_000_000),
                "sell_pct": fp_ladder.get("sell_pct"),
            }
        )
    elif ceiling == "100k_to_1M":
        tp_targets.update(
            {"tp2_mcap": 100_000, "tp3_mcap": 350_000, "moon_mcap": 1_000_000}
        )
    elif ceiling == "50k_to_250k":
        tp_targets.update(
            {"tp2_mcap": 50_000, "tp3_mcap": 150_000, "moon_mcap": 250_000}
        )

    return {
        "is_alpha": is_alpha,
        "is_mega": is_mega,
        "is_mega_10m": is_mega_10m,
        "highlight": is_alpha or tier == "WATCH_ALPHA" or fp_tier in ("MEGA_10M", "HIGH_10M"),
        "score": score,
        "tier": tier,
        "signal": signal,
        "summary": summary,
        "reasons": reasons[:14],
        "badges": badges[:12],
        "entry_window": entry_window,
        "confidence": confidence,
        "ceiling": ceiling,
        "ceiling_label": ceiling_label,
        "mega_stack": mega_stack,
        "high_stack": high_stack,
        "tp_mcap_targets": tp_targets,
        "megaFingerprint": fingerprint,
        "metrics": {
            "mcap": round(mcap),
            "holders": holders,
            "mid_bags": len(mid_bags),
            "max_wallet_pct": round(max_non_pool, 2),
            "quote_sol": round(quote_sol, 3),
            "buys_m5": buys_m5,
            "sells_m5": sells_m5,
            "buy_ratio": round(buy_ratio, 2),
            "real_two_way": real_two_way,
            "viral": viral,
            "own_twitter": _is_own_twitter(twitter),
            "real_website": _is_real_website(website),
            "on_curve": on_curve,
            "age_min": round(age_min, 1) if age_min is not None else None,
            "deep_curve": deep_curve,
            "solid_dist": solid_dist,
            "fingerprint_score": fp_score,
            "fingerprint_tier": fp_tier,
        },
    }
