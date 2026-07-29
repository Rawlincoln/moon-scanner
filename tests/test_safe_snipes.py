"""Safe snipe 2× filter tests."""

from __future__ import annotations

from services.safe_snipes import (
    evaluate_snipe,
    filter_and_rank_snipes,
    snipe_reject_reason,
)


def _base(**kw):
    t = {
        "tokenAddress": "SafeSnipeMint1111111111111111111111111pump",
        "mcap_usd": 7000,
        "ath_mcap": 7200,
        "age_minutes": 12,
        "bonding_progress": 28,
        "priceChange": {"m5": 8, "h1": 15},
        "bundleSniper": {
            "hard_reject": False,
            "bundled_pct": 2.0,
            "bundle": {"bundled_pct": 2.0, "risk_level": "low"},
            "snipers": {"risk_level": "low", "max_wallet_pct": 4.0, "score": 10},
        },
        "bundle": {"bundled_pct": 2.0, "risk_level": "low"},
        "snipers": {"risk_level": "low", "max_wallet_pct": 4.0},
        "avoid": {"avoid": False},
        "socialSignals": {},
    }
    t.update(kw)
    return t


def test_reject_high_mcap():
    r = snipe_reject_reason(_base(mcap_usd=40_000, ath_mcap=42_000))
    assert r and "above" in r.lower()


def test_reject_dumped():
    r = snipe_reject_reason(_base(mcap_usd=5000, ath_mcap=20_000))
    assert r and ("faded" in r.lower() or "dump" in r.lower())


def test_reject_bundled():
    r = snipe_reject_reason(
        _base(
            bundleSniper={
                "hard_reject": False,
                "overall": "high",
                "bundle": {"bundled_pct": 18.0, "risk_level": "high"},
                "snipers": {"risk_level": "low", "max_wallet_pct": 5},
            },
            bundle={"bundled_pct": 18.0},
        )
    )
    assert r and "bundled" in r.lower()


def test_setup_allows_6_to_8_pct_bundle():
    """SETUP zone: 6–8% bundle is allowed (not hard-rejected)."""
    t = _base(
        bundleSniper={
            "hard_reject": True,
            "overall": "high",
            "summary": "Bundled ~7% · bundle high",
            "bundle": {"bundled_pct": 7.0, "risk_level": "high"},
            "snipers": {"risk_level": "medium", "max_wallet_pct": 6},
        },
        bundle={"bundled_pct": 7.0},
        snipers={"risk_level": "medium", "max_wallet_pct": 6},
    )
    assert snipe_reject_reason(t) is None
    ev = evaluate_snipe(t)
    assert ev["label"] in ("SETUP", "SNIPE", "SKIP")
    # Cannot be full SNIPE with 7% bundle
    assert ev["label"] != "SNIPE" or (ev.get("bundle_pct") or 7) <= 5


def test_eligible_sweet_spot():
    ev = evaluate_snipe(_base())
    assert ev["eligible"]
    assert ev["label"] in ("SNIPE", "SETUP")
    assert ev["target_2x_usd"] == 14000
    assert ev["plan"]["take_profit_2x_usd"] == 14000


def test_filter_ranks_snipe_first():
    good = _base(tokenAddress="a" + "1" * 40 + "pump")
    mid = _base(
        tokenAddress="b" + "1" * 40 + "pump",
        mcap_usd=14000,
        ath_mcap=14500,
        age_minutes=40,
    )
    out = filter_and_rank_snipes([mid, good], min_score=50, limit=5)
    assert out
    assert out[0]["snipe_score"] >= out[-1]["snipe_score"]
