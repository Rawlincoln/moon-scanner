"""X/TikTok presence, ticker narratives, and influencer tweet detection.

Priority signals (in order of edge):
  1. Real tweet URL from Elon / CZ / Trump / other market movers
  2. Trending narrative in ticker/name + real community
  3. Own X account + organic replies (no status-link spoof)

Name-jacking ("ELON" with no real Elon tweet) is NOT an edge — often a rug.
"""

from __future__ import annotations

import re
from typing import Any

# X handles that move meme markets (lookup is always lowercased)
INFLUENTIAL_X_HANDLES: dict[str, str] = {
    # --- User watchlist (Solana / meme movers) ---
    "elonmusk": "Elon Musk",
    "blknoiz06": "Ansem",
    "cobratate": "Andrew Tate",
    "iggyazalea": "Iggy Azalea",
    "theunipcs": "Bonk Guy / Unipcs",
    "mattwallace888": "Matt Wallace",
    "a1lon9": "Alon (pump.fun)",
    "billym2k": "Billy Markus",
    "cryptowendyo": "Crypto Wendy O",
    "davidgokhshtein": "David Gokhshtein",
    "muststopmurad": "Murad",
    "cryptohayes": "Arthur Hayes",
    "ashcryptoreal": "Ash Crypto",
    "themooncarl": "TheMoonCarl",
    "thecryptolark": "Lark Davis",
    "bitboy_crypto": "BitBoy",
    "altcoindailyio": "Altcoin Daily",
    "frankdegods": "Frank DeGods",
    "solbigbrain": "SOL Big Brain",
    "darkfarms1": "Darkfarms",
    # --- Other tier-1 / ecosystem ---
    "realdonaldtrump": "Donald Trump",
    "potus": "Donald Trump",
    "donaldjtrumpjr": "Trump family",
    "melaniatrump": "Melania Trump",
    "cz_binance": "CZ",
    "cz": "CZ",
    "binance": "Binance",
    "vitalikbuterin": "Vitalik",
    "saylor": "Michael Saylor",
    "michael_saylor": "Michael Saylor",
    "cathiedwood": "Cathie Wood",
    "chamath": "Chamath",
    "naval": "Naval",
    "balajis": "Balaji",
    "pumpdotfun": "pump.fun",
    "zhusu": "Zhu Su",
    "hsaka": "Hsaka",
    "cryptopunk7213": "CryptoPunk",
    "cobie": "Cobie",
    "0xngmi": "0xngmi",
    "gabor": "Gabor",
    "solana": "Solana",
    "solanafndn": "Solana Foundation",
    "raydiumprotocol": "Raydium",
    "jupiterexchange": "Jupiter",
    "cb_doge": "Doge account",
    "tesla": "Tesla",
    "spacex": "SpaceX",
    "x": "X / Twitter",
    "openai": "OpenAI",
    "sama": "Sam Altman",
    "grok": "Grok",
    "tiktok": "TikTok",
    "mrbeast": "MrBeast",
    "kanyewest": "Kanye",
    "ye": "Kanye",
    "drake": "Drake",
    "therock": "The Rock",
    "neiltyson": "Neil deGrasse Tyson",
    "joerogan": "Joe Rogan",
    "roaringkitty": "Roaring Kitty",
    "the_real_fly": "Fly",
    "trampolineboy": "Trampoline",
}

