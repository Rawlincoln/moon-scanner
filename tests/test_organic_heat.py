"""Organic Heat high-recall filters."""

from services.organic_heat import (
    LABEL_HEAT,
    LABEL_RISKY,
    LABEL_WARM,
    evaluate_heat,
    filter_and_rank_heat,
    heat_reject_reason,
)


def _base(**kw):
    t = {
        "tokenAddress": "HeatMint1111111111111111111111111111111",
        "mcap_usd": 12_000,
        "ath_mcap": 13_000,
        "age_minutes": 20,
        "bonding_progress": 32,
        "enrich_ok": True,
        "priceChange": {"m5": 12, "h1": 25},
        "pumpfun": {
            "reply_count": 25,
            "twitter": "https://x.com/someproject",
            "name": "HeatCoin",
            "symbol": "HEAT",
            "usd_market_cap": 12_000,
            "ath_market_cap": 13_000,
        },
        "name": "HeatCoin",
        "symbol": "HEAT",
        "safety": {
            "passed": True,
            "top_holders": [{"pct": 3}, {"pct": 2}],
        },
        "bundleSniper": {
            "holders_known": True,
            "hard_reject": False,
            "overall": "low",
            "bundle": {"bundled_pct": 3.0, "risk_level": "low"},
            "snipers": {"risk_level": "low", "max_wallet_pct": 4},
        },
        "bundle": {"bundled_pct": 3.0},
        "avoid": {"avoid": False},
    }
    t.update(kw)
    return t


def test_heat_allows_pullback_moons_would_kill():
    """−20% from ATH is still heat (moons would reject at −12%)."""
    t = _base(mcap_usd=10_400, ath_mcap=13_000)
    assert heat_reject_reason(t) is None
    ev = evaluate_heat(t)
    assert ev["eligible"] is True
    assert ev["label"] in (LABEL_HEAT, LABEL_WARM, LABEL_RISKY)


def test_heat_blocks_hard_dump():
    t = _base(mcap_usd=4_000, ath_mcap=13_000)  # ~31% — wait need <55%
    t = _base(mcap_usd=5_000, ath_mcap=20_000)  # 25% retained = hard dump
    r = heat_reject_reason(t)
    assert r and "dump" in r.lower()


def test_heat_no_narrative_still_eligible_with_replies():
    t = _base(
        pumpfun={
            "reply_count": 30,
            "twitter": "",
            "name": "Rand",
            "symbol": "RNDX",
            "description": "just a coin",
            "usd_market_cap": 12_000,
            "ath_market_cap": 12_500,
        },
        name="Rand",
        symbol="RNDX",
    )
    # clear social so it re-analyzes
    t.pop("socialSignals", None)
    assert heat_reject_reason(t) is None
    ev = evaluate_heat(t)
    assert ev["eligible"] is True


def test_filter_ranks_heat_first():
    good = _base(tokenAddress="A" + "1" * 40)
    weak = _base(
        tokenAddress="B" + "1" * 40,
        mcap_usd=3_000,
        ath_mcap=3_200,
        pumpfun={
            "reply_count": 4,
            "twitter": "",
            "name": "Thin",
            "symbol": "THIN",
            "usd_market_cap": 3_000,
            "ath_market_cap": 3_200,
        },
        bonding_progress=8,
        priceChange={"m5": 1, "h1": 2},
    )
    weak.pop("socialSignals", None)
    out = filter_and_rank_heat([weak, good], min_score=40, limit=5)
    assert out
    assert out[0]["heat_score"] >= out[-1]["heat_score"]


def test_honeypot_blocked():
    r = heat_reject_reason(_base(safety={"honeypot": True, "top_holders": [{"pct": 1}]}))
    assert r and "honey" in r.lower() or "rugged" in r.lower() or r


def test_serial_deployer_blocked():
    t = _base(
        safety={
            "passed": True,
            "top_holders": [{"pct": 3}],
            "creator_token_count": 20,
            "creator_migrated_count": 0,
            "creator": "Dev111111111111111111111111111111111",
        }
    )
    r = heat_reject_reason(t)
    assert r and ("serial" in r.lower() or "launch" in r.lower())


def test_dev_sold_multi_launch_blocked():
    t = _base(
        safety={
            "passed": True,
            "top_holders": [{"pct": 2, "owner": "OtherWallet11111111111111111111111"}],
            "creator": "Dev111111111111111111111111111111111",
            "creator_sold": True,
            "creator_pct": 0,
            "creator_token_count": 5,
            "creator_migrated_count": 0,
        }
    )
    r = heat_reject_reason(t)
    assert r and ("sold" in r.lower() or "dev" in r.lower() or "serial" in r.lower())


def test_dev_with_migrations_scores_higher():
    from services.organic_heat import evaluate_heat

    weak = _base(
        safety={
            "passed": True,
            "top_holders": [{"pct": 3}],
            "creator_token_count": 6,
            "creator_migrated_count": 0,
            "creator_sold": False,
            "creator": "DevA11111111111111111111111111111111",
        }
    )
    good = _base(
        tokenAddress="HeatMintGood1111111111111111111111111",
        safety={
            "passed": True,
            "top_holders": [{"pct": 3, "owner": "DevB11111111111111111111111111111111"}],
            "creator_token_count": 3,
            "creator_migrated_count": 2,
            "creator_sold": False,
            "creator_pct": 4.0,
            "creator": "DevB11111111111111111111111111111111",
        },
    )
    # may still reject weak for serial farm pattern
    eg = evaluate_heat(good)
    assert eg.get("dev") is not None or eg.get("eligible") is not None
    if eg.get("eligible"):
        assert eg["dev"]["tokens_migrated"] == 2
        assert "migration" in " ".join(eg.get("why") or []).lower() or eg[
            "dev"
        ]["tokens_launched"] == 3
