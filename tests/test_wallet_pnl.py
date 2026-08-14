"""Wallet PnL helpers for FOMO KOL dropdown."""

from services.wallet_pnl import _empty_pnl, fmt_pnl_usd


def test_fmt_pnl_usd():
    assert fmt_pnl_usd(None) == "—"
    assert "+" in fmt_pnl_usd(1500)
    assert "-" in fmt_pnl_usd(-500)
    assert "M" in fmt_pnl_usd(2_500_000)


def test_empty_pnl_shape():
    e = _empty_pnl("Abc123", "Test")
    assert e["address"] == "Abc123"
    assert e["label"] == "Test"
    assert e["pnl_1d"] is None
    assert e["ok"] is False