# Ticker / name / description → narrative (substring match on normalized text)
NARRATIVE_KEYWORDS: dict[str, str] = {
    # Elon / X / AI cluster
    "elon": "Elon narrative",
    "musk": "Elon narrative",
    "tesla": "Tesla narrative",
    "spacex": "SpaceX narrative",
    "doge": "Doge narrative",
    "shib": "Meme dog narrative",
    "grok": "Grok / xAI narrative",
    "xai": "xAI narrative",
    "optimus": "Optimus / Tesla AI",
    "starlink": "Starlink narrative",
    "neuralink": "Neuralink narrative",
    "boring company": "Boring Co narrative",
    # Politics
    "trump": "Trump narrative",
    "maga": "MAGA narrative",
    "donald": "Trump narrative",
    "potus": "Trump narrative",
    "melania": "Trump family narrative",
    "barron": "Trump family narrative",
    "jd vance": "Politics narrative",
    "vance": "Politics narrative",
    "america first": "MAGA narrative",
    # CZ / exchange
    "cz": "CZ narrative",
    "binance": "Binance narrative",
    "bnb": "BNB narrative",
    # Culture / viral platforms
    "tiktok": "TikTok narrative",
    "vine": "Vine nostalgia",
    "instagram": "IG narrative",
    "youtube": "YouTube narrative",
    "mrbeast": "MrBeast narrative",
    "kai cenat": "Streamer narrative",
    "speed": "Streamer narrative",
    # Meme classics that still trend
    "pepe": "Pepe narrative",
    "wojak": "Wojak narrative",
    "bonk": "Bonk narrative",
    "wif": "dogwifhat narrative",
    "popcat": "Popcat narrative",
    "mog": "MOG narrative",
    "fartcoin": "Fartcoin narrative",
    "ai agent": "AI agent narrative",
    "ai ": "AI narrative",
    "gpt": "AI narrative",
    "claude": "AI narrative",
    "solana": "Solana narrative",
    "pump": "pump.fun meta",
    "roaring kitty": "Roaring Kitty",
    "gamestop": "GME narrative",
    "gme": "GME narrative",
    "bitcoin": "Bitcoin narrative",
    "btc": "Bitcoin narrative",
}

# Tickers that often map to live meta (exact symbol match, case-insensitive)
HOT_TICKERS: dict[str, str] = {
    "DOGE": "Doge narrative",
    "TRUMP": "Trump narrative",
    "MAGA": "MAGA narrative",
    "ELON": "Elon narrative",
    "MUSK": "Elon narrative",
    "GROK": "Grok narrative",
    "XAI": "xAI narrative",
    "PEPE": "Pepe narrative",
    "BONK": "Bonk narrative",
    "WIF": "dogwifhat narrative",
    "POPCAT": "Popcat narrative",
    "AI": "AI narrative",
    "SOL": "Solana narrative",
    "BTC": "Bitcoin narrative",
    "CZ": "CZ narrative",
    "BNB": "BNB narrative",
    "TIKTOK": "TikTok narrative",
    "MELANIA": "Trump family narrative",
    "BARRON": "Trump family narrative",
}

# Junk ticker patterns (often bot farms)
_JUNK_TICKER_RE = re.compile(
    r"^(test|asd|qwe|zzz|xxx|aaa|bbb|ccc|null|undefined|token|coin|moon|rug|scam)$",
    re.I,
)

_X_HANDLE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,50})",
    re.I,
)
_STATUS_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,50})/status/(\d+)",
    re.I,
)
_TIKTOK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com)[^\s\"']*",
    re.I,
)


def _normalize_text(*parts: str | None) -> str:
    return " ".join(p for p in parts if p).lower()


def _extract_x_handle(url: str) -> str | None:
    if not url:
        return None
    m = _X_HANDLE_RE.search(url.strip())
    if not m:
        return None
    handle = m.group(1).lower()
    if handle in ("status", "intent", "share", "home", "search", "i", "hashtag"):
        return None
    return handle


def _parse_status_url(url: str) -> tuple[str | None, str | None]:
    """Return (handle, status_id) if this is a tweet URL."""
    if not url:
        return None, None
    m = _STATUS_RE.search(url.strip())
    if not m:
        return None, None
    return m.group(1).lower(), m.group(2)


def _is_tweet_url(url: str) -> bool:
    return bool(url and "/status/" in url.lower())


def ticker_quality(
    symbol: str = "",
    name: str = "",
) -> dict[str, Any]:
    """Score ticker/name quality — reject bot junk, flag hot tickers."""
    sym = (symbol or "").strip()
    nm = (name or "").strip()
    issues: list[str] = []
    score = 50

    if not sym or len(sym) > 14:
        issues.append("bad ticker length")
        score -= 30
    if _JUNK_TICKER_RE.match(sym):
        issues.append("junk ticker")
        score -= 40
    # Random all-consonant spam (e.g. XKQRTV)
    letters = re.sub(r"[^a-zA-Z]", "", sym)
    if len(letters) >= 5:
        vowels = sum(1 for c in letters.lower() if c in "aeiou")
        if vowels == 0:
            issues.append("gibberish ticker")
            score -= 25
    # Pure hex-like noise
    if re.fullmatch(r"[0-9a-fA-F]{8,}", sym):
        issues.append("hex junk ticker")
        score -= 35

    hot = HOT_TICKERS.get(sym.upper())
    if hot:
        score += 20

    # Name is just the mint or empty
    if len(nm) < 2:
        issues.append("empty name")
        score -= 15

    return {
        "score": max(0, min(100, score)),
        "issues": issues,
        "hot_ticker": hot,
        "symbol": sym,
        "ok": score >= 35 and not any(
            x in issues for x in ("junk ticker", "hex junk ticker", "gibberish ticker")
        ),
    }


