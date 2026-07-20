"""Curated historical $10M–$100M+ tokens for learning seed.

Entry features are *idealized early-trench fingerprints* (what to look for at $3.5k–$8k),
not exact launch-day snapshots. ATH mcaps are approximate historical peaks used for
outcome labels (MEGA when ath >= $10M).

Seed version: bump MEGA_SEEDS_VERSION when adding tokens so startup re-injects.
"""

from __future__ import annotations

from typing import Any

# Bump when seed catalog changes so LearningEngine re-applies
MEGA_SEEDS_VERSION = "mega_v2_10m_2026_07"

# Shared "looks like a real mega at entry" feature template
_MEGA_EARLY: dict[str, Any] = {
    "has_viral": 1,
    "own_twitter": 1,
    "real_website": 1,
    "mid_bags_ge_5": 1,
    "holders_ge_40": 1,
    "curve_sol_ge_5": 1,
    "buy_ratio_ge_1.3": 1,
    "mcap_bin:sweet_3.5_7.5k": 1,
    "alpha_bin:alpha_high": 1,
    "mega_fingerprint:MEGA_10M": 1,
    "organic_two_way": 1,
    "clean_social_stack": 1,
    "deep_curve_sol": 1,
    "solid_distribution": 1,
    "external_narrative": 1,
}

_MEGA_AI: dict[str, Any] = {
    **_MEGA_EARLY,
    "narrative:ai_agent": 1,
    "has_viral": 0,  # AI lore can scale without TikTok
    "own_twitter": 1,
    "real_website": 1,
}

_MEGA_ANIMAL: dict[str, Any] = {
    **_MEGA_EARLY,
    "narrative:animal_mascot": 1,
    "has_viral": 1,
}

_MEGA_CULTURE: dict[str, Any] = {
    **_MEGA_EARLY,
    "narrative:culture_brand": 1,
    "has_viral": 1,
}


def _seed(
    mint: str,
    name: str,
    symbol: str,
    *,
    first_mcap: float,
    ath_mcap: float,
    features: dict[str, Any],
    notes: str = "",
) -> dict[str, Any]:
    outcome = "MEGA" if ath_mcap >= 10_000_000 else (
        "SUPER" if ath_mcap >= 1_000_000 else "WINNER"
    )
    return {
        "mint": mint,
        "name": name,
        "symbol": symbol,
        "first_mcap": first_mcap,
        "ath_mcap": ath_mcap,
        "outcome": outcome,
        "features": features,
        "notes": notes or f"historical_{outcome.lower()}_seed",
    }


