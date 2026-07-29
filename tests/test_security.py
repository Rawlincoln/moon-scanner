"""Security helpers: address validation, admin gate, redact."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.security import cors_allow_origins, safe_secret_eq, validate_token_address
from services.avoid_filters import is_hard_avoid


def test_validate_solana_mint_ok():
    mint = "TokenMintAbcdefghijkmnopqrstuvwxyz123pump"
    assert validate_token_address("solana", mint) == mint


def test_validate_solana_mint_bad():
    with pytest.raises(HTTPException) as ei:
        validate_token_address("solana", "not-a-mint!!")
    assert ei.value.status_code == 400


def test_validate_evm_ok():
    addr = "0x" + "a" * 40
    assert validate_token_address("ethereum", addr) == addr


def test_validate_evm_bad():
    with pytest.raises(HTTPException) as ei:
        validate_token_address("base", "0x123")
    assert ei.value.status_code == 400


def test_cors_defaults_nonempty():
    origins = cors_allow_origins()
    assert isinstance(origins, list)
    assert len(origins) >= 1


def test_safe_secret_eq_length_mismatch():
    assert safe_secret_eq("short", "longer_secret_value") is False
    assert safe_secret_eq("abcdef", "abcdef") is True
    assert safe_secret_eq("", "x") is False
    assert safe_secret_eq("abc", "abd") is False


def test_is_hard_avoid_shared():
    hard, why = is_hard_avoid(
        {"avoid": {"hard_avoid": True, "summary": "ghost", "flags": ["ghost_launch"]}}
    )
    assert hard is True
    assert why
    hard2, _ = is_hard_avoid(
        {"avoid": {"hard_avoid": False, "flags": ["bundled"], "summary": "bundled"}}
    )
    assert hard2 is True
    soft, _ = is_hard_avoid({"avoid": {"hard_avoid": False, "flags": ["wash_buys"]}})
    assert soft is False


def test_client_ip_ignores_xff_when_untrusted(monkeypatch):
    from app import security as sec
    from starlette.requests import Request
    from starlette.datastructures import Headers

    monkeypatch.setattr(sec, "TRUST_X_FORWARDED_FOR", False)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"8.8.8.8, 1.2.3.4")],
        "client": ("9.9.9.9", 12345),
        "server": ("127.0.0.1", 80),
    }
    req = Request(scope)
    assert sec.client_ip(req) == "9.9.9.9"


def test_client_ip_uses_rightmost_xff_when_trusted(monkeypatch):
    from app import security as sec
    from starlette.requests import Request

    monkeypatch.setattr(sec, "TRUST_X_FORWARDED_FOR", True)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"8.8.8.8, 1.2.3.4")],
        "client": ("9.9.9.9", 12345),
        "server": ("127.0.0.1", 80),
    }
    req = Request(scope)
    assert sec.client_ip(req) == "1.2.3.4"


def test_observe_feed_card_maps_moon_shape():
    from services.learning.memory import LearningMemory
    from services.learning.tracker import LearningEngine
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        mem = LearningMemory(Path(td) / "t.db")
        eng = LearningEngine(mem)
        card = {
            "tokenAddress": "TokenMintAbcdefghijkmnopqrstuvwxyz123pump",
            "name": "Test",
            "symbol": "TST",
            "mcap_usd": 8000,
            "enrich_ok": True,
            "pumpfun": {
                "usd_market_cap": 8000,
                "ath_market_cap": 9000,
                "reply_count": 15,
            },
            "safety": {"total_holders": 40, "top_holders": [{"pct": 2}]},
            "socialSignals": {"has_edge": True, "replies": 15},
            "moon_label": "WATCH",
        }
        out = eng.observe_feed_card(card, source="moon")
        assert out
        assert mem.get_token(card["tokenAddress"]) is not None