def analyze_social_narrative(
    pump_coin: dict | None = None,
    name: str = "",
    symbol: str = "",
    description: str = "",
    links: list[dict] | None = None,
) -> dict[str, Any]:
    """Scan metadata for X, TikTok, influencer tweets, and trending narratives."""
    coin = pump_coin or {}
    name = name or coin.get("name", "") or ""
    symbol = symbol or coin.get("symbol", "") or ""
    description = description or coin.get("description", "") or ""

    twitter = (coin.get("twitter") or "").strip()
    website = (coin.get("website") or "").strip()
    telegram = (coin.get("telegram") or "").strip()

    blob_urls = " ".join(u for u in [twitter, website, telegram, description] if u)
    for link in links or []:
        blob_urls += " " + str(link.get("url", ""))

    has_x = bool(twitter) or "x.com" in blob_urls.lower() or "twitter.com" in blob_urls.lower()
    tiktok_match = _TIKTOK_RE.search(blob_urls)
    has_tiktok = bool(tiktok_match) or "tiktok" in _normalize_text(description, name, website)

    x_url = twitter if twitter else None
    if not x_url:
        xm = _X_HANDLE_RE.search(blob_urls)
        if xm:
            x_url = xm.group(0)

    tiktok_url = tiktok_match.group(0) if tiktok_match else None
    if not tiktok_url and "tiktok.com" in website.lower():
        tiktok_url = website

    # --- Influencer accounts + real tweet detection ---
    influencer_accounts: list[str] = []
    influencer_tweet = False
    tweet_by: str | None = None
    tweet_url: str | None = None
    status_handle: str | None = None

    # Direct status URL on twitter field (most important signal)
    sh, sid = _parse_status_url(twitter)
    if sh and sid:
        status_handle = sh
        label = INFLUENTIAL_X_HANDLES.get(sh)
        if label:
            influencer_tweet = True
            tweet_by = label
            tweet_url = twitter
            if label not in influencer_accounts:
                influencer_accounts.append(label)

    # Any status URL in blob
    for m in _STATUS_RE.finditer(blob_urls):
        h = m.group(1).lower()
        label = INFLUENTIAL_X_HANDLES.get(h)
        if label:
            if label not in influencer_accounts:
                influencer_accounts.append(label)
            influencer_tweet = True
            tweet_by = tweet_by or label
            tweet_url = tweet_url or m.group(0)

    handles_to_check: list[str] = []
    if twitter:
        h = _extract_x_handle(twitter)
        if h:
            handles_to_check.append(h)
    for m in _X_HANDLE_RE.finditer(blob_urls):
        h = m.group(1).lower()
        if h not in ("status", "intent", "share", "home", "search", "i", "hashtag"):
            handles_to_check.append(h)

    for handle in set(handles_to_check):
        label = INFLUENTIAL_X_HANDLES.get(handle)
        if label and label not in influencer_accounts:
            influencer_accounts.append(label)

    # Profile link to influencer (not a tweet) — weaker than tweet, still useful
    profile_only_influencer = bool(influencer_accounts) and not influencer_tweet

    # --- Narratives from name/symbol/desc ---
    text = _normalize_text(name, symbol, description)
    narrative_keywords: list[str] = []
    narratives: list[str] = []
    for kw, label in NARRATIVE_KEYWORDS.items():
        if kw in text and label not in narratives:
            narratives.append(label)
            narrative_keywords.append(kw)

    tq = ticker_quality(symbol, name)
    if tq.get("hot_ticker") and tq["hot_ticker"] not in narratives:
        narratives.insert(0, tq["hot_ticker"])

    # Name-jacking: narrative words without real influencer link
    namejack = bool(narratives) and not influencer_tweet and not influencer_accounts
    # Stronger: "ELON" ticker with zero community
    namejack_risk = namejack and _i_replies(coin) < 5

    replies = _i_replies(coin)
    status_only = _is_tweet_url(twitter) and not influencer_tweet
    real_x = bool(
        twitter
        and not _is_tweet_url(twitter)
        and ("x.com/" in twitter.lower() or "twitter.com/" in twitter.lower())
    )

    # --- Edge score (0–100): what actually drives moons ---
    edge = 0
    edge_reasons: list[str] = []
    if influencer_tweet and tweet_by:
        edge += 55
        edge_reasons.append(f"🔥 {tweet_by} tweet linked")
    elif profile_only_influencer:
        edge += 22
        edge_reasons.append(f"Linked to {influencer_accounts[0]}")
    if narratives and not namejack_risk:
        edge += 18
        edge_reasons.append(narratives[0])
    elif narratives and namejack_risk:
        edge += 4  # name-jack alone is weak / often a rug
        edge_reasons.append(f"Name-jack risk: {narratives[0]}")
    if has_tiktok:
        edge += 12
        edge_reasons.append("TikTok")
    if real_x:
        edge += 10
        edge_reasons.append("Own X account")
    if replies >= 30:
        edge += 15
        edge_reasons.append(f"{replies} replies")
    elif replies >= 12:
        edge += 10
        edge_reasons.append(f"{replies} replies")
    elif replies >= 5:
        edge += 5
    if tq.get("hot_ticker") and (replies >= 8 or real_x or influencer_tweet):
        edge += 12
        edge_reasons.append(f"Hot ticker ${symbol}")
    if not tq.get("ok"):
        edge -= 20
        edge_reasons.extend(tq.get("issues") or [])

    edge = max(0, min(100, edge))

    # Must have a real "story" to recommend (not random green chart)
    has_edge = (
        influencer_tweet
        or (edge >= 40 and (narratives or has_tiktok or replies >= 12))
        or (profile_only_influencer and replies >= 8)
        or (bool(narratives) and real_x and replies >= 10 and not namejack_risk)
    )

    badges: list[dict[str, str]] = []
    if influencer_tweet and tweet_by:
        badges.append({
            "id": "influencer_tweet",
            "label": f"{tweet_by} TWEET",
            "type": "influencer_tweet",
        })
    for acct in influencer_accounts[:3]:
        badges.append({
            "id": f"inf_{acct.lower().replace(' ', '_')}",
            "label": acct,
            "type": "influencer",
        })
    for narr in narratives[:3]:
        badges.append({
            "id": f"narr_{narr[:20]}",
            "label": narr,
            "type": "narrative",
        })
    if has_tiktok:
        badges.append({"id": "tiktok", "label": "TikTok", "type": "social"})
    if has_x and real_x:
        badges.append({"id": "x", "label": "Own X", "type": "social"})
    if namejack_risk:
        badges.append({"id": "namejack", "label": "Name-jack risk", "type": "warn"})

    highlight = bool(influencer_tweet or (has_edge and edge >= 45))

    summary_parts: list[str] = []
    if influencer_tweet and tweet_by:
        summary_parts.append(f"Linked to {tweet_by} tweet")
    elif influencer_accounts:
        summary_parts.append(f"X → {', '.join(influencer_accounts[:2])}")
    if narratives:
        summary_parts.append(narratives[0])
    if has_tiktok:
        summary_parts.append("TikTok")
    if namejack_risk:
        summary_parts.append("⚠ name-jack (no real influencer tweet)")

    return {
        "has_x": has_x,
        "has_tiktok": has_tiktok,
        "x_url": x_url,
        "tiktok_url": tiktok_url,
        "influencer_tweet": influencer_tweet,
        "tweet_by": tweet_by,
        "tweet_url": tweet_url,
        "status_handle": status_handle,
        "influencer_accounts": influencer_accounts,
        "profile_only_influencer": profile_only_influencer,
        "narrative_keywords": narrative_keywords,
        "narratives": narratives,
        "namejack_risk": namejack_risk,
        "ticker": tq,
        "edge_score": edge,
        "edge_reasons": edge_reasons[:5],
        "has_edge": has_edge,
        "replies": replies,
        "real_x": real_x,
        "status_only": status_only,
        "badges": badges,
        "highlight": highlight,
        "summary": " · ".join(summary_parts) if summary_parts else "",
    }


def _i_replies(coin: dict) -> int:
    try:
        return int(coin.get("reply_count") or 0)
    except (TypeError, ValueError):
        return 0
