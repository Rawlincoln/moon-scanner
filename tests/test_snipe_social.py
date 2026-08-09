"""Snipe social policy: social-optional, social-honest."""

from __future__ import annotations

from services.avoid_filters import HARD_AVOID_FLAGS, analyze_avoid_flags, is_hard_avoid
from services.safe_snipes import evaluate_snipe, snipe_reject_reason
from services.snipe_social import analyze_snipe_social


def _base(**kw):
    t = {
        "tokenAddress": "SafeSnipeMint1111111111111111111111111pump",
        "mcap_usd": 7000,
        "ath_mcap": 7200,
        "age_minutes": 12,
        "bonding_progress": 28,
        "enrich_ok": True,
        "priceChange": {"m5": 8, "h1": 15},
        "bundleSniper": {
            "hard_reject": False,
            "overall": "low",
            "holders_known": True,
            "bundled_pct": 2.0,
            "bundle": {"bundled_pct": 2.0, "risk_level": "low"},
            "snipers": {"risk_level": "low", "max_wallet_pct": 4.0, "score": 10},
        },
        "bundle": {"bundled_pct": 2.0, "risk_level": "low"},
        "snipers": {"risk_level": "low", "max_wallet_pct": 4.0},
        "avoid": {"avoid": False, "flags": []},
        "socialSignals": {},
        "safety": {
            "passed": True,
            "total_holders": 120,
            "top_holders": [{"pct": 2}, {"pct": 1.5}],
        },
        "pumpfun": {
            "description": "a small meme with a clean book",
            "reply_count": 4,
        },
    }
    t.update(kw)
    return t


def test_no_socials_clean_book_optional_ok():
    """Missing socials is fine when book is clean — social-optional."""
    t = _base(
        pumpfun={"description": "clean snipe", "reply_count": 5},
    )
    soc = analyze_snipe_social(t)
    assert soc["hard_reject"] is None
    assert soc["honest"] is True
    assert "social_optional_ok" in soc["flags"]
    assert soc["score_delta"] == 0
    assert snipe_reject_reason(t) is None
    ev = evaluate_snipe(t)
    assert ev["eligible"]
    assert ev["snipe_social"]["policy"] == "social-optional, social-honest"


def test_status_twitter_hard_reject():
    """Status-link X is dishonest even with a description."""
    t = _base(
        pumpfun={
            "twitter": "https://x.com/someone/status/1234567890",
            "description": "hello world this is a coin",
            "reply_count": 2,
        }
    )
    soc = analyze_snipe_social(t)
    assert soc["hard_reject"]
    assert "status" in soc["hard_reject"].lower() or "dishonest" in soc["hard_reject"].lower()
    r = snipe_reject_reason(t)
    assert r and "dishonest" in r.lower()
    ev = evaluate_snipe(t)
    assert not ev["eligible"]


def test_website_does_not_save_entry_trap():
    """Cashoty-class: real site + status X + empty desc → still hard reject."""
    t = _base(
        pumpfun={
            "twitter": "https://x.com/random/status/999",
            "website": "https://cashothy.fun",
            "description": "",
            "reply_count": 0,
        }
    )
    soc = analyze_snipe_social(t)
    assert soc["hard_reject"]
    assert "website alone does not save" in soc["hard_reject"].lower() or "entry" in soc[
        "hard_reject"
    ].lower() or "dishonest" in soc["hard_reject"].lower()

    avoid = analyze_avoid_flags(
        safety={"mint": t["tokenAddress"], "top_holders": []},
        pump=t["pumpfun"],
        mint=t["tokenAddress"],
    )
    assert "entry_trap_social" in (avoid.get("flags") or [])
    assert avoid.get("hard_avoid") is True
    hard, _ = is_hard_avoid({"avoid": avoid})
    assert hard
    assert "entry_trap_social" in HARD_AVOID_FLAGS


def test_media_website_hard_reject():
    t = _base(
        pumpfun={
            "website": "https://www.instagram.com/reel/abc123",
            "description": "check ig",
            "reply_count": 1,
        }
    )
    soc = analyze_snipe_social(t)
    assert soc["hard_reject"]
    assert "website" in soc["hard_reject"].lower() or "instagram" in soc["hard_reject"].lower()
    assert snipe_reject_reason(t)


def test_own_twitter_boosts_score():
    bare = _base(
        mcap_usd=12_000,
        ath_mcap=12_500,
        age_minutes=40,
        pumpfun={"description": "ok", "reply_count": 3},
    )
    with_x = _base(
        mcap_usd=12_000,
        ath_mcap=12_500,
        age_minutes=40,
        pumpfun={
            "twitter": "https://x.com/MyCoolProject",
            "description": "ok",
            "reply_count": 3,
        },
    )
    for t in (bare, with_x):
        t.pop("snipeSocial", None)
    soc0 = analyze_snipe_social(bare)
    soc1 = analyze_snipe_social(with_x)
    assert soc1["score_delta"] > soc0["score_delta"]
    assert soc1["score_delta"] >= 10
    ev1 = evaluate_snipe(with_x)
    assert ev1["eligible"]
    assert ev1["snipe_social"]["has_real_social"]
    assert ev1["snipe_social"]["score_delta"] >= 10
    assert any("Own X" in w or "honest" in w.lower() for w in (ev1.get("why") or []))


def test_real_website_boosts():
    t = _base(
        pumpfun={
            "website": "https://myproject.xyz",
            "description": "project site",
            "reply_count": 2,
        }
    )
    soc = analyze_snipe_social(t)
    assert soc["hard_reject"] is None
    assert soc["score_delta"] >= 8
    assert soc["real_website"] is True


def test_silence_plus_red_flags_demotes():
    t = _base(
        pumpfun={"description": "", "reply_count": 0},
        avoid={"avoid": True, "flags": ["dead_book", "wash_buys"]},
        safety={"passed": True, "total_holders": 25, "top_holders": []},
    )
    soc = analyze_snipe_social(t)
    assert soc["hard_reject"] is None
    assert soc["score_delta"] < 0
    assert "silence_red_flags" in soc["flags"]
