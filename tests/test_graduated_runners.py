"""Graduated / large runner filters."""

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
        "mcap_usd": 2_500_000,
        "ath_mcap": 3_000_000,
        "age_minutes": 400,
        "bonding_progress": 100,
        "complete": True,
        "enrich_ok": True,
        "priceChange": {"m5": 3, "h1": 8, "h6": 15, "h24": 40},
        "pumpfun": {
            "complete": True,
            "reply_count": 40,
            "usd_market_cap": 2_500_000,
            "ath_market_cap": 3_000_000,
            "twitter": "https://x.com/proj",
        },
        "safety": {
            "passed": True,
            "on_bonding_curve": False,
            "top_holders": [{"pct": 2}, {"pct": 1.5}],
        },
        "avoid": {"avoid": False},
    }
    t.update(kw)
    return t


def test_early_small_rejected():
    r = graduated_reject_reason(_base(mcap_usd=20_000, ath_mcap=22_000, complete=False))
    assert r


def test_runner_near_ath():
    t = _base(mcap_usd=2_800_000, ath_mcap=3_000_000)
    assert graduated_reject_reason(t) is None
    ev = evaluate_graduated(t)
    assert ev["eligible"]
    assert ev["label"] in (LABEL_RUNNER, LABEL_DIP, LABEL_WATCH)


def test_dip_zone():
    t = _base(mcap_usd=1_200_000, ath_mcap=3_000_000)  # 40% ATH
    assert graduated_reject_reason(t) is None
    ev = evaluate_graduated(t)
    assert ev["eligible"]
    # typically DIP
    assert ev["label"] in (LABEL_DIP, LABEL_WATCH, LABEL_RUNNER)


def test_dead_dump_blocked():
    t = _base(mcap_usd=200_000, ath_mcap=3_000_000)  # ~6.7% ATH
    r = graduated_reject_reason(t)
    assert r and "dump" in r.lower()


def test_cate_like_profile():
    """CATE-like: multi-M graduated, ~36% ATH — should be DIP/WATCH not skip."""
    t = _base(
        tokenAddress="Ai66LHZG9MCzg1WKdawwqduVAXpNDUuV8M3uyq5ppump",
        mcap_usd=31_000_000,
        ath_mcap=87_000_000,
        age_minutes=12_000,
        complete=True,
        priceChange={"m5": -7, "h1": -15, "h6": 11, "h24": -33},
    )
    assert graduated_reject_reason(t) is None
    ev = evaluate_graduated(t)
    assert ev["eligible"] is True
    assert ev["label"] in (LABEL_DIP, LABEL_WATCH, LABEL_RUNNER)


def test_flash_tiktok_like_early_mega():
    """Just-graduated mega ~$28M in <1h should be eligible RUNNER/DIP."""
    t = _base(
        mcap_usd=28_000_000,
        ath_mcap=28_100_000,
        age_minutes=30,
        complete=True,
        priceChange={"m5": 10, "h1": 80, "h6": 200, "h24": 500},
        safety={
            "passed": True,
            "on_bonding_curve": False,
            "top_holders": [{"pct": 2}],
            "error": False,
        },
        enrich_ok=True,
    )
    assert graduated_reject_reason(t) is None
    ev = evaluate_graduated(t)
    assert ev["eligible"] is True
