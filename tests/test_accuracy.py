"""Accuracy helpers: holders, ATH merge, snipe gates."""

from pathlib import Path

from services.accuracy import holders_known, merge_ath_into_token
from services.snipe_outcomes import SnipeOutcomes


def test_holders_known_true_false():
    assert holders_known(
        {
            "safety": {"top_holders": [{"pct": 1}]},
            "bundleSniper": {"holders_known": True},
        }
    )
    assert not holders_known({"safety": {}, "bundleSniper": {"holders_known": False}})
    assert not holders_known({})


def test_merge_ath_uses_live_as_floor():
    t = {
        "mcap_usd": 12_000,
        "ath_mcap": 8_000,
        "pumpfun": {"ath_market_cap": 9_000, "usd_market_cap": 12_000},
    }
    ath = merge_ath_into_token(t)
    assert ath >= 12_000
    assert t["ath_mcap"] >= 12_000


def test_snipe_gates_tighten_on_dump(tmp_path: Path):
    g = SnipeOutcomes.suggested_gates_from_stats(
        overall={"n": 20, "dump_rate_pct": 75, "win_rate_pct": 10}
    )
    assert g["adapted"] is True
    assert g["min_score"] > 55


def test_snipe_record_roundtrip(tmp_path: Path):
    o = SnipeOutcomes(tmp_path / "s.db")
    n = o.record_shown(
        [
            {
                "tokenAddress": "SnipeMintAAAA111111111111111111111111",
                "symbol": "SN",
                "mcap_usd": 7000,
                "ath_mcap": 7200,
                "snipe_label": "SNIPE",
                "snipe_score": 80,
                "confidence": 75,
                "snipe": {"label": "SNIPE", "snipe_score": 80},
            }
        ]
    )
    assert n == 1
    s = o.summary()
    assert s["total_recs"] == 1
    assert "gates" in s
