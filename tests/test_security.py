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
