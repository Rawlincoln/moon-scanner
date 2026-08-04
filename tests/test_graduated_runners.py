"""Graduated early runners — under $1M, organic books."""

from services.graduated_runners import (
    LABEL_DIP,
    LABEL_RUNNER,
    LABEL_WATCH,
    evaluate_graduated,
    graduated_reject_reason,
)


def _base(**kw):
    t = {
        "tokenAddress": "GradMint1111111111111111111111111111111",
        "mcap_usd": 180_000,
        "ath_mcap": 200_000,
        "age_minutes": 90,
        "bonding_progress": 100,
        "complete": True,
        "enrich_ok": True,
        "priceChange": {"m5": 3, "h1": 8, "h6": 15, "h24": 40},
        "pumpfun": {
            "complete": True,
            "reply_count": 40,
            "usd_market_cap": 180_000,
            "ath_market_cap": 200_000,
            "twitter": "https://x.com/proj",
        },
        "safety": {
            "passed": True,
            "on_bonding_curve": False,
            "top_holders": [{"pct": 2}, {"pct": 1.5}],
        },
        "bundleSniper": {
            "hard_reject": False,
            "overall": "low",
            "holders_known": True,
            "bundle": {"bundled_pct": 3.0, "risk_level": "low"},
            "snipers": {"risk_level": "low", "max_wallet_pct": 4},
        },
        "avoid": {"avoid": False},
    }
    t.update(kw)
    return t


def test_early_small_rejected():
    r = graduated_reject_reason(_base(mcap_usd=20_000, ath_mcap=22_000, complete=False))
    assert r


def test_millions_rejected():
    r = graduated_reject_reason(
        _base(mcap_usd=5_000_000, ath_mcap=6_000_000, age_minutes=400)
    )
    assert r and ("million" in r.lower() or "large" in r.lower() or "1m" in r.lower())


def test_runner_near_ath_under_1m():
    t = _base(mcap_usd=250_000, ath_mcap=280_000)
    assert graduated_reject_reason(t) is None
    ev = evaluate_graduated(t)
    assert ev["eligible"]
    assert ev["label"] in (LABEL_RUNNER, LABEL_DIP, LABEL_WATCH)


def test_dip_zone_under_1m():
    t = _base(mcap_usd=120_000, ath_mcap=300_000)  # 40% ATH
    assert graduated_reject_reason(t) is None
    ev = evaluate_graduated(t)
    assert ev["eligible"]
    assert ev["label"] in (LABEL_DIP, LABEL_WATCH, LABEL_RUNNER)


def test_dead_dump_blocked():
    t = _base(mcap_usd=55_000, ath_mcap=400_000)  # ~14% ATH
    r = graduated_reject_reason(t)
    assert r and "dump" in r.lower()


def test_sniper_farm_blocked():
    t = _base(
        bundleSniper={
            "hard_reject": True,
            "overall": "critical",
            "summary": "sniper critical",
            "bundle": {"bundled_pct": 18.0},
            "snipers": {"risk_level": "critical"},
        }
    )
    r = graduated_reject_reason(t)
    assert r and ("sniper" in r.lower() or "bundle" in r.lower() or "organic" in r.lower())


def test_organic_low_bundle_ok():
    t = _base(
        mcap_usd=150_000,
        ath_mcap=160_000,
        bundleSniper={
            "hard_reject": False,
            "overall": "low",
            "bundle": {"bundled_pct": 4.0},
            "snipers": {"risk_level": "low"},
        },
    )
    assert graduated_reject_reason(t) is None
    ev = evaluate_graduated(t)
    assert ev["eligible"] is True


def test_tiktok_millions_blocked():
    t = _base(
        mcap_usd=28_000_000,
        ath_mcap=28_100_000,
        age_minutes=30,
        complete=True,
    )
    r = graduated_reject_reason(t)
    assert r is not None
