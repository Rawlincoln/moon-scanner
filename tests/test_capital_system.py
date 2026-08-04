"""Complete money system: sizing, gates, position mgmt helpers."""

from pathlib import Path

from services.capital import can_open_trade, enrich_plan_with_size, size_position
from services.money_plan import build_money_plan
from services.trade_journal import TradeJournal, reset_journal_singleton


def test_size_position_risk_math():
    s = size_position(entry_mcap=10_000, stop_pct=0.20, bankroll=1000, risk_pct=1.0)
    # risk $10, stop 20% → size $50
    assert s["risk_usd"] == 10.0
    assert s["size_usd"] == 50.0


def test_enrich_plan_has_sizing():
    plan = enrich_plan_with_size(
        "moon", {"mcap_usd": 12_000, "symbol": "X", "moon_label": "MOON"}
    )
    assert plan["sizing"]["size_usd"] > 0
    assert plan["size_usd"] == plan["sizing"]["size_usd"]


def test_gate_max_open(tmp_path: Path):
    reset_journal_singleton()
    j = TradeJournal(tmp_path / "cap.db")
    # open two trades
    for i, mint in enumerate(
        ["MintCapAAAA1111111111111111111111111", "MintCapBBBB1111111111111111111111111"]
    ):
        j.open_from_alert(
            "moon",
            {
                "tokenAddress": mint,
                "symbol": f"T{i}",
                "mcap_usd": 10_000,
                "moon_label": "MOON",
            },
            paper=True,
        )
    from config import MAX_OPEN_TRADES

    ok, why = can_open_trade(j, kind="moon")
    if MAX_OPEN_TRADES <= 2:
        assert ok is False
        assert "open" in why.lower() or "max" in why.lower()


def test_journal_size_fields(tmp_path: Path):
    reset_journal_singleton()
    j = TradeJournal(tmp_path / "sz.db")
    plan = enrich_plan_with_size("snipe", {"mcap_usd": 8_000, "symbol": "S"})
    tid = j.open_from_alert(
        "snipe",
        {
            "tokenAddress": "MintSize1111111111111111111111111111",
            "symbol": "S",
            "mcap_usd": 8_000,
            "snipe_label": "SNIPE",
        },
        plan=plan,
    )
    row = j.get(tid)
    assert row["size_usd"] and row["size_usd"] > 0
    assert row["risk_usd"] and row["risk_usd"] > 0


def test_format_includes_size():
    from services.telegram_alerts import format_pick_message

    msg = format_pick_message(
        "moon",
        {
            "tokenAddress": "MintSz222111111111111111111111111111",
            "symbol": "SZ",
            "mcap_usd": 20_000,
            "moon_label": "MOON",
            "moon": {"why": ["edge"]},
        },
    )
    assert "SIZE" in msg or "size" in msg.lower() or "💰" in msg
