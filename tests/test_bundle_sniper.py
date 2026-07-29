"""Jito-style bundle / sniper thresholds (2026 trader bands)."""

from services.bundle_sniper import (
    BUNDLE_PCT_CRITICAL,
    BUNDLE_PCT_HARD_SKIP,
    BUNDLE_PCT_HIGH,
    BUNDLE_PCT_NOISE,
    analyze_bundle_and_snipers,
)


def test_thresholds_constants():
    assert BUNDLE_PCT_NOISE == 5.0
    assert BUNDLE_PCT_HIGH == 12.0
    assert BUNDLE_PCT_HARD_SKIP == 20.0
    assert BUNDLE_PCT_CRITICAL == 25.0


def test_clean_organic_book():
    r = analyze_bundle_and_snipers(
        {
            "top_holders": [
                {"pct": 75, "owner": "pool"},
                {"pct": 2.0, "owner": "a"},
                {"pct": 1.5, "owner": "b"},
            ],
            "total_holders": 200,
        },
        {},
        {},
    )
    assert r["bundled_pct"] < 5
    assert r["hard_reject"] is False
    assert r["overall"] in ("clean", "low")
    assert r.get("decision") in ("acceptable", None) or r["decision"] == "acceptable"


def test_classic_multi_wallet_similar_sizes_high_bundled():
    r = analyze_bundle_and_snipers(
        {
            "top_holders": [
                {"pct": 50, "owner": "pool"},
                {"pct": 4.1, "owner": "w1"},
                {"pct": 4.0, "owner": "w2"},
                {"pct": 3.9, "owner": "w3"},
                {"pct": 4.05, "owner": "w4"},
                {"pct": 3.95, "owner": "w5"},
                {"pct": 4.0, "owner": "w6"},
            ],
            "total_holders": 30,
        },
        {},
        {},
    )
    assert r["bundled_pct"] >= 12
    assert r["hard_reject"] is True
    assert r["overall"] in ("high", "critical")


def test_insider_graph_critical():
    r = analyze_bundle_and_snipers(
        {
            "top_holders": [
                {"pct": 40, "owner": "pool"},
                {"pct": 4.2, "owner": "w1", "insider": True},
                {"pct": 4.0, "owner": "w2", "insider": True},
            ]
            + [{"pct": 3.5, "owner": f"w{i}"} for i in range(8)],
            "insider_detected": True,
            "insider_networks": 2,
            "total_holders": 25,
        },
        {},
        {},
        age_minutes=1.2,
        mcap_usd=18_000,
    )
    assert r["hard_reject"] is True
    assert r["overall"] in ("high", "critical")


def test_reported_bundled_pct_from_risk_text():
    r = analyze_bundle_and_snipers(
        {
            "risks": [
                {
                    "name": "Bundled",
                    "level": "danger",
                    "description": "Bundled 22% of supply",
                    "value": "22%",
                }
            ],
            "top_holders": [],
        }
    )
    assert r.get("bundled_pct_reported") == 22
    assert r["hard_reject"] is True


def test_whale_sniper_max_wallet():
    r = analyze_bundle_and_snipers(
        {
            "top_holders": [
                {"pct": 70, "owner": "pool"},
                {"pct": 28, "owner": "sniper1"},
                {"pct": 1.5, "owner": "x"},
            ],
            "total_holders": 40,
        }
    )
    assert r["snipers"]["max_wallet_pct"] >= 22
    assert r["snipers"]["risk_level"] in ("high", "critical")
