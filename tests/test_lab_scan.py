"""Fast lab multi-source merge + uniqueness of API shape."""

from services.lab_scan import _merge_market


def test_merge_prefers_pump_on_curve():
    m = _merge_market(
        {"usd_market_cap": 12_000, "ath_market_cap": 15_000, "complete": False},
        {
            "pair": {
                "marketCap": 11_500,
                "liquidity": {"usd": 4_000},
                "volume": {"h24": 9_000},
                "baseToken": {"symbol": "X", "name": "Xcoin"},
            },
            "pair_count": 2,
        },
    )
    assert m["marketCap"] == 12_000
    assert m["liquidity_usd"] == 4_000
    assert m["pair_count"] == 2
    assert m["sources_mcap"]["pump"] == 12_000
    assert m["sources_mcap"]["dex"] == 11_500


def test_merge_prefers_dex_when_complete():
    m = _merge_market(
        {"usd_market_cap": 50_000, "complete": True},
        {"pair": {"marketCap": 52_000, "liquidity": {"usd": 20_000}}, "pair_count": 1},
    )
    assert m["marketCap"] == 52_000
