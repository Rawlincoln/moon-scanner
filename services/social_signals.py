"""Detect X/TikTok presence and influencer narrative (Elon, Trump, etc.)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# X handles known to move meme markets when they tweet/post
INFLUENTIAL_X_HANDLES: dict[str, str] = {
    "elonmusk": "Elon Musk",
    "realDonaldTrump": "Donald Trump",
    "potus": "Donald Trump",
    "donaldjtrumpjr": "Trump family",
    "vitalikbuterin": "Vitalik",
    "cz_binance": "CZ",
    "saylor": "Michael Saylor",
    "cathiedwood": "Cathie Wood",
    "chamath": "Chamath",
    "naval": "Naval",
    "a1lon9": "alon (pump.fun)",
    "blknoiz06": "Ansem",
    "cb_doge": "Doge account",
    "tesla": "Tesla",
    "x": "X / Twitter official",
}

# Name/symbol/description keyword → narrative label
NARRATIVE_KEYWORDS: dict[str, str] = {
    "elon": "Elon narrative",
    "musk": "Elon narrative",
    "tesla": "Elon/Tesla narrative",
    "doge": "Doge narrative",
    "grok": "Grok/Elon narrative",
    "xai": "xAI narrative",
    "trump": "Trump narrative",
    "maga": "Trump/MAGA narrative",
    "donald": "Trump narrative",
    "potus": "Trump narrative",
    "melania": "Trump family narrative",
    "barron": "Trump family narrative",
    "tiktok": "TikTok narrative",
    "vine": "Vine/TikTok nostalgia",
    "roaringkitty": "Roaring Kitty narrative",
    "gamestop": "GME narrative",
    "pepe": "Pepe narrative",
    "bonk": "Bonk narrative",
}

_X_HANDLE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,50})",
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
    if handle in ("status", "intent", "share", "home", "search", "i"):
        return None
    return handle


def _is_tweet_url(url: str) -> bool:
    return bool(url and "/status/" in url.lower())


def analyze_social_narrative(
    pump_coin: dict | None = None,
    name: str = "",
    symbol: str = "",
    description: str = "",
    links: list[dict] | None = None,
) -> dict[str, Any]:
    """Scan token metadata for X, TikTok, and influencer narratives."""
    coin = pump_coin or {}
    name = name or coin.get("name", "")
    symbol = symbol or coin.get("symbol", "")
    description = description or coin.get("description", "")

    twitter = (coin.get("twitter") or "").strip()
    website = (coin.get("website") or "").strip()
    telegram = (coin.get("telegram") or "").strip()

    blob_urls = " ".join(
        u
        for u in [twitter, website, telegram, description]
        if u
    )
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

    influencer_accounts: list[str] = []
    influencer_tweet = False
    tweet_by: str | None = None

    handles_to_check: list[str] = []
    if twitter:
        h = _extract_x_handle(twitter)
        if h:
            handles_to_check.append(h)
    for m in _X_HANDLE_RE.finditer(blob_urls):
        h = m.group(1).lower()
        if h not in ("status", "intent", "share", "home", "search", "i"):
            handles_to_check.append(h)

    for handle in set(handles_to_check):
        label = INFLUENTIAL_X_HANDLES.get(handle)
        if label and label not in influencer_accounts:
            influencer_accounts.append(label)
            if twitter and handle in twitter.lower() and _is_tweet_url(twitter):
                influencer_tweet = True
                tweet_by = label

    text = _normalize_text(name, symbol, description)
    narrative_keywords: list[str] = []
    narratives: list[str] = []
    for kw, label in NARRATIVE_KEYWORDS.items():
        if kw in text and label not in narratives:
            narratives.append(label)
            narrative_keywords.append(kw)

    badges: list[dict[str, str]] = []
    if has_x:
        badges.append({"id": "x", "label": "X / Twitter", "type": "social"})
    if has_tiktok:
        badges.append({"id": "tiktok", "label": "TikTok", "type": "social"})
    for acct in influencer_accounts:
        badges.append({
            "id": f"influencer_{acct.lower().replace(' ', '_')}",
            "label": f"@{acct}" if "@" not in acct else acct,
            "type": "influencer",
        })
    if influencer_tweet and tweet_by:
        badges.append({
            "id": "influencer_tweet",
            "label": f"{tweet_by} tweet",
            "type": "influencer_tweet",
        })
    for narr in narratives[:3]:
        slug = narr.lower().replace(" ", "_").replace("/", "_")
        badges.append({"id": f"narrative_{slug}", "label": narr, "type": "narrative"})

    highlight = bool(
        influencer_accounts
        or influencer_tweet
        or narratives
        or (has_x and has_tiktok)
    )

    summary_parts: list[str] = []
    if influencer_tweet and tweet_by:
        summary_parts.append(f"Linked to {tweet_by} tweet on X")
    elif influencer_accounts:
        summary_parts.append(f"X linked to {', '.join(influencer_accounts[:2])}")
    if narratives:
        summary_parts.append(narratives[0])
    if has_tiktok:
        summary_parts.append("TikTok presence")
    elif has_x and not summary_parts:
        summary_parts.append("Has X / Twitter")

    return {
        "has_x": has_x,
        "has_tiktok": has_tiktok,
        "x_url": x_url,
        "tiktok_url": tiktok_url,
        "influencer_tweet": influencer_tweet,
        "influencer_accounts": influencer_accounts,
        "narrative_keywords": narrative_keywords,
        "narratives": narratives,
        "badges": badges,
        "highlight": highlight,
        "summary": " · ".join(summary_parts) if summary_parts else "",
    }