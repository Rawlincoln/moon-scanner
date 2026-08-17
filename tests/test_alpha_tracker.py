"""Alpha Tracker scoring + format smoke tests."""

from __future__ import annotations

from services.alpha_tracker import _score_pro, format_alpha_buy_telegram


def test_score_pro_buy_band():
    score, label, why = _score_pro(
        mcap=12_000,
        liq=9_000,
        age_min=18,
        buys5=40,
        sells5=20,
        group_count=3,
        boost=120,
        avoid={"flags": []},
        social={"highlight": "community heat"},
        snipe_soc={"score_delta": 4, "flags": []},
        honeypot=False,
        ath_ret=0.85,
        sources=["padre_alpha_tracker", "dex_boost"],
    )
    assert score >= 68
    assert label == "BUY"
    assert why


def test_score_pro_skip_honeypot():
    score, label, _ = _score_pro(
        mcap=10_000,
        liq=5_000,
        age_min=10,
        buys5=20,
        sells5=5,
        group_count=5,
        boost=200,
        avoid={},
        social={},
        snipe_soc={},
        honeypot=True,
        ath_ret=0.9,
        sources=["dex_boost"],
    )
    assert label == "SKIP"
    assert score == 0


def test_format_alpha_buy_telegram():
    msg = format_alpha_buy_telegram(
        {
            "tokenAddress": "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f",
            "symbol": "TEST",
            "name": "Test Coin",
            "mcap_usd": 15000,
            "age_minutes": 12,
            "alpha": {
                "score": 78,
                "group_count": 2,
                "groups": ["dex_boost", "alpha"],
                "why": ["2 group heat", "mcap in band"],
                "sources": ["dex_boost"],
            },
            "padre_url": "https://trade.padre.gg/trade/solana/abc",
        }
    )
    assert "ALPHA BUY" in msg
    assert "TEST" in msg
    assert "PLAN" in msg
