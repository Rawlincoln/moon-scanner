"""Smoke: scan pipeline imports and card builder rejects banned mints."""

from services.avoid_filters import BLOCKED_MINTS
from services.scan_moon import moon_card_from_coin, rough_priority


def test_moon_card_rejects_blocklist():
    mint = next(iter(BLOCKED_MINTS))
    coin = {
        "mint": mint,
        "usd_market_cap": 20_000,
        "ath_market_cap": 20_000,
        "name": "Bad",
        "symbol": "BAD",
        "reply_count": 10,
    }
    assert moon_card_from_coin(coin) is None


def test_rough_priority_prefers_near_ath():
    low = {"mcap_usd": 10_000, "ath_mcap": 20_000, "bonding_progress": 20, "pumpfun": {}}
    high = {"mcap_usd": 19_000, "ath_mcap": 20_000, "bonding_progress": 40, "pumpfun": {"reply_count": 20}}
    assert rough_priority(high) > rough_priority(low)