# Historical multi‑$M Solana / pump.fun-class winners
MEGA_SEEDS: list[dict[str, Any]] = [
    _seed(
        "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump",
        "Fartcoin",
        "Fartcoin",
        first_mcap=5500,
        ath_mcap=2_000_000_000,  # peaked well above $100M
        features={**_MEGA_CULTURE, "narrative:culture_brand": 1, "has_viral": 1},
        notes="pump.fun → multi‑$100M culture meme",
    ),
    _seed(
        "2qEHjDLDLbuBgRYvsxhc5D6uDWAivNFZGan56P1tpump",
        "Peanut the Squirrel",
        "PNUT",
        first_mcap=6000,
        ath_mcap=1_500_000_000,
        features={**_MEGA_ANIMAL, "narrative:animal_mascot": 1, "external_narrative": 1},
        notes="real-world animal + politics attention",
    ),
    _seed(
        "CzLSujWBLFsSjncfkh59rUFqvafWcY5tzedWJSuypump",
        "Goatseus Maximus",
        "GOAT",
        first_mcap=5000,
        ath_mcap=1_000_000_000,
        features={**_MEGA_AI, "narrative:ai_agent": 1},
        notes="Truth Terminal AI agent narrative",
    ),
    _seed(
        "Df6yfrKC8kZE3KNkrHERKzAetSxbrWeniQfyJY4Jpump",
        "Just a chill guy",
        "CHILLGUY",
        first_mcap=5500,
        ath_mcap=500_000_000,
        features={**_MEGA_CULTURE, "has_viral": 1, "narrative:culture_brand": 1},
        notes="pre-viral internet meme → pump graduate",
    ),
    _seed(
        "GJAFwWjJ3vnTsrQVabjBVK2TYB1YtRCQXRDfDgUnpump",
        "Act I : The AI Prophecy",
        "ACT",
        first_mcap=5000,
        ath_mcap=600_000_000,
        features={**_MEGA_AI, "real_website": 1, "own_twitter": 1},
        notes="AI prophecy culture + multi-channel socials",
    ),
    _seed(
        "A8C3xuqscfmyLrte3VmTqrAq8kgMASius9AFNANwpump",
        "FWOG",
        "FWOG",
        first_mcap=5000,
        ath_mcap=300_000_000,
        features={**_MEGA_ANIMAL, "narrative:animal_mascot": 1},
        notes="animal meme with community brand",
    ),
    _seed(
        "6AJcP7wuLwmRYLBNbi825wgguaPsWzPBEHcHndpRpump",
        "Vine Coin",
        "VINE",
        first_mcap=6000,
        ath_mcap=400_000_000,
        features={**_MEGA_CULTURE, "own_twitter": 1, "real_website": 1},
        notes="personality / culture brand launch",
    ),
    _seed(
        "ED5nyyWEzpPPiWimP8vYm7sD7TD3LAt3Q3gRTWHzPJBY",
        "Moo Deng",
        "MOODENG",
        first_mcap=7000,
        ath_mcap=500_000_000,
        features={**_MEGA_ANIMAL, "has_viral": 1, "external_narrative": 1},
        notes="zoo hippo viral fame before coin",
    ),
    _seed(
        "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr",
        "Popcat",
        "POPCAT",
        first_mcap=8000,
        ath_mcap=2_000_000_000,
        features={**_MEGA_CULTURE, "has_viral": 1, "own_twitter": 1, "real_website": 1},
        notes="pre-existing meme + brand stack",
    ),
    _seed(
        "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "dogwifhat",
        "WIF",
        first_mcap=10000,
        ath_mcap=4_000_000_000,
        features={**_MEGA_CULTURE, "own_twitter": 1, "real_website": 1, "holders_ge_100": 1},
        notes="simple mascot + culture — template mega",
    ),
    _seed(
        "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",
        "cat in a dogs world",
        "MEW",
        first_mcap=8000,
        ath_mcap=1_000_000_000,
        features={**_MEGA_ANIMAL, "own_twitter": 1, "real_website": 1},
        notes="cat culture brand on Solana",
    ),
    _seed(
        "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
        "BOOK OF MEME",
        "BOME",
        first_mcap=9000,
        ath_mcap=1_500_000_000,
        features={**_MEGA_CULTURE, "own_twitter": 1, "external_narrative": 1},
        notes="artist-driven meme book narrative",
    ),
    _seed(
        "63LfDmNb3MQ8mw9MtZ2To9bEA2M71kZUUGq5tiJxcqj9",
        "GIGACHAD",
        "GIGA",
        first_mcap=7000,
        ath_mcap=800_000_000,
        features={**_MEGA_CULTURE, "has_viral": 1, "own_twitter": 1, "real_website": 1},
        notes="pre-existing internet persona meme",
    ),
    _seed(
        "5z3EqYQo9HiCEs3R84RCDMu2n7anpDMxRhdK8PSWmrRC",
        "PONKE",
        "PONKE",
        first_mcap=7000,
        ath_mcap=400_000_000,
        features={**_MEGA_CULTURE, "own_twitter": 1, "real_website": 1},
        notes="character brand + multi social",
    ),
    _seed(
        "HeLp6NuQkmYB4pYWo2zYs22mESHXPQYzXbB8n4V98ojC",
        "ai16z",
        "ai16z",
        first_mcap=6000,
        ath_mcap=2_000_000_000,
        features={**_MEGA_AI, "narrative:ai_agent": 1, "real_website": 1},
        notes="AI agent / DAO culture meta",
    ),
    _seed(
        "8x5VqbHA8D7NkD52uNuS5nnt3PwA8pLD34ymskeSo2Wn",
        "Zerebro",
        "ZEREBRO",
        first_mcap=5500,
        ath_mcap=500_000_000,
        features={**_MEGA_AI, "narrative:ai_agent": 1},
        notes="AI agent narrative runner",
    ),
    _seed(
        "KENJSUYLASHUMfHyy5o4Hp2FdNqZg1AsUPhfH2kYvEP",
        "GRIFFAIN",
        "GRIFFAIN",
        first_mcap=5500,
        ath_mcap=300_000_000,
        features={**_MEGA_AI, "real_website": 1, "own_twitter": 1},
        notes="AI product + community socials",
    ),
    # Local documented winner (sub-100k but positive structure template)
    _seed(
        "FUY6RbdfrDfa82y1AS5ZQRtaoSr1ZVTGD2EkN11bpump",
        "The Addiction Bird",
        "KIWI",
        first_mcap=5200,
        ath_mcap=62_489,
        features={
            "has_viral": 1,
            "own_twitter": 1,
            "real_website": 1,
            "mid_bags_ge_5": 1,
            "holders_ge_100": 1,
            "curve_sol_ge_5": 1,
            "buy_ratio_ge_1.3": 1,
            "mcap_bin:sweet_3.5_7.5k": 1,
            "alpha_bin:alpha_high": 1,
            "organic_two_way": 1,
            "clean_social_stack": 1,
            "external_narrative": 1,
            "mega_fingerprint:BUILDING_10M": 1,
        },
        notes="local WINNER — viral TikTok + own X + real site template",
    ),
]


def all_seed_mints() -> set[str]:
    return {s["mint"] for s in MEGA_SEEDS}
