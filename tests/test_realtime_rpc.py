"""Unit tests for realtime RPC helpers and mint extraction."""

from __future__ import annotations

from services.realtime_rpc import (
    classify_logs,
    extract_mint_from_tx_notification,
    is_paid_wss,
    mint_from_account_keys,
    mint_from_log_lines,
    redact_rpc_url,
    resolve_ws_mode,
)


def test_classify_create_logs():
    assert classify_logs(["Program log: Instruction: Create"]) == "create"
    assert classify_logs(["Program log: Instruction: InitializeMint2"]) == "create"
    assert classify_logs(["Program log: Instruction: Buy"]) == "buy"


def test_mint_from_log_lines_pump_suffix():
    # Valid base58 charset only (no 0,O,I,l)
    mint = "TokenMintAbcdefghijkmnopqrstuvwxyz123pump"
    logs = [f"Program log: mint {mint}"]
    assert mint_from_log_lines(logs) == mint


def test_mint_from_account_keys_prefers_pump():
    mint = "XyZabcdefghijkmnopqrstuvwxyz123456789pump"
    keys = [
        "11111111111111111111111111111111",
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
        "So11111111111111111111111111111111111111112",
        mint,
    ]
    assert mint_from_account_keys(keys) == mint


def test_extract_mint_from_tx_notification_json_parsed():
    mint = "TokenMintAbcdefghijkmnopqrstuvwxyz123pump"
    result = {
        "signature": "sig123",
        "slot": 99,
        "transaction": {
            "meta": {
                "err": None,
                "logMessages": ["Program log: Instruction: Create"],
                "postTokenBalances": [{"mint": mint, "owner": "creator"}],
            },
            "transaction": {
                "message": {
                    "accountKeys": [
                        {"pubkey": "creatorWallet1111111111111111111111111"},
                        {"pubkey": mint},
                        {"pubkey": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"},
                    ]
                }
            },
        },
    }
    out, kind, slot = extract_mint_from_tx_notification(result)
    assert out == mint
    assert kind == "create"
    assert slot == 99


def test_is_paid_wss_helius_host():
    assert is_paid_wss("wss://mainnet.helius-rpc.com/?api-key=abc")
    assert not is_paid_wss("wss://api.mainnet-beta.solana.com")


def test_redact_rpc_url_strips_api_key():
    raw = "wss://mainnet.helius-rpc.com/?api-key=secretKEY1234567890abcdef"
    out = redact_rpc_url(raw)
    assert "secretKEY" not in out
    assert "api-key" not in out
    assert "helius-rpc.com" in out



def test_resolve_ws_mode_auto_public(monkeypatch):
    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    monkeypatch.setenv("SOLANA_WS_MODE", "auto")
    monkeypatch.setenv("SOLANA_RPC_WSS", "wss://api.mainnet-beta.solana.com")
    # re-import resolve uses config at import time for SOLANA_WS_MODE
    # resolve_ws_mode reads SOLANA_WS_MODE from config module — patch config
    import config as cfg

    monkeypatch.setattr(cfg, "SOLANA_WS_MODE", "auto")
    monkeypatch.setattr(cfg, "HELIUS_API_KEY", "")
    monkeypatch.setattr(cfg, "SOLANA_RPC_WSS", "wss://api.mainnet-beta.solana.com")
    import services.realtime_rpc as rr

    monkeypatch.setattr(rr, "SOLANA_WS_MODE", "auto")
    monkeypatch.setattr(rr, "HELIUS_API_KEY", "")
    monkeypatch.setattr(rr, "SOLANA_RPC_WSS", "wss://api.mainnet-beta.solana.com")
    assert rr.resolve_ws_mode() == "logs"


def test_resolve_ws_mode_transaction_forced(monkeypatch):
    import services.realtime_rpc as rr

    monkeypatch.setattr(rr, "SOLANA_WS_MODE", "transaction")
    assert rr.resolve_ws_mode() == "transaction"
