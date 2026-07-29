"""RPC URL helpers + mint extraction for realtime feeds (unit-testable)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from config import (
    HELIUS_API_KEY,
    SOLANA_RPC_HTTP,
    SOLANA_RPC_WSS,
    SOLANA_WS_MODE,
)
from services.realtime_bus import PUMPFUN_PROGRAM_ID

_MINT_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")

_KNOWN_SKIP = {
    PUMPFUN_PROGRAM_ID,
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "ComputeBudget111111111111111111111111111111",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "SysvarRent111111111111111111111111111111111",
    "SysvarC1ock11111111111111111111111111111111",
}


def wss_url() -> str:
    return SOLANA_RPC_WSS


def http_url() -> str:
    return SOLANA_RPC_HTTP


def is_paid_wss(url: str | None = None) -> bool:
    """Heuristic: non-public Solana WSS (Helius / QuickNode / Triton / key in URL)."""
    u = (url or wss_url()).lower()
    if HELIUS_API_KEY:
        return True
    if "api-key=" in u or "apikey=" in u:
        return True
    host = urlparse(u).hostname or ""
    paid_hosts = (
        "helius-rpc.com",
        "helius.dev",
        "quiknode.pro",
        "quicknode.com",
        "triton.one",
        "rpcpool.com",
        "chainstack",
        "alchemy.com",
        "genesysgo",
        "shyft.to",
    )
    return any(h in host for h in paid_hosts)


def resolve_ws_mode() -> str:
    """Return 'transaction' or 'logs'."""
    mode = SOLANA_WS_MODE
    if mode in ("transaction", "tx", "transactions"):
        return "transaction"
    if mode in ("logs", "log", "logsSubscribe"):
        return "logs"
    # auto
    return "transaction" if is_paid_wss() else "logs"


def classify_logs(logs: list[Any] | None) -> str:
    """create | buy | sell | unknown from program log lines."""
    blob = "\n".join(str(x) for x in (logs or [])).lower()
    if (
        "instruction: create" in blob
        or "createv2" in blob
        or "initialize mint" in blob
        or "initializemint2" in blob
    ):
        return "create"
    if "instruction: buy" in blob or " buy " in blob:
        return "buy"
    if "instruction: sell" in blob:
        return "sell"
    return "unknown"


def mint_from_log_lines(logs: list[Any] | None) -> str | None:
    for line in logs or []:
        s = str(line)
        if "pump" not in s.lower():
            continue
        for m in _MINT_RE.findall(s):
            if m.endswith("pump") and 32 <= len(m) <= 44:
                return m
    return None


def _account_key_str(k: Any) -> str:
    if isinstance(k, str):
        return k
    if isinstance(k, dict):
        return str(k.get("pubkey") or k.get("publicKey") or "")
    return str(k or "")


def extract_account_keys(tx_payload: dict[str, Any]) -> list[str]:
    """Pull account pubkeys from various getTransaction / notification shapes."""
    addrs: list[str] = []

    def _from_message(msg: dict) -> None:
        for k in msg.get("accountKeys") or []:
            a = _account_key_str(k)
            if a:
                addrs.append(a)

    # logsSubscribe-style getTransaction result
    if "transaction" in tx_payload and "message" in (tx_payload.get("transaction") or {}):
        _from_message((tx_payload.get("transaction") or {}).get("message") or {})
        return addrs

    # transactionSubscribe: result.transaction.{transaction, meta}
    outer = tx_payload.get("transaction") or tx_payload
    inner = outer.get("transaction") if isinstance(outer, dict) else None
    if isinstance(inner, dict):
        msg = (inner.get("message") or {}) if "message" in inner else {}
        if msg:
            _from_message(msg)
        # some encodings nest twice
        if not addrs and isinstance(inner.get("transaction"), dict):
            _from_message((inner.get("transaction") or {}).get("message") or {})
    elif isinstance(outer, dict) and "message" in outer:
        _from_message(outer.get("message") or {})

    return addrs


def mint_from_account_keys(keys: list[str]) -> str | None:
    pump_ends = [a for a in keys if a.endswith("pump") and 32 <= len(a) <= 44]
    if pump_ends:
        # Prefer first non-program pump mint (create usually early in keys)
        for a in pump_ends:
            if a not in _KNOWN_SKIP:
                return a
        return pump_ends[0]
    return None


def mint_from_token_balances(meta: dict[str, Any] | None) -> str | None:
    if not meta:
        return None
    for bal in (meta.get("postTokenBalances") or []) + (meta.get("preTokenBalances") or []):
        mint = str((bal or {}).get("mint") or "")
        if mint.endswith("pump") and 32 <= len(mint) <= 44:
            return mint
    return None


def extract_mint_from_tx_notification(result: dict[str, Any]) -> tuple[str | None, str, int | None]:
    """
    Parse transactionSubscribe / getTransaction-shaped result.
    Returns (mint, kind, slot).
    """
    slot = result.get("slot")
    if isinstance(slot, str):
        try:
            slot = int(slot)
        except ValueError:
            slot = None

    tx_wrap = result.get("transaction") or result
    meta = {}
    if isinstance(tx_wrap, dict):
        meta = tx_wrap.get("meta") or {}
        if not meta and isinstance(tx_wrap.get("transaction"), dict):
            meta = (tx_wrap.get("transaction") or {}).get("meta") or meta

    logs = meta.get("logMessages") or meta.get("logs") or []
    kind = classify_logs(logs)

    mint = mint_from_log_lines(logs)
    if not mint:
        mint = mint_from_token_balances(meta if isinstance(meta, dict) else {})
    if not mint:
        keys = extract_account_keys(result if "transaction" in result else {"transaction": tx_wrap})
        if not keys:
            keys = extract_account_keys(tx_wrap if isinstance(tx_wrap, dict) else {})
        mint = mint_from_account_keys(keys)

    # Helius pump create: often keys[1] is new mint when InitializeMint2
    if not mint and kind == "create":
        keys = extract_account_keys(result)
        if len(keys) >= 2 and keys[1] not in _KNOWN_SKIP:
            mint = keys[1]

    return mint, kind if kind != "unknown" else "buy", slot if isinstance(slot, int) else None
