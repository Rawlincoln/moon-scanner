"""Solana token safety analysis via RugCheck API."""

from __future__ import annotations

from typing import Any

import httpx

from config import (
    MAX_SOLANA_RUG_SCORE,
    MIN_LIQUIDITY_USD,
    MIN_LP_LOCKED_PCT,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

RUGCHECK_BASE = "https://api.rugcheck.xyz/v1/tokens"


class SolanaAnalyzer:
    def __init__(self) -> None:
        self._headers = {"User-Agent": USER_AGENT}

    async def analyze(
        self,
        mint_address: str,
        pump_coin: dict | None = None,
        padre_audit: dict | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT, headers=self._headers
            ) as client:
                summary_resp = await client.get(
                    f"{RUGCHECK_BASE}/{mint_address}/report/summary"
                )
                if summary_resp.status_code == 404:
                    if pump_coin:
                        return self._pumpfun_fallback(
                            mint_address, pump_coin, padre_audit=padre_audit
                        )
                    return self._no_data(mint_address)
                summary_resp.raise_for_status()
                summary = summary_resp.json()

                full_resp = await client.get(
                    f"{RUGCHECK_BASE}/{mint_address}/report"
                )
                full = full_resp.json() if full_resp.status_code == 200 else {}
        except Exception as exc:
            if pump_coin:
                return self._pumpfun_fallback(
                    mint_address, pump_coin, padre_audit=padre_audit
                )
            return self._error(mint_address, str(exc))

        return self._parse_response(
            mint_address,
            summary,
            full,
            pump_coin=pump_coin,
            padre_audit=padre_audit,
        )

    def _parse_response(
        self,
        mint: str,
        summary: dict,
        full: dict,
        pump_coin: dict | None = None,
        padre_audit: dict | None = None,
    ) -> dict[str, Any]:
        score_norm = int(summary.get("score_normalised") or 100)
        score_raw = int(summary.get("score") or 0)
        lp_locked = float(summary.get("lpLockedPct") or 0)
        risks = summary.get("risks") or full.get("risks") or []
        on_bonding_curve = bool(
            pump_coin and not pump_coin.get("complete", True)
        )

        danger_risks = [
            r
            for r in risks
            if r.get("level") == "danger"
            and not (
                on_bonding_curve
                and "liquidity" in r.get("name", "").lower()
            )
        ]
        warn_risks = [r for r in risks if r.get("level") == "warn"]

        issues: list[str] = []
        is_honeypot = False
        sell_tax_detected = False

        for risk in risks:
            name = risk.get("name", "")
            level = risk.get("level", "")
            desc = risk.get("description", "")
            value = risk.get("value", "")

            if on_bonding_curve and "liquidity" in name.lower():
                continue

            if "honeypot" in name.lower() or "cannot sell" in desc.lower():
                is_honeypot = True
                issues.append(f"{name}: {desc}")
            elif "sell tax" in name.lower() or "transfer fee" in name.lower():
                sell_tax_detected = True
                issues.append(f"{name}: {value or desc}")
            elif level == "danger":
                issues.append(f"{name}: {desc or value}")
            elif level == "warn" and score_norm > 20:
                issues.append(f"{name}: {desc or value}")

        if pump_coin and pump_coin.get("is_banned"):
            issues.append("Token banned on pump.fun")
        if pump_coin and pump_coin.get("complete"):
            issues.append("Already graduated from bonding curve")

        # Extract mint authority / freeze from full report
        mint_authority = full.get("mintAuthority")
        freeze_authority = full.get("freezeAuthority")
        mutable_metadata = full.get("mutableMetadata", False)

        if mint_authority:
            issues.append("Mint authority not revoked — supply can be inflated")
        if freeze_authority:
            issues.append("Freeze authority active — wallets can be frozen")
        if mutable_metadata:
            issues.append("Token metadata is mutable")

        # Rug indicators from full report
        top_holders = full.get("topHolders") or []
        if top_holders:
            top_pct = sum(h.get("pct", 0) for h in top_holders[:5])
            if top_pct > 50:
                issues.append(f"Top 5 holders own {top_pct:.1f}% of supply")

        liquidity = 0.0
        for risk in risks:
            if "liquidity" in risk.get("name", "").lower():
                val = risk.get("value", "")
                try:
                    liquidity = float(
                        val.replace("$", "").replace(",", "")
                    )
                except (ValueError, AttributeError):
                    pass

        max_rug = 50 if on_bonding_curve else MAX_SOLANA_RUG_SCORE
        lp_ok = (
            lp_locked >= MIN_LP_LOCKED_PCT
            or on_bonding_curve
            or lp_locked >= 100
        )

        passed = (
            not is_honeypot
            and not sell_tax_detected
            and score_norm <= max_rug
            and len(danger_risks) == 0
            and lp_ok
            and not mint_authority
            and not freeze_authority
            and not (pump_coin and pump_coin.get("is_banned"))
        )

        if liquidity >= MIN_LIQUIDITY_USD * 10 and lp_locked < MIN_LP_LOCKED_PCT:
            if "Low LP lock" not in str(issues):
                issues.append(
                    f"LP only {lp_locked:.0f}% locked (acceptable due to high liquidity)"
                )
            passed = passed or (
                not is_honeypot
                and not sell_tax_detected
                and score_norm <= max_rug
                and not mint_authority
            )

        token_meta = full.get("tokenMeta") or {}
        if pump_coin and not token_meta.get("name"):
            token_meta = {
                "name": pump_coin.get("name", ""),
                "symbol": pump_coin.get("symbol", ""),
            }
        markets = full.get("markets") or []

        creator = full.get("creator") or (pump_coin or {}).get("creator")
        creator_balance = float(full.get("creatorBalance") or 0)
        creator_pct = 0.0
        creator_sold = False
        if creator and top_holders:
            for h in top_holders:
                owner = h.get("owner") or h.get("address") or ""
                if creator in str(owner):
                    creator_pct = float(h.get("pct") or 0)
                    break
            if creator_pct == 0 and creator_balance == 0:
                creator_sold = True

        insider_detected = bool(full.get("graphInsidersDetected"))
        insider_networks = len(full.get("insiderNetworks") or [])
        creator_tokens = len(full.get("creatorTokens") or [])
        total_holders = int(full.get("totalHolders") or 0)
        rugged = bool(full.get("rugged"))
        insider_holders = [
            h for h in top_holders if h.get("insider")
        ]

        result = {
            "chain": "solana",
            "type": "solana",
            "passed": passed,
            "on_bonding_curve": on_bonding_curve,
            "is_honeypot": is_honeypot,
            "sell_tax_detected": sell_tax_detected,
            "rug_score": score_norm,
            "rug_score_raw": score_raw,
            "lp_locked_pct": lp_locked,
            "liquidity_usd": liquidity,
            "mint_authority": mint_authority,
            "freeze_authority": freeze_authority,
            "mutable_metadata": mutable_metadata,
            "danger_risks": len(danger_risks),
            "warn_risks": len(warn_risks),
            "token_name": token_meta.get("name", ""),
            "token_symbol": token_meta.get("symbol", ""),
            "markets_count": len(markets),
            "top_holders": top_holders[:10],
            "risks": risks,
            "issues": issues,
            "creator": creator,
            "creator_balance": creator_balance,
            "creator_pct": creator_pct,
            "creator_sold": creator_sold,
            "insider_detected": insider_detected,
            "insider_networks": insider_networks,
            "creator_token_count": creator_tokens,
            "total_holders": total_holders,
            "rugged": rugged,
            "insider_holders": insider_holders[:5],
        }
        return self._merge_padre_audit(result, padre_audit)

    @staticmethod
    def _merge_padre_audit(
        result: dict[str, Any], padre_audit: dict | None
    ) -> dict[str, Any]:
        from services.padre import PadreClient

        parsed = PadreClient.parse_audit(padre_audit)
        result["padre"] = parsed
        if not parsed.get("available"):
            return result

        if parsed.get("honeypot"):
            result["is_honeypot"] = True
            result["passed"] = False

        for issue in parsed.get("issues", []):
            if issue not in result.get("issues", []):
                result.setdefault("issues", []).append(issue)

        if parsed.get("danger_checks", 0) > 0:
            result["passed"] = False

        return result

    @staticmethod
    def _pumpfun_fallback(
        mint: str, coin: dict, padre_audit: dict | None = None
    ) -> dict[str, Any]:
        """Safety check when RugCheck hasn't indexed a brand-new mint yet."""
        banned = bool(coin.get("is_banned"))
        graduated = bool(coin.get("complete"))
        issues: list[str] = []
        if banned:
            issues.append("Token banned on pump.fun")
        if graduated:
            issues.append("Already graduated — late entry")

        passed = not banned and not graduated
        result = {
            "chain": "solana",
            "type": "solana",
            "passed": passed,
            "is_honeypot": False,
            "sell_tax_detected": False,
            "rug_score": 0,
            "rug_score_raw": 0,
            "lp_locked_pct": 100.0,
            "liquidity_usd": float(coin.get("usd_market_cap") or 0),
            "mint_authority": None,
            "freeze_authority": None,
            "mutable_metadata": False,
            "danger_risks": 0,
            "warn_risks": 0,
            "token_name": coin.get("name", ""),
            "token_symbol": coin.get("symbol", ""),
            "markets_count": 0,
            "top_holders": [],
            "risks": [],
            "issues": issues,
            "on_bonding_curve": not graduated,
            "pumpfun_native": True,
            "creator": coin.get("creator"),
            "creator_balance": 0,
            "creator_pct": 0,
            "creator_sold": False,
            "insider_detected": False,
            "insider_networks": 0,
            "creator_token_count": 0,
            "total_holders": 0,
            "rugged": False,
            "insider_holders": [],
        }
        return SolanaAnalyzer._merge_padre_audit(result, padre_audit)

    @staticmethod
    def _no_data(mint: str) -> dict:
        return {
            "chain": "solana",
            "type": "solana",
            "passed": False,
            "issues": [f"No RugCheck data for {mint[:10]}..."],
        }

    @staticmethod
    def _error(mint: str, msg: str) -> dict:
        return {
            "chain": "solana",
            "type": "solana",
            "passed": False,
            "issues": [f"Analysis error: {msg[:100]}"],
        }