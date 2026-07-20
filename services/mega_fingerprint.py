"""MEGA $10M–$100M fingerprint — identify early setups that rhyme with historical multi‑$M coins.

Based on common factors of WIF/FARTCOIN/PNUT/MOODENG/GOAT/CHILLGUY/ACT/POPCAT-class:
  - Pre-existing / external narrative (viral media, AI lore, culture brand)
  - Own X + real website (not status / IG spoof)
  - Organic two-way flow (not wash "all green")
  - Distributed mid bags + deep curve SOL
  - Early entry window ($3.5k–$12k) with structure complete

Returns a 0–100 score, checklist, narrative tags, and absolute TP ladder.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Narrative keywords that historically scaled to multi‑$M (culture / AI / animal / brand)
NARRATIVE_KEYWORDS: tuple[str, ...] = (
    "tiktok",
    "youtube",
    "viral",
    "agent",
    "ai ",
    " a.i",
    "terminal",
    "hippo",
    "squirrel",
    "chill guy",
    "goat",
    "cat",
    "dog",
    "frog",
    "penguin",
    "community",
    "cto",
    "meme",
    "culture",
    "mascot",
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
    "instagram.com",
    "tiktok.com",
    "vm.tiktok.com",
    "youtube.com",
    "youtu.be",
)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def _is_own_twitter(url: str) -> bool:
    if not url:
        return False
    u = url.lower().strip()
    if "status/" in u or "/i/status" in u or "/i/communities/" in u:
        return False
    return bool(re.search(r"(?:x|twitter)\.com/[A-Za-z0-9_]{2,30}/?$", u.rstrip("/")))


def _is_real_website(url: str) -> bool:
    if not url:
        return False
    h = _host(url)
    if not h or any(h == f or h.endswith("." + f) for f in FAKE_SITE_HOSTS):
        return False
    return True


def _has_viral(blob: str) -> list[str]:
    hits: list[str] = []
    low = (blob or "").lower()
    for host in VIRAL_HOSTS:
        if host in low:
            hits.append(host.split(".")[0])
    return hits


def _narrative_tags(name: str, symbol: str, desc: str, social_blob: str) -> list[str]:
    tags: list[str] = []
    text = f"{name} {symbol} {desc} {social_blob}".lower()
    if any(h in text for h in ("tiktok", "youtube", "youtu.be", "instagram")):
        tags.append("viral_media")
    if any(k in text for k in ("agent", "ai ", "a.i", "terminal", "llm", "gpt")):
        tags.append("ai_agent")
    if any(
        k in text
        for k in (
            "cat",
            "dog",
            "frog",
            "hippo",
            "squirrel",
            "penguin",
            "bird",
            "chill guy",
            "goat",
            "pepe",
            "doge",
        )
    ):
        tags.append("animal_mascot")
    if "community" in text or "cto" in text:
        tags.append("community_cto")
    if any(k in text for k in ("culture", "meme", "mascot", "brand")):
        tags.append("culture_brand")
    # Dedup preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def analyze_mega_fingerprint(
    safety: dict | None = None,
    pair: dict | None = None,
    pump: dict | None = None,
    social: dict | None = None,
    mcap_usd: float = 0.0,
    *,
    precomputed: dict | None = None,
) -> dict[str, Any]:
    """Score how closely a live token matches $10M–$100M historical fingerprint."""
    safety = safety or {}
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    social = social or {}
    pre = precomputed or {}

    if safety.get("avoid", {}).get("avoid") or safety.get("rugged") or safety.get(
        "is_honeypot"
    ):
        return {
            "score": 0,
            "tier": "NONE",
            "match": False,
            "checklist": {},
            "narrative_tags": [],
            "missing": ["hard_avoid"],
            "summary": "Fails avoid — no mega fingerprint",
            "tp_ladder": {},
            "entry_recommendation": "SKIP",
        }

    mcap = mcap_usd or _safe_float(
        pump.get("usd_market_cap") or pair.get("marketCap") or pair.get("fdv")
    )
    desc = (pump.get("description") or "").strip()
    twitter = str(pump.get("twitter") or "")
    website = str(pump.get("website") or "")
    telegram = str(pump.get("telegram") or "")
    name = str(pump.get("name") or "")
    symbol = str(pump.get("symbol") or "")
    social_blob = " ".join(
        [desc, twitter, website, telegram, str(social.get("highlight") or "")]
    )

    own_twitter = bool(pre.get("own_twitter")) if "own_twitter" in pre else _is_own_twitter(twitter)
    real_website = (
        bool(pre.get("real_website")) if "real_website" in pre else _is_real_website(website)
    )
    viral = pre.get("viral") if "viral" in pre else _has_viral(social_blob)
    if isinstance(viral, bool):
        viral_list = ["media"] if viral else []
    else:
        viral_list = list(viral or [])

    on_curve = bool(
        pre.get("on_curve")
        if "on_curve" in pre
        else (safety.get("on_bonding_curve") or (pump and not pump.get("complete", True)))
    )
    quote_sol = _safe_float(pre.get("quote_sol", safety.get("lp_quote_sol")))
    holders = int(pre.get("holders") if "holders" in pre else (safety.get("total_holders") or 0))
    mid_bags = int(pre.get("mid_bags") if "mid_bags" in pre else 0)
    max_non_pool = _safe_float(pre.get("max_wallet_pct"))
    if "mid_bags" not in pre or "max_wallet_pct" not in pre:
        mid_bags = 0
        max_non_pool = 0.0
        for h in safety.get("top_holders") or []:
            pct = _safe_float(h.get("pct"))
            if pct >= 40 or h.get("insider"):
                continue
            max_non_pool = max(max_non_pool, pct)
            if 0.4 <= pct <= 8.0:
                mid_bags += 1

    txns = pair.get("txns") or {}
    m5 = txns.get("m5") or {}
    buys_m5 = int(pre.get("buys_m5") if "buys_m5" in pre else (m5.get("buys") or 0))
    sells_m5 = int(pre.get("sells_m5") if "sells_m5" in pre else (m5.get("sells") or 0))
    buy_ratio = buys_m5 / max(sells_m5, 1)

    age_min = pre.get("age_min")
    if age_min is None:
        created = pair.get("pairCreatedAt") or pump.get("created_timestamp")
        if created:
            import time

            age_min = (time.time() * 1000 - float(created)) / 60_000

    name_blob = f"{name} {symbol}".lower()
    adult_bait = any(
        k in name_blob
        for k in ("sex", "porn", "nude", "onlyfans", "xxx", "nsfw", "milf", "hentai")
    )
    ai_shell = (
        len(desc) >= 120
        and not twitter
        and not website
        and not telegram
    )
    status_only = bool(twitter and "status/" in twitter.lower())
    one_way_wash = buys_m5 >= 15 and sells_m5 == 0
    extreme_wash = buy_ratio >= 8.0 and buys_m5 >= 30

    deep_curve = (on_curve and quote_sol >= 10) or (not on_curve and quote_sol >= 40)
    solid_dist = mid_bags >= 5 and holders >= 40 and 0 < max_non_pool <= 15
    organic_flow = (
        buys_m5 >= 8 and sells_m5 >= 3 and 1.05 <= buy_ratio <= 3.2
    ) or (buys_m5 >= 12 and sells_m5 >= 4 and buy_ratio <= 3.5)
    early_window = 2000 <= mcap <= 12000 if mcap > 0 else False
    sweet_window = 3500 <= mcap <= 7500 if mcap > 0 else False
    age_ok = age_min is None or (3 <= float(age_min) <= 90)
    external_narrative = bool(viral_list) or bool(_narrative_tags(name, symbol, desc, social_blob))
    multi_social = sum([own_twitter, real_website, bool(telegram), bool(viral_list)]) >= 2
    clean_social = (own_twitter or real_website or bool(viral_list)) and not status_only

    narrative_tags = _narrative_tags(name, symbol, desc, social_blob)
    if viral_list and "viral_media" not in narrative_tags:
        narrative_tags.insert(0, "viral_media")

    checklist = {
        "external_narrative": external_narrative,
        "own_twitter": own_twitter,
        "real_website": real_website,
        "multi_social": multi_social,
        "clean_social": clean_social,
        "deep_curve": deep_curve,
        "solid_dist": solid_dist,
        "max_wallet_ok": 0 < max_non_pool <= 15 if max_non_pool else holders >= 25,
        "organic_two_way": organic_flow,
        "early_window": early_window,
        "sweet_window": sweet_window,
        "age_ok": age_ok,
        "not_wash": not one_way_wash and not extreme_wash,
        "not_adult_bait": not adult_bait,
        "not_ai_shell": not ai_shell,
    }

    # Weighted score (max ~100)
    weights = {
        "external_narrative": 14,
        "own_twitter": 10,
        "real_website": 10,
        "multi_social": 8,
        "clean_social": 6,
        "deep_curve": 12,
        "solid_dist": 12,
        "max_wallet_ok": 6,
        "organic_two_way": 12,
        "early_window": 6,
        "sweet_window": 4,
        "age_ok": 4,
        "not_wash": 8,
        "not_adult_bait": 6,
        "not_ai_shell": 6,
    }
    score = 0
    for k, w in weights.items():
        if checklist.get(k):
            score += w
    # Caps for missing critical structure
    if not checklist["not_wash"]:
        score = min(score, 35)
    if adult_bait or ai_shell:
        score = min(score, 25)
    if status_only and not real_website:
        score = min(score, 40)
    score = int(max(0, min(100, score)))

    missing = [k for k, v in checklist.items() if not v and k not in ("sweet_window",)]

    # Match tiers for $10M path
    core_ok = (
        checklist["deep_curve"]
        and checklist["solid_dist"]
        and checklist["organic_two_way"]
        and checklist["clean_social"]
        and checklist["not_wash"]
        and checklist["not_adult_bait"]
        and checklist["not_ai_shell"]
    )
    if score >= 78 and core_ok and checklist["early_window"]:
        tier = "MEGA_10M"
        entry_rec = "ENTER"
        match = True
        summary = (
            f"MEGA $10M fingerprint ({score}) — structure matches historical multi‑$M "
            f"coins (narrative + deep SOL + distribution + two-way flow)."
        )
    elif score >= 65 and core_ok:
        tier = "HIGH_10M"
        entry_rec = "ENTER" if checklist["early_window"] else "WATCH"
        match = True
        summary = (
            f"High $10M fingerprint ({score}) — most mega ingredients present. "
            f"{'Enter early window' if checklist['early_window'] else 'Wait for early retest / more structure'}."
        )
    elif score >= 52 and checklist["not_wash"] and checklist["not_adult_bait"]:
        tier = "BUILDING_10M"
        entry_rec = "WATCH"
        match = False
        summary = (
            f"Building toward $10M fingerprint ({score}). "
            f"Missing: {', '.join(missing[:5]) or 'minor items'}."
        )
    else:
        tier = "NONE"
        entry_rec = "SKIP" if score < 35 else "WATCH"
        match = False
        summary = (
            f"Low mega fingerprint ({score}) — unlikely $10M path. "
            f"Most trench coins die under $25k."
        )

    # Absolute TP ladder for $10M thesis from current mcap
    if mcap > 0:
        tp1 = max(mcap * 3, 15_000)
        tp2 = max(mcap * 10, 50_000)
        tp3 = max(mcap * 40, 250_000)
        moon = 10_000_000
        mega_cap = 100_000_000
    else:
        tp1, tp2, tp3, moon, mega_cap = 15_000, 100_000, 500_000, 10_000_000, 100_000_000

    if tier in ("MEGA_10M", "HIGH_10M"):
        tp_ladder = {
            "tp1_mcap": round(tp1),
            "tp2_mcap": round(min(tp2, 150_000) if mcap < 20_000 else tp2),
            "tp3_mcap": round(min(tp3, 1_000_000) if mcap < 20_000 else tp3),
            "moon_mcap": moon,
            "mega_band_mcap": mega_cap,
            "sell_pct": {"tp1": 30, "tp2": 25, "tp3": 20, "core": 25},
            "notes": "Scale out; keep core only if narrative + volume expand post-grad",
        }
    elif tier == "BUILDING_10M":
        tp_ladder = {
            "tp1_mcap": round(max(mcap * 2.5, 12_000) if mcap else 12_000),
            "tp2_mcap": round(max(mcap * 6, 40_000) if mcap else 40_000),
            "tp3_mcap": round(max(mcap * 15, 100_000) if mcap else 100_000),
            "moon_mcap": 1_000_000,
            "mega_band_mcap": 10_000_000,
            "sell_pct": {"tp1": 35, "tp2": 30, "tp3": 20, "core": 15},
            "notes": "Lower conviction — bank harder; upgrade if stack completes",
        }
    else:
        tp_ladder = {
            "tp1_mcap": round(max(mcap * 2, 10_000) if mcap else 10_000),
            "tp2_mcap": round(max(mcap * 4, 25_000) if mcap else 25_000),
            "tp3_mcap": round(max(mcap * 8, 50_000) if mcap else 50_000),
            "moon_mcap": 100_000,
            "mega_band_mcap": 250_000,
            "sell_pct": {"tp1": 40, "tp2": 35, "tp3": 25, "core": 0},
            "notes": "Not a $10M candidate — tight risk",
        }

    badges: list[dict[str, str]] = []
    if tier == "MEGA_10M":
        badges.append({"id": "mega10m", "label": "MEGA $10M+", "type": "mega10m"})
    elif tier == "HIGH_10M":
        badges.append({"id": "high10m", "label": "High $10M path", "type": "mega10m"})
    for tag in narrative_tags[:3]:
        badges.append({"id": f"narr_{tag}", "label": tag.replace("_", " "), "type": "narrative"})

    return {
        "score": score,
        "tier": tier,
        "match": match,
        "checklist": checklist,
        "checklist_hits": sum(1 for v in checklist.values() if v),
        "checklist_total": len(checklist),
        "narrative_tags": narrative_tags,
        "missing": missing[:8],
        "summary": summary,
        "entry_recommendation": entry_rec,
        "tp_ladder": tp_ladder,
        "badges": badges,
        "metrics": {
            "mcap": round(mcap),
            "holders": holders,
            "mid_bags": mid_bags,
            "max_wallet_pct": round(max_non_pool, 2),
            "quote_sol": round(quote_sol, 3),
            "buys_m5": buys_m5,
            "sells_m5": sells_m5,
            "buy_ratio": round(buy_ratio, 2),
            "own_twitter": own_twitter,
            "real_website": real_website,
            "viral": viral_list,
            "on_curve": on_curve,
            "age_min": round(float(age_min), 1) if age_min is not None else None,
        },
    }
