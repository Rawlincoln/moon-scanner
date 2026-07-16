"""EVM token safety analysis via honeypot.is."""

from __future__ import annotations

from typing import Any

import httpx

from config import (
    EVM_CHAIN_IDS,
    MAX_BUY_TAX_PCT,
    MAX_RISK_LEVEL,
    MAX_SELL_TAX_PCT,
    MIN_LIQUIDITY_USD,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

HONEYPOT_URL = "https://api.honeypot.is/v2/IsHoneypot"


class EVMAnalyzer:
    def __init__(self) -> None:
        self._headers = {"User-Agent": USER_AGENT}

    async def analyze(
        self, chain_id: str, token_address: str, pair_address: str | None = None
    ) -> dict[str, Any]:
        hp_chain = EVM_CHAIN_IDS.get(chain_id)
        if hp_chain is None:
            return self._unsupported_chain(chain_id)

        params: dict[str, str | int] = {
            "address": token_address,
            "chainID": hp_chain,
        }
        if pair_address:
            params["pair"] = pair_address

        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT, headers=self._headers
            ) as client:
                resp = await client.get(HONEYPOT_URL, params=params)
                if resp.status_code == 404:
                    return self._no_data(chain_id, token_address)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return self._error(chain_id, token_address, str(exc))

        return self._parse_response(chain_id, data)

    def _parse_response(self, chain_id: str, data: dict) -> dict[str, Any]:
        summary = data.get("summary") or {}
        honeypot = data.get("honeypotResult") or {}
        sim = data.get("simulationResult") or {}
        contract = data.get("contractCode") or {}
        pair_info = data.get("pair") or {}
        holder = data.get("holderAnalysis") or {}

        is_honeypot = bool(honeypot.get("isHoneypot", False))
        risk = summary.get("risk", "unknown")
        risk_level = summary.get("riskLevel", 100)
        buy_tax = float(sim.get("buyTax") or 0)
        sell_tax = float(sim.get("sellTax") or 0)
        transfer_tax = float(sim.get("transferTax") or 0)
        liquidity = float(pair_info.get("liquidity") or 0)
        flags = summary.get("flags") or data.get("flags") or []

        failed_holders = int(holder.get("failed") or 0)
        siphoned = int(holder.get("siphoned") or 0)
        high_tax_wallets = int(holder.get("highTaxWallets") or 0)

        issues: list[str] = []
        if is_honeypot:
            issues.append("Honeypot detected — cannot sell")
        if sell_tax > MAX_SELL_TAX_PCT:
            issues.append(f"High sell tax: {sell_tax}%")
        if buy_tax > MAX_BUY_TAX_PCT:
            issues.append(f"High buy tax: {buy_tax}%")
        if risk_level > MAX_RISK_LEVEL:
            issues.append(f"Risk level {risk_level} ({risk})")
        if liquidity < MIN_LIQUIDITY_USD:
            issues.append(f"Low liquidity: ${liquidity:,.0f}")
        if failed_holders > 0:
            issues.append(f"{failed_holders} holders cannot sell")
        if siphoned > 0:
            issues.append(f"{siphoned} wallets siphoned")
        if high_tax_wallets > 0:
            issues.append(f"{high_tax_wallets} wallets with 50%+ tax")
        if not contract.get("openSource", False):
            issues.append("Contract not fully open source")
        if contract.get("isProxy"):
            issues.append("Proxy contract detected")

        for flag in flags:
            if isinstance(flag, dict):
                sev = flag.get("severity", "")
                if sev in ("critical", "high"):
                    desc = flag.get("description", flag.get("flag", ""))
                    if desc and desc not in issues:
                        issues.append(desc[:120])

        passed = (
            not is_honeypot
            and sell_tax <= MAX_SELL_TAX_PCT
            and buy_tax <= MAX_BUY_TAX_PCT
            and risk_level <= MAX_RISK_LEVEL
            and liquidity >= MIN_LIQUIDITY_USD
            and failed_holders == 0
            and siphoned == 0
        )

        token = data.get("token") or {}
        return {
            "chain": chain_id,
            "type": "evm",
            "passed": passed,
            "is_honeypot": is_honeypot,
            "honeypot_reason": honeypot.get("honeypotReason"),
            "risk": risk,
            "risk_level": risk_level,
            "buy_tax": buy_tax,
            "sell_tax": sell_tax,
            "transfer_tax": transfer_tax,
            "liquidity_usd": liquidity,
            "open_source": contract.get("openSource", False),
            "is_proxy": contract.get("isProxy", False),
            "holders_analyzed": int(holder.get("holders") or 0),
            "failed_sells": failed_holders,
            "siphoned_wallets": siphoned,
            "high_tax_wallets": high_tax_wallets,
            "simulation_success": data.get("simulationSuccess", False),
            "token_name": token.get("name", ""),
            "token_symbol": token.get("symbol", ""),
            "total_holders": token.get("totalHolders"),
            "pair_address": data.get("pairAddress"),
            "issues": issues,
            "flags": flags,
        }

    @staticmethod
    def _unsupported_chain(chain_id: str) -> dict:
        return {
            "chain": chain_id,
            "type": "evm",
            "passed": False,
            "issues": [f"Chain {chain_id} not supported for honeypot check"],
        }

    @staticmethod
    def _no_data(chain_id: str, address: str) -> dict:
        return {
            "chain": chain_id,
            "type": "evm",
            "passed": False,
            "issues": [f"No honeypot data for {address[:10]}..."],
        }

    @staticmethod
    def _error(chain_id: str, address: str, msg: str) -> dict:
        return {
            "chain": chain_id,
            "type": "evm",
            "passed": False,
            "issues": [f"Analysis error: {msg[:100]}"],
        }