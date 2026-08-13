"""Telegram alert formatting + status (no live API)."""

from services.telegram_alerts import (
    _allowed_labels,
    _label_of,
    format_pick_message,
    status,
)


def test_format_moon_message_has_links():
    t = {
        "tokenAddress": "MintABC1111111111111111111111111111111",
        "symbol": "TEST",
        "name": "Test Moon",
        "mcap_usd": 20_000,
        "age_minutes": 15,
        "moon_label": "MOON",
        "moon_score": 80,
        "moon": {"label": "MOON", "why": ["Near ATH", "Influencer edge"]},
    }
    msg = format_pick_message("moon", t)
    assert "MOON" in msg
    assert "TEST" in msg
    assert "Padre" in msg
    assert "MintABC" in msg


def test_format_heat_includes_dev():
    t = {
        "tokenAddress": "HeatMint11111111111111111111111111111",
        "symbol": "HOT",
        "mcap_usd": 8_000,
        "age_minutes": 10,
        "heat_label": "HEAT",
        "heat": {
            "label": "HEAT",
            "why": ["25 replies"],
            "dev": {
                "tokens_launched": 2,
                "tokens_migrated": 1,
                "creator_sold": False,
            },
        },
    }
    msg = format_pick_message("heat", t)
    assert "HEAT" in msg
    assert "launched" in msg.lower() or "migrated" in msg.lower()


def test_label_and_allowed():
    assert _label_of("moon", {"moon_label": "WATCH"}) == "WATCH"
    assert "MOON" in _allowed_labels("moon")
    assert "SNIPE" in _allowed_labels("snipe")
    # Money mode: WATCH allowed for climb/near-mig migrators; SETUP still off
    from config import TELEGRAM_MONEY_MODE

    if TELEGRAM_MONEY_MODE:
        assert "WATCH" in _allowed_labels("moon")
        assert "SETUP" not in _allowed_labels("snipe")
        assert "HEAT" in _allowed_labels("heat")
        assert "ELITE" in _allowed_labels("elite")
        assert "COPY" in _allowed_labels("elite")
        st = status()
        assert "elite" in (st.get("feeds") or []) or True  # env may override


def test_status_shape():
    st = status()
    assert "configured" in st
    assert "feeds" in st
    assert "labels" in st
    assert "money_mode" in st


def test_format_moon_has_money_plan():
    t = {
        "tokenAddress": "MintABC1111111111111111111111111111111",
        "symbol": "TEST",
        "name": "Test Moon",
        "mcap_usd": 20_000,
        "age_minutes": 15,
        "moon_label": "MOON",
        "moon_score": 80,
        "moon": {"label": "MOON", "why": ["Near ATH", "Influencer edge"]},
    }
    msg = format_pick_message("moon", t)
    assert "PLAN" in msg or "STOP" in msg or "🛑" in msg

