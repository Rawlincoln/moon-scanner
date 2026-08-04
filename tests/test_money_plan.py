"""Money plan + invalidation + journal."""

from pathlib import Path

from services.money_plan import build_money_plan, check_invalidation, classify_exit
from services.trade_journal import TradeJournal, reset_journal_singleton
from services.telegram_alerts import format_pick_message


def test_build_plan_levels():
    plan = build_money_plan("moon", {"mcap_usd": 10_000, "symbol": "X"})
    assert plan["entry_mcap"] == 10_000
    assert plan["stop_mcap"] == 8200  # −18%
    assert plan["tp1_mcap"] == 15_000  # +50%
    assert plan["tp2_mcap"] == 20_000  # +100%
    assert plan["invalid_if_below_mcap"] == 8500  # −15%
    assert plan["max_hold_min"] == 45


def test_invalidation_drop():
    plan = build_money_plan("snipe", {"mcap_usd": 10_000})
    bad, reason = check_invalidation(plan, current_mcap=8_000, alert_age_min=5)
    assert bad is True
    assert reason and "drop" in reason.lower() or "dropped" in (reason or "").lower()


def test_invalidation_time_stop():
    plan = build_money_plan("moon", {"mcap_usd": 10_000})
    # still near entry after 50m
    bad, reason = check_invalidation(plan, current_mcap=10_200, alert_age_min=50)
    assert bad is True
    assert "time" in (reason or "").lower()


def test_invalidation_ok_early():
    plan = build_money_plan("moon", {"mcap_usd": 10_000})
    bad, _ = check_invalidation(plan, current_mcap=10_500, alert_age_min=10)
    assert bad is False


def test_classify_tp_and_stop():
    plan = build_money_plan("moon", {"mcap_usd": 10_000})
    assert classify_exit(plan, exit_mcap=16_000, peak_mcap=16_000)["outcome"] == "tp1"
    assert classify_exit(plan, exit_mcap=21_000, peak_mcap=21_000)["outcome"] == "tp2"
    assert classify_exit(plan, exit_mcap=8_000, peak_mcap=10_000)["outcome"] in (
        "stop",
        "loss",
    )


def test_journal_open_invalidate_close(tmp_path: Path):
    reset_journal_singleton()
    j = TradeJournal(tmp_path / "tj.db")
    tid = j.open_from_alert(
        "moon",
        {
            "tokenAddress": "JournalMint1111111111111111111111111",
            "symbol": "JRN",
            "mcap_usd": 12_000,
            "moon_label": "MOON",
        },
        paper=True,
    )
    assert tid
    # crash
    row = j.apply_mcap(tid, 9_500)  # −21%
    assert row and row["status"] == "invalid"
    assert row["invalid_reason"]
    s = j.summary()
    assert s["closed_or_invalid"] >= 1


def test_format_message_has_plan():
    msg = format_pick_message(
        "moon",
        {
            "tokenAddress": "MintPlan1111111111111111111111111111",
            "symbol": "PLN",
            "mcap_usd": 18_000,
            "age_minutes": 20,
            "moon_label": "MOON",
            "moon_score": 80,
            "moon": {"label": "MOON", "why": ["Near ATH"]},
        },
    )
    assert "STOP" in msg or "stop" in msg.lower() or "🛑" in msg
    assert "TP1" in msg or "🎯" in msg
    assert "INVALID" in msg or "❌" in msg
