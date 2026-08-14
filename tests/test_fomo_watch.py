"""FOMO wallet buy/exit parsing tests."""

from __future__ import annotations

from services.fomo_watch import format_fomo_telegram, parse_wallet_token_deltas


def _tx(wallet: str, mint: str, pre: float, post: float) -> dict:
    return {
        "meta": {
            "err": None,
            "preTokenBalances": [
                {
                    "owner": wallet,
                    "mint": mint,
                    "uiTokenAmount": {"uiAmount": pre, "decimals": 6, "amount": str(int(pre * 1e6))},
                }
            ],
            "postTokenBalances": [
                {
                    "owner": wallet,
                    "mint": mint,
                    "uiTokenAmount": {"uiAmount": post, "decimals": 6, "amount": str(int(post * 1e6))},
                }
            ],
        }
    }


def test_parse_buy_delta():
    w = "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f"
    mint = "TokenMintAbcdefghijkmnopqrstuvwxyz123pump"
    d = parse_wallet_token_deltas(_tx(w, mint, 0, 50000), w)
    assert len(d) == 1
    assert d[0]["side"] == "buy"
    assert d[0]["mint"] == mint
    assert d[0]["delta"] > 0


def test_parse_sell_delta():
    w = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
    mint = "TokenMintAbcdefghijkmnopqrstuvwxyz123pump"
    d = parse_wallet_token_deltas(_tx(w, mint, 80000, 0), w)
    assert len(d) == 1
    assert d[0]["side"] == "sell"


def test_skip_wsol():
    w = "CAPn1yH4oSywsxGU456jfgTrSSUidf9jgeAnHceNUJdw"
    wsol = "So11111111111111111111111111111111111111112"
    d = parse_wallet_token_deltas(_tx(w, wsol, 1, 10), w)
    assert d == []


def test_format_buy_telegram():
    msg = format_fomo_telegram(
        {
            "side": "buy",
            "wallet_label": "Cupsey",
            "symbol": "PEPE",
            "mint": "Mint111",
            "mcap": 12000,
            "signature": "Sig111",
            "wallet": "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f",
            "pre": 0,
            "post": 1000,
        }
    )
    assert "FOMO BUY" in msg
    assert "Cupsey" in msg
    assert "PEPE" in msg


def test_format_exit_telegram():
    msg = format_fomo_telegram(
        {
            "side": "sell",
            "wallet_label": "Cented",
            "symbol": "DOGE",
            "mint": "Mint222",
            "hold_sec": 180,
            "signature": "Sig222",
            "wallet": "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
        }
    )
    assert "FOMO EXIT" in msg
    assert "Cented" in msg
