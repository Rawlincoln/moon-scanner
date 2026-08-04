"""Ticker uniqueness — novel brand vs reused copycat."""

from pathlib import Path

from services.ticker_registry import (
    TickerRegistry,
    analyze_ticker_uniqueness,
    normalize_symbol,
)


def test_normalize():
    assert normalize_symbol("$doge") == "DOGE"
    assert normalize_symbol("  elon ") == "ELON"


def test_unique_first_sighting(tmp_path: Path):
    reg = TickerRegistry(tmp_path / "t.db")
    a = reg.record("ZORBLIX", "MintUniqueAAAA11111111111111111111111", name="Z")
    assert a["unique"] is True
    assert a["status"] == "unique"
    assert a["score_boost"] > 0
    assert a["prior_mints"] == 0


def test_reused_same_symbol_different_mint(tmp_path: Path):
    reg = TickerRegistry(tmp_path / "t2.db")
    reg.record("PEPU", "MintOne1111111111111111111111111111111")
    b = reg.record("PEPU", "MintTwo2222222222222222222222222222222")
    assert b["unique"] is False
    assert b["prior_mints"] >= 1
    assert b["score_boost"] <= 0


def test_hot_meta_not_unique():
    u = analyze_ticker_uniqueness("ELON", "MintX11111111111111111111111111111111", prior_mints=0)
    assert u["unique"] is False
    assert u["is_hot_meta"] is True
    assert u["status"] == "hot_meta"


def test_heavily_reused():
    u = analyze_ticker_uniqueness("FOMO", "MintY11111111111111111111111111111111", prior_mints=6)
    assert u["status"] == "heavily_reused"
    assert u["score_boost"] < 0
