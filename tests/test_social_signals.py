"""Influencer + ticker narrative detection."""

from services.social_signals import analyze_social_narrative, ticker_quality


def test_elon_status_url_with_community_is_influencer_tweet():
    r = analyze_social_narrative(
        pump_coin={
            "twitter": "https://x.com/elonmusk/status/1234567890123456789",
            "reply_count": 20,
            "name": "Something",
            "symbol": "SIG",
            "description": "",
        }
    )
    assert r["influencer_tweet_claim"] is True
    assert r["influencer_tweet"] is True
    assert r["tweet_by"] == "Elon Musk"
    assert r["has_edge"] is True
    assert r["edge_score"] >= 50


def test_elon_status_url_without_community_is_claim_only():
    r = analyze_social_narrative(
        pump_coin={
            "twitter": "https://x.com/elonmusk/status/1234567890123456789",
            "reply_count": 0,
            "name": "Something",
            "symbol": "SIG",
            "description": "",
        }
    )
    assert r["influencer_tweet_claim"] is True
    assert r["influencer_tweet"] is False
    assert r["has_edge"] is False


def test_cz_status_url():
    r = analyze_social_narrative(
        pump_coin={
            "twitter": "https://twitter.com/cz_binance/status/99",
            "symbol": "CZM",
            "name": "CZ Meta",
            "reply_count": 18,
        }
    )
    assert r["influencer_tweet"] is True
    assert "CZ" in (r["tweet_by"] or "")


def test_keyword_only_no_community_not_edge():
    r = analyze_social_narrative(
        pump_coin={
            "twitter": "",
            "name": "Pepe Coin",
            "symbol": "PEPE",
            "description": "pepe moon",
            "reply_count": 1,
        }
    )
    assert r["has_edge"] is False
    assert r.get("namejack_risk") is True


def test_ansem_and_alon_handles_need_community():
    for handle, name_part in (
        ("blknoiz06", "Ansem"),
        ("a1lon9", "Alon"),
    ):
        r = analyze_social_narrative(
            pump_coin={
                "twitter": f"https://x.com/{handle}/status/1",
                "reply_count": 5,
                "symbol": "X",
                "name": "X",
            }
        )
        assert r["influencer_tweet_claim"] is True, handle
        assert r["influencer_tweet"] is False, handle  # no community yet
        assert name_part in (r["tweet_by"] or ""), (handle, r["tweet_by"])


def test_namejack_elon_without_tweet():
    r = analyze_social_narrative(
        pump_coin={
            "twitter": "",
            "name": "Elon Coin",
            "symbol": "ELON",
            "description": "to the moon",
            "reply_count": 0,
        }
    )
    assert r["namejack_risk"] is True
    assert r["has_edge"] is False


def test_junk_ticker_rejected():
    tq = ticker_quality("XKQRTV", "Noise")
    assert tq["ok"] is False
    assert any("gibberish" in i for i in tq["issues"])


def test_hot_ticker_boost():
    tq = ticker_quality("TRUMP", "Trump")
    assert tq.get("hot_ticker")
    assert tq["score"] >= 50
