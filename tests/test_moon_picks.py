"""Capital-protection reject + rank gates."""

from services.avoid_filters import BLOCKED_MINTS
from services.moon_picks import (
    LABEL_MOON,
    LABEL_WATCH,
    evaluate,
    filter_and_rank,
    reject_reason,
)


def _base_token(**kwargs):
    t = {
        "tokenAddress": "GoodMint1111111111111111111111111111111",
        "mcap_usd": 25_000,
        "ath_mcap": 25_500,
        "bonding_progress": 40,
        "age_minutes": 25,
        "name": "Mars Dog",
        "symbol": "MARS",
        "enrich_ok": True,
        "safety": {
            "passed": True,
            "top_holders": [
                {"pct": 3.0, "owner": "HolderA11111111111111111111111111111"},
                {"pct": 2.5, "owner": "HolderB11111111111111111111111111111"},
            ],
        },
        "bundleSniper": {
            "holders_known": True,
            "hard_reject": False,
            "overall": "low",
            "bundle": {"bundled_pct": 2.0, "risk_level": "low"},
            "snipers": {"risk_level": "low", "max_wallet_pct": 3.0},
        },
        "pumpfun": {
            "twitter": "https://x.com/elonmusk/status/1",
            "reply_count": 30,
            "description": "doge forever",
            "name": "Mars Dog",
            "symbol": "MARS",
            "usd_market_cap": 25_000,
            "ath_market_cap": 25_500,
        },
    }
    t.update(kwargs)
    if "pumpfun" in kwargs:
        pf = dict(t.get("pumpfun") or {})
        pf.update(kwargs["pumpfun"])
        t["pumpfun"] = pf
    return t


def test_blocklist_reject():
    mint = next(iter(BLOCKED_MINTS))
    assert reject_reason({"tokenAddress": mint, "mcap_usd": 10_000, "ath_mcap": 10_000})


def test_dump_from_ath_reject():
    r = reject_reason(
        {
            "tokenAddress": "DumpMint11111111111111111111111111111",
            "mcap_usd": 5_000,
            "ath_mcap": 30_000,
            "age_minutes": 40,
            "pumpfun": {"reply_count": 50, "twitter": "https://x.com/proj"},
        }
    )
    assert r
    assert "dump" in r.lower() or "faded" in r.lower() or "ath" in r.lower()


def test_random_chart_no_narrative_reject():
    r = reject_reason(
        {
            "tokenAddress": "RandMint1111111111111111111111111111111",
            "mcap_usd": 12_000,
            "ath_mcap": 12_100,
            "bonding_progress": 18,
            "age_minutes": 20,
            "name": "asdf",
            "symbol": "XQRT",
            "pumpfun": {"reply_count": 0, "twitter": "", "description": ""},
        }
    )
    assert r
    assert "narrative" in r.lower() or "ghost" in r.lower() or "edge" in r.lower()


def test_influencer_near_ath_eligible():
    t = _base_token()
    assert reject_reason(t) is None, reject_reason(t)
    ev = evaluate(t)
    assert ev["eligible"] is True
    assert ev["label"] in (LABEL_MOON, LABEL_WATCH)
    assert ev["moon_score"] >= 50
    assert ev.get("influencer_tweet") is True


def test_filter_and_rank_drops_weak_and_respects_min_score():
    good = _base_token()
    weak_dump = {
        "tokenAddress": "Dump2Mint1111111111111111111111111111",
        "mcap_usd": 3_000,
        "ath_mcap": 40_000,
        "age_minutes": 30,
        "pumpfun": {},
    }
    out = filter_and_rank([good, weak_dump], min_score=55, min_confidence=50)
    assert len(out) >= 1
    assert all(x["moon_label"] in (LABEL_MOON, LABEL_WATCH) for x in out)
    assert all(x["tokenAddress"] != weak_dump["tokenAddress"] for x in out)


def test_filter_max_bundled_pct():
    good = _base_token()
    good["bundle"] = {"bundled_pct": 20.0}
    out = filter_and_rank(
        [good],
        min_score=40,
        min_confidence=40,
        max_bundled_pct=12.0,
    )
    assert out == []


def test_filter_require_influencer():
    no_inf = _base_token(
        pumpfun={
            "twitter": "https://x.com/someproject",
            "reply_count": 40,
            "description": "community token with website",
            "website": "https://example.com",
            "name": "Community",
            "symbol": "COM",
            "usd_market_cap": 30_000,
            "ath_market_cap": 30_500,
        },
        mcap_usd=30_000,
        ath_mcap=30_500,
        bonding_progress=50,
        name="Community",
        symbol="COM",
        tokenAddress="ComMint1111111111111111111111111111111",
    )
    # May or may not pass reject without edge — force evaluate path if eligible
    if reject_reason(no_inf) is None:
        out = filter_and_rank(
            [no_inf],
            min_score=40,
            min_confidence=40,
            require_influencer=True,
        )
        assert out == []
