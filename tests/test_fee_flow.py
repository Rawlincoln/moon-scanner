"""Fee/volume quality vs flash sniper wars."""

from services.fee_flow import (
    analyze_fee_flow,
    fee_flow_gate,
    format_fee_telegram,
)


def test_organic_two_way_volume():
    ff = analyze_fee_flow(
        {
            "mcap_usd": 20_000,
            "age_minutes": 25,
            "market": {
                "volume": {"m5": 4_000, "h1": 25_000, "h24": 40_000},
                "txns": {"m5": {"buys": 35, "sells": 18}, "h1": {"buys": 120, "sells": 60}},
                "marketCap": 20_000,
            },
        }
    )
    assert ff["quality"] in ("organic", "mixed")
    assert ff["hard_reject"] is False
    assert ff["two_way"] is True
    ok, _ = fee_flow_gate(ff)
    assert ok is True


def test_flash_volume_young_hard():
    ff = analyze_fee_flow(
        {
            "mcap_usd": 12_000,
            "age_minutes": 1.5,
            "market": {
                "volume": {"m5": 50_000, "h1": 50_000},
                "txns": {"m5": {"buys": 200, "sells": 5}},
                "marketCap": 12_000,
            },
        }
    )
    assert ff["hard_reject"] is True or "flash_fees" in ff["flags"]
    ok, why = fee_flow_gate(ff)
    assert ok is False
    assert why


def test_wash_one_way():
    ff = analyze_fee_flow(
        {
            "mcap_usd": 15_000,
            "age_minutes": 20,
            "market": {
                "volume": {"m5": 8_000, "h1": 20_000},
                "txns": {"m5": {"buys": 80, "sells": 1}},
            },
        }
    )
    assert "wash" in ff["quality"] or "wash_fees" in ff["flags"] or ff["hard_reject"]


def test_format_flow():
    ff = analyze_fee_flow(
        {
            "mcap_usd": 18_000,
            "age_minutes": 30,
            "market": {
                "volume": {"m5": 3_000, "h1": 15_000},
                "txns": {"m5": {"buys": 30, "sells": 14}},
            },
        }
    )
    s = format_fee_telegram(ff)
    assert "FLOW" in s
