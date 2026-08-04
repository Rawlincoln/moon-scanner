"""Germanus-inspired cockpit + scan archive."""

from pathlib import Path

from services.cockpit import cockpit_delta, extract_cockpit, liquidity_drift_pct
from services.scan_archive import ScanArchive


def _sample_result(**kw):
    r = {
        "tokenAddress": "CockpitMint111111111111111111111111111",
        "chainId": "solana",
        "symbol": "TST",
        "name": "Test",
        "mcap_usd": 50_000,
        "analyzedAt": 1_700_000_000,
        "safety": {
            "mint_authority": None,
            "freeze_authority": None,
            "total_holders": 500,
            "top_holders": [
                {"pct": 4.5},
                {"pct": 3.0},
                {"pct": 2.0},
                {"pct": 1.5},
                {"pct": 1.0},
            ],
            "lp_locked": True,
            "lp_locked_pct": 100,
        },
        "market": {
            "liquidity": {"usd": 20_000},
            "volume": {"h24": 80_000},
            "pair_count": 2,
        },
        "bundleSniper": {
            "bundle": {"bundled_pct": 3.0},
            "snipers": {"risk_level": "low"},
        },
    }
    r.update(kw)
    return r


def test_extract_cockpit_facts():
    c = extract_cockpit(_sample_result())
    assert c["mint_authority"] == "revoked"
    assert c["freeze_authority"] == "revoked"
    assert c["liquidity_usd"] == 20_000
    assert c["top1_pct"] == 4.5
    assert c["top5_pct"] == 12.0
    assert c["holders"] == 500
    assert c["lp_status"] == "locked"
    assert c["coverage_pct"] >= 80
    assert c["philosophy"] == "facts_with_evidence_no_verdict"


def test_na_not_zero_filled():
    c = extract_cockpit(
        {
            "tokenAddress": "EmptyMint1111111111111111111111111111",
            "safety": {},
            "market": {},
        }
    )
    assert c["liquidity_usd"] is None
    assert c["top1_pct"] is None
    assert c["holders"] is None


def test_delta_and_drift():
    a = extract_cockpit(_sample_result())
    b = extract_cockpit(
        _sample_result(
            market={"liquidity": {"usd": 25_000}, "volume": {"h24": 80_000}, "pair_count": 2}
        )
    )
    d = cockpit_delta(a, b)
    assert d["has_prev"] is True
    assert "liquidity_usd" in d["changes"]
    drift = liquidity_drift_pct(20_000, 21_000)
    assert drift is not None and drift < 10
    assert liquidity_drift_pct(20_000, 30_000) == 50.0


def test_control_surface_gate():
    from services.cockpit import control_surface_gate

    ok, _ = control_surface_gate(
        {"mint_authority": "revoked", "freeze_authority": "revoked"}
    )
    assert ok is True
    bad, why = control_surface_gate(
        {"mint_authority": "present", "freeze_authority": "revoked"}
    )
    assert bad is False and "mint" in (why or "").lower()
    bad2, why2 = control_surface_gate(
        {"mint_authority": "revoked", "freeze_authority": "n/a"}
    )
    assert bad2 is False and "n/a" in (why2 or "").lower()


def test_format_cockpit_telegram():
    from services.cockpit import format_cockpit_telegram

    s = format_cockpit_telegram(
        {
            "mint_authority": "revoked",
            "freeze_authority": "revoked",
            "lp_status": "locked",
            "liquidity_usd": 12_000,
            "top1_pct": 3.2,
            "holders": 400,
            "bundled_pct": 2.0,
            "coverage_pct": 90,
        }
    )
    assert "LAB" in s
    assert "revoked" in s
    assert "mint" in s.lower()


def test_format_pick_includes_lab():
    from services.telegram_alerts import format_pick_message

    msg = format_pick_message(
        "moon",
        {
            "tokenAddress": "MintLabAuto1111111111111111111111111",
            "symbol": "LABX",
            "mcap_usd": 18_000,
            "age_minutes": 20,
            "moon_label": "MOON",
            "moon": {"why": ["edge"]},
            "safety": {
                "mint_authority": None,
                "freeze_authority": None,
                "total_holders": 100,
                "top_holders": [{"pct": 2.5}],
            },
            "market": {"liquidity": {"usd": 8_000}},
        },
    )
    assert "LAB" in msg or "mint" in msg.lower()
    assert "revoked" in msg.lower()


def test_archive_store_list_freshness(tmp_path: Path):
    arch = ScanArchive(tmp_path / "arch.db")
    r1 = _sample_result()
    s1 = arch.store(r1)
    assert s1["ok"] and s1["id"]
    assert arch.scan_count(r1["tokenAddress"]) == 1
    rows = arch.list_archive(filter_mode="all")
    assert len(rows) == 1
    assert rows[0]["cockpit"]["symbol"] == "TST"

    fresh, meta = arch.freshness_ok(r1["tokenAddress"], 20_500)
    assert fresh is True
    assert meta["drift_pct"] < 10

    stale, meta2 = arch.freshness_ok(r1["tokenAddress"], 40_000)
    assert stale is False

    arch.star(r1["tokenAddress"], symbol="TST")
    assert len(arch.watchlist()) == 1
    wl = arch.list_archive(filter_mode="watchlist")
    assert len(wl) == 1
