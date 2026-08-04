"""Deployer rug history + serial farm gates."""

from services.dev_risk import (
    analyze_creator_history,
    dev_risk_gate,
    format_dev_telegram,
)


def test_clean_light_history():
    d = analyze_creator_history(
        {
            "creator": "CleanDev111111111111111111111111111111",
            "creator_token_count": 1,
            "creator_tokens": [{"mint": "a", "marketCap": 2_000}],
            "creator_sold": False,
        }
    )
    assert d["hard_reject"] is False
    assert d["risk_level"] in ("low", "unknown", "medium")
    ok, _ = dev_risk_gate(d)
    assert ok is True


def test_serial_zero_migrate_hard():
    rows = [{"mint": f"m{i}", "marketCap": 500} for i in range(10)]
    d = analyze_creator_history(
        {
            "creator": "FarmDev1111111111111111111111111111111",
            "creator_token_count": 10,
            "creator_migrated_count": 0,
            "creator_tokens": rows,
        }
    )
    assert d["hard_reject"] is True
    assert d["tokens_launched"] >= 8
    ok, why = dev_risk_gate(d)
    assert ok is False
    assert why


def test_prior_rugs_hard():
    rows = [
        {
            "mint": "rug1",
            "rugged": True,
            "marketCap": 100,
            "ath_market_cap": 20_000,
        },
        {
            "mint": "rug2",
            "marketCap": 500,
            "ath_market_cap": 25_000,  # dead from ATH
        },
        {
            "mint": "rug3",
            "marketCap": 200,
            "ath_market_cap": 18_000,
        },
    ]
    d = analyze_creator_history(
        {
            "creator": "RugDev11111111111111111111111111111111",
            "creator_token_count": 3,
            "creator_tokens": rows,
        }
    )
    assert d["prior_rugs"] >= 2
    assert d["hard_reject"] is True
    ok, why = dev_risk_gate(d)
    assert ok is False
    assert "rug" in (why or "").lower() or "prior" in (why or "").lower()


def test_format_dev_telegram():
    d = analyze_creator_history(
        {
            "creator": "Abcdefghijklmnopqrstuvwxyz1234567890ab",
            "creator_token_count": 4,
            "creator_migrated_count": 1,
            "creator_tokens": [],
            "creator_sold": True,
            "creator_pct": 0,
        }
    )
    s = format_dev_telegram(d)
    assert "DEV" in s
    assert "launched" in s


def test_proven_migrator_and_prior_moons():
    rows = [
        {
            "mint": "moon1",
            "migrated": True,
            "ath_market_cap": 250_000,
            "marketCap": 80_000,
        },
        {
            "mint": "moon2",
            "complete": True,
            "ath_market_cap": 120_000,
            "marketCap": 40_000,
        },
        {
            "mint": "ok3",
            "migrated": True,
            "marketCap": 55_000,
            "ath_market_cap": 70_000,
        },
    ]
    d = analyze_creator_history(
        {
            "creator": "GoodDev1111111111111111111111111111111",
            "creator_token_count": 3,
            "creator_tokens": rows,
            "creator_sold": False,
        }
    )
    assert d["hard_reject"] is False
    assert d["prior_moons"] >= 2
    assert d["tokens_migrated"] >= 2
    assert d["proven_dev"] is True
    assert d["score_boost"] > 0
    assert d["priority_boost"] > 0
    s = format_dev_telegram(d)
    assert "PROVEN" in s or "ELITE" in s or "prior_moons" in s
