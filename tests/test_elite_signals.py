"""Elite copy-trade signals + roster."""

from __future__ import annotations

from services.elite_signals import (
    LABEL_COPY,
    LABEL_ELITE,
    LABEL_SKIP,
    evaluate_elite,
    elite_reject_reason,
)
from services.elite_traders import (
    credit_wallet,
    get_elite_roster,
    load_seed_traders,
    match_elites_on_token,
)


def test_roster_has_up_to_20():
    seeds = load_seed_traders(force=True)
    assert len(seeds) >= 10
    roster = get_elite_roster(limit=20)
    assert 1 <= len(roster) <= 20
    assert all(t.get("address") for t in roster)


def test_no_elite_rejects():
    t = {
        "tokenAddress": "EliteMint1111111111111111111111111111111",
        "mcap_usd": 12_000,
        "ath_mcap": 12_500,
        "age_minutes": 20,
        "enrich_ok": True,
        "safety": {"passed": True, "top_holders": [{"pct": 2, "owner": "SomeRand111"}]},
        "avoid": {"avoid": False},
        "bundleSniper": {
            "hard_reject": False,
            "overall": "low",
            "bundle": {"bundled_pct": 2},
            "snipers": {"risk_level": "low"},
        },
    }
    r = elite_reject_reason(t)
    assert r and "elite" in r.lower()


def test_elite_hit_and_evaluate():
    seeds = load_seed_traders(force=True)
    addr = seeds[0]["address"]
    credit_wallet(addr, points=5, reason="win", mint="WinMint111")
    t = {
        "tokenAddress": "EliteMint2222222222222222222222222222222",
        "mcap_usd": 15_000,
        "ath_mcap": 15_500,
        "age_minutes": 25,
        "bonding_progress": 30,
        "enrich_ok": True,
        "priceChange": {"m5": 8},
        "safety": {
            "passed": True,
            "total_holders": 100,
            "top_holders": [
                {"pct": 2.5, "owner": addr},
                {"pct": 1.5, "owner": "OtherHolder1111111111111111111111111"},
            ],
        },
        "avoid": {"avoid": False, "flags": []},
        "bundleSniper": {
            "hard_reject": False,
            "overall": "low",
            "holders_known": True,
            "bundle": {"bundled_pct": 2.0, "risk_level": "low"},
            "snipers": {"risk_level": "low", "max_wallet_pct": 3},
        },
        "bundle": {"bundled_pct": 2.0},
        "socialSignals": {"replies": 20, "has_edge": False},
        "pumpfun": {"reply_count": 20, "description": "ok"},
    }
    hits = match_elites_on_token(t)
    assert hits
    assert elite_reject_reason(t) is None
    ev = evaluate_elite(t)
    assert ev["eligible"] is True
    assert ev["label"] in (LABEL_ELITE, LABEL_COPY, "WATCH")
    assert ev["elite_count"] >= 1
    assert ev["label"] != LABEL_SKIP
