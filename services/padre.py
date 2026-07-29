"""Padre (Terminal) API client — trade.padre.gg integration."""

from __future__ import annotations

from typing import Any

from config import PADRE_API_URL, PADRE_TRADE_URL, REQUEST_TIMEOUT
from services.http_client import get as http_get

PADRE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Origin": PADRE_TRADE_URL,
    "Referer": f"{PADRE_TRADE_URL}/trenches",
}

CHAIN_TO_PADRE = {
    "solana": "SOLANA",
    "bsc": "BSC",
    "base": "BASE",
    "ethereum": "ETH",
    "arbitrum": "ARBITRUM",
    "polygon": "POLYGON",
}

CHAIN_TO_TRADE = {
    "solana": "solana",
    "bsc": "bsc",
    "base": "base",
    "ethereum": "ethereum",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
}


class PadreClient:
    def __init__(self) -> None:
        self._api = PADRE_API_URL
        self._trade = PADRE_TRADE_URL

    @staticmethod
    def trade_url(chain_id: str, token_address: str) -> str:
        chain = CHAIN_TO_TRADE.get(chain_id, chain_id)
        return f"{PADRE_TRADE_URL}/trade/{chain}/{token_address}"

    @staticmethod
    def trenches_url() -> str:
        return f"{PADRE_TRADE_URL}/trenches"

    @staticmethod
    def new_pairs_url() -> str:
        return f"{PADRE_TRADE_URL}/new-pairs"

    async def get_token_audit(
        self, chain_id: str, token_address: str
    ) -> dict[str, Any] | None:
        padre_chain = CHAIN_TO_PADRE.get(chain_id)
        if not padre_chain:
            return None

        url = (
            f"{self._api}/markets/chains/{padre_chain}/tokens/"
            f"{token_address}/get-token-audit"
        )
        try:
            resp = await http_get(url, headers=PADRE_HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    @staticmethod
    def parse_audit(audit: dict | None) -> dict[str, Any]:
        if not audit:
            return {"available": False}

        rug = audit.get("rugcheck") or {}
        honeypot = audit.get("honeypot")
        checks = rug.get("checks") or []

        danger = [c for c in checks if c.get("level") in ("danger", "critical")]
        warn = [c for c in checks if c.get("level") == "warn"]

        issues: list[str] = []
        for check in checks:
            name = check.get("name", "")
            desc = check.get("description", "")
            level = check.get("level", "")
            if level in ("danger", "critical", "warn"):
                issues.append(f"[Padre] {name}: {desc or level}")

        if honeypot and honeypot.get("isHoneypot"):
            issues.append("[Padre] Honeypot detected")

        return {
            "available": True,
            "rugcheck_checks": len(checks),
            "danger_checks": len(danger),
            "warn_checks": len(warn),
            "honeypot": bool(honeypot and honeypot.get("isHoneypot")),
            "honeypot_data": honeypot,
            "rugcheck": rug,
            "issues": issues,
            "trade_url": None,
        }