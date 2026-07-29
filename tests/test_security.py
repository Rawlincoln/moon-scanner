"""Security helpers: address validation, admin gate, redact."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.security import cors_allow_origins, validate_token_address


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
