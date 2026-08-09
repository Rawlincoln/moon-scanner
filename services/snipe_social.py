"""Snipe social policy: social-optional, social-honest.

Product rule (money-mode snipes):
  - Missing X / TG / website is OK when the book is clean.
  - Spoofed socials are never OK (status-link X, media-as-website).
  - A real website does NOT save status-link X + empty description
    (Cashoty-class packaging).
  - Real own X / real site / TG are mild boosts only — not a gate.
  - Total silence only demotes when combined with other red flags
    (dead book, 0 replies + thin float, wash/AI shell flags).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Media / non-project hosts used as fake "website" fields
FAKE_SITE_HOSTS: tuple[str, ...] = (
    "instagram.com",
    "tiktok.com",
    "vm.tiktok.com",
    "youtube.com",
    "youtu.be",
    "x.com",
    "twitter.com",
    "t.me",
    "telegram.me",
    "urbandictionary.com",
    "pump.fun",
    "j7tracker.io",
)

# Avoid flags that make "no socials" a demote (not a hard reject)
SILENCE_RED_FLAGS: frozenset[str] = frozenset(
    {
        "dead_book",
        "empty_distribution",
        "wash_buys",
        "extreme_wash",
        "wash_fees",
        "bot_holder_cluster",
        "ghost_community",
        "zero_sellers",
        "ai_pitch_no_socials",
    }
)


def _host(url: str) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).netloc or "").lower().strip().replace("www.", "")
    except Exception:
        return ""


def _is_status_twitter(url: str) -> bool:
    u = (url or "").lower()
    return "status/" in u or "/i/status" in u


def _is_own_twitter(url: str) -> bool:
    """x.com/ProjectName ✓   x.com/random/status/123 ✗"""
    if not url or _is_status_twitter(url):
        return False
    u = (url or "").lower().strip()
    return bool(re.search(r"(?:x|twitter)\.com/[A-Za-z0-9_]{2,30}/?$", u.rstrip("/")))


def _is_real_website(url: str) -> bool:
    if not url:
        return False
    h = _host(url)
    if not h:
        return False
    if any(h == f or h.endswith("." + f) for f in FAKE_SITE_HOSTS):
        return False
    return True


def _pump_blob(token: dict[str, Any]) -> dict[str, Any]:
    pump = token.get("pumpfun") if isinstance(token.get("pumpfun"), dict) else {}
    # Top-level fallbacks used on some cards
    return {
        "twitter": str(
            pump.get("twitter") or token.get("twitter") or ""
        ).strip(),
        "website": str(
            pump.get("website") or token.get("website") or ""
        ).strip(),
        "telegram": str(
            pump.get("telegram") or token.get("telegram") or ""
        ).strip(),
        "description": str(
            pump.get("description") or token.get("description") or ""
        ).strip(),
        "reply_count": int(
            pump.get("reply_count")
            or token.get("reply_count")
            or 0
        ),
    }


def _avoid_flags(token: dict[str, Any]) -> set[str]:
    avoid = token.get("avoid")
    if not isinstance(avoid, dict):
        avoid = (token.get("safetyReport") or {}).get("avoid") or {}
    if not isinstance(avoid, dict):
        return set()
    return {str(f) for f in (avoid.get("flags") or [])}


def analyze_snipe_social(token: dict[str, Any]) -> dict[str, Any]:
    """Apply social-optional / social-honest policy for snipes.

    Returns:
      hard_reject: reason string if dishonest, else None
      score_delta: int score adjustment (boost real, demote silence+red)
      why: list of short notes for the snipe card
      flags: honesty-related flags detected here
      policy: fixed product string
    """
    blob = _pump_blob(token)
    twitter = blob["twitter"]
    website = blob["website"]
    telegram = blob["telegram"]
    desc = blob["description"]
    replies = blob["reply_count"]
    avoid_flags = _avoid_flags(token)

    flags: list[str] = []
    why: list[str] = []
    score_delta = 0

    fake_tw = bool(twitter and _is_status_twitter(twitter))
    fake_web = bool(website and not _is_real_website(website) and _host(website))
    own_x = _is_own_twitter(twitter)
    real_site = _is_real_website(website)
    has_tg = bool(telegram)
    has_any = bool(twitter or website or telegram)
    has_real = bool(own_x or real_site or has_tg)

    if fake_tw:
        flags.append("fake_twitter")
    if fake_web:
        flags.append("fake_website")

    # Entry trap: status X + empty/near-empty packaging.
    # Website alone does NOT save it (Cashoty).
    if fake_tw and len(desc) < 8:
        flags.append("entry_trap_social")
    elif fake_tw and replies == 0 and len(desc) < 20:
        flags.append("entry_trap_social")

    # Spoofed socials + no community
    if (fake_tw or fake_web) and replies == 0:
        flags.append("social_spoof_scam")

    # --- Hard honesty gate (social-honest) ---
    hard: str | None = None
    if "entry_trap_social" in flags:
        hard = (
            "social dishonest: status-link X + empty description "
            "(website alone does not save)"
        )
    elif fake_tw:
        hard = "social dishonest: Twitter is a status link, not a project account"
    elif fake_web:
        hard = (
            f"social dishonest: website is {_host(website) or 'media'} link "
            "— not a real project site"
        )
    elif "social_spoof_scam" in avoid_flags or "entry_trap_social" in avoid_flags:
        # Prefer avoid-pipeline wording when already flagged upstream
        if "entry_trap_social" in avoid_flags:
            hard = (
                "social dishonest: entry-trap packaging "
                "(status X / empty community)"
            )
        else:
            hard = "social dishonest: fake socials + no community"

    if hard:
        return {
            "hard_reject": hard,
            "score_delta": 0,
            "why": [hard],
            "flags": flags,
            "has_any_social": has_any,
            "has_real_social": False,
            "own_twitter": False,
            "real_website": False,
            "policy": "social-optional, social-honest",
            "honest": False,
        }

    # --- Social-optional: silence is neutral unless red flags ---
    if not has_any:
        red = avoid_flags & SILENCE_RED_FLAGS
        # Local thin-community signal if avoid not enriched yet
        safety = token.get("safety") if isinstance(token.get("safety"), dict) else {}
        holders = int(safety.get("total_holders") or 0)
        if replies == 0 and holders > 0 and holders < 40:
            red = set(red) | {"dead_book"}
        if red:
            score_delta -= 12
            why.append(
                "No socials + thin/wash book — silence demote "
                f"({', '.join(sorted(red)[:2])})"
            )
            flags.append("silence_red_flags")
        else:
            why.append("No socials — social-optional OK (clean book)")
            flags.append("social_optional_ok")
    else:
        # Mild boosts only — never required
        if own_x:
            score_delta += 10
            why.append("Own X account (honest social)")
            flags.append("own_twitter")
        elif twitter and not fake_tw:
            # Non-status URL that isn't a clean profile path — slight boost
            score_delta += 4
            why.append("X link present (not a status spoof)")

        if real_site:
            score_delta += 8
            why.append(f"Real website: {_host(website)}")
            flags.append("real_website")

        if has_tg:
            score_delta += 3
            why.append("Telegram linked")
            flags.append("telegram")

        if not has_real and has_any:
            # Unexpected residual packaging without hard fakes
            score_delta -= 4
            why.append("Social links weak/unclear — size smaller")

    # Narrative edge already scored in evaluate_snipe via socialSignals;
    # only note honesty here.
    return {
        "hard_reject": None,
        "score_delta": score_delta,
        "why": why,
        "flags": flags,
        "has_any_social": has_any,
        "has_real_social": has_real,
        "own_twitter": own_x,
        "real_website": real_site,
        "policy": "social-optional, social-honest",
        "honest": True,
    }


def snipe_social_reject_reason(token: dict[str, Any]) -> str | None:
    """Hard reject string if socials are dishonest; None if optional/honest."""
    return analyze_snipe_social(token).get("hard_reject")
