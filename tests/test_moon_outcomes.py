"""Outcome classification + adaptive gates."""

from pathlib import Path

from services.moon_outcomes import MoonOutcomes


def test_classify_win_and_dump():
    o = MoonOutcomes.__new__(MoonOutcomes)
    assert o._classify(10_000, 12_000, 22_000)[0] == "win_2x"
    assert o._classify(10_000, 12_000, 16_000)[0] == "win_1_5x"
    assert o._classify(10_000, 12_000, 9_000)[0] == "hold"
    assert o._classify(10_000, 12_000, 6_000)[0] == "dump"
    assert o._classify(10_000, 12_000, 4_000)[0] == "dump"


def test_suggested_gates_defaults_small_sample():
    g = MoonOutcomes.suggested_gates_from_stats(
        overall={"n": 2, "dump_rate_pct": 100, "win_rate_pct": 0},
        by_label={},
        by_influencer={},
        by_bundled={},
    )
    assert g["adapted"] is False
    assert g["min_score"] == 55
    assert g["min_confidence"] == 52
    assert g["max_bundled_pct"] == 12.0


def test_suggested_gates_tighten_on_high_dump():
    g = MoonOutcomes.suggested_gates_from_stats(
        overall={"n": 20, "dump_rate_pct": 70, "win_rate_pct": 15, "by_outcome": {}},
        by_label={
            "WATCH": {"n": 10, "dump_rate_pct": 80},
            "MOON": {"n": 5, "dump_rate_pct": 40},
        },
        by_influencer={
            "no": {"n": 12, "dump_rate_pct": 85},
            "yes": {"n": 5, "dump_rate_pct": 40},
        },
        by_bundled={
            "5_12": {"n": 8, "dump_rate_pct": 75},
            "12_20": {"n": 2, "dump_rate_pct": 50},
            "lt5": {"n": 5, "dump_rate_pct": 40},
            "ge20": {"n": 0},
        },
    )
    assert g["adapted"] is True
    assert g["min_score"] > 55
    assert g["min_confidence"] > 52
    assert g["max_bundled_pct"] <= 5.0
    assert g["require_influencer"] is True


def test_record_and_summary_roundtrip(tmp_path: Path):
    db = tmp_path / "out.db"
    o = MoonOutcomes(db)
    n = o.record_shown(
        [
            {
                "tokenAddress": "TestMintOutcomeAAAA111111111111111111",
                "symbol": "T",
                "name": "Test",
                "mcap_usd": 10_000,
                "ath_mcap": 10_500,
                "moon_label": "WATCH",
                "moon_score": 60,
                "confidence": 55,
                "moon": {"label": "WATCH", "narrative": "test"},
            }
        ]
    )
    assert n == 1
    # Dedupe within 30m
    assert o.record_shown(
        [
            {
                "tokenAddress": "TestMintOutcomeAAAA111111111111111111",
                "symbol": "T",
                "mcap_usd": 10_000,
                "moon_label": "WATCH",
                "moon_score": 60,
                "confidence": 55,
            }
        ]
    ) == 0
    s = o.summary()
    assert s["total_recs"] == 1
    assert s["active"] == 1
    assert "gates" in s
