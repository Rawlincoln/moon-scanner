"""Solana token safety analysis via RugCheck API."""

from __future__ import annotations

import asyncio
from typing import Any

from config import (
    MAX_SOLANA_RUG_SCORE,
    MIN_LIQUIDITY_USD,
    MIN_LP_LOCKED_PCT,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from services.avoid_filters import analyze_avoid_flags
from services.http_client import get as http_get
from services.http_client import get_client

RUGCHECK_BASE = "https://api.rugcheck.xyz/v1/tokens"


class SolanaAnalyzer:
    def __init__(self) -> None:
        self._headers = {"User-Agent": USER_AGENT}

    async def analyze(
        self,
        mint_address: str,
        pump_coin: dict | None = None,
        padre_audit: dict | None = None,
        *,
        fast: bool = False,
    ) -> dict[str, Any]:
        try:
            # Fast bulk: summary only (~1 round-trip). Full report on deep analyze.
            if fast:
                summary_resp = await http_get(
                    f"{RUGCHECK_BASE}/{mint_address}/report/summary",
                    headers=self._headers,
                    timeout=REQUEST_TIMEOUT,
                )
                if summary_resp.status_code == 404:
                    if pump_coin:
                        return self._pumpfun_fallback(
                            mint_address, pump_coin, padre_audit=padre_audit
                        )
                    return self._no_data(mint_address)
                if summary_resp.status_code == 429:
                    if pump_coin:
                        return self._pumpfun_fallback(
                            mint_address, pump_coin, padre_audit=padre_audit
                        )
                    return self._error(mint_address, "RugCheck rate limited")
                summary_resp.raise_for_status()
                summary = summary_resp.json()
                full = {}
            else:
                client = get_client()
                summary_resp, full_resp = await asyncio.gather(
                    client.get(
                        f"{RUGCHECK_BASE}/{mint_address}/report/summary",
                        headers=self._headers,
                        timeout=REQUEST_TIMEOUT,
                    ),
                    client.get(
                        f"{RUGCHECK_BASE}/{mint_address}/report",
                        headers=self._headers,
                        timeout=REQUEST_TIMEOUT,
                    ),
                    return_exceptions=True,
                )
                if isinstance(summary_resp, Exception):
                    raise summary_resp
                if summary_resp.status_code == 404:
                    if pump_coin:
                        return self._pumpfun_fallback(
                            mint_address, pump_coin, padre_audit=padre_audit
                        )
                    return self._no_data(mint_address)
                summary_resp.raise_for_status()
                summary = summary_resp.json()
                if isinstance(full_resp, Exception) or full_resp.status_code != 200:
                    full = {}
                else:
                    full = full_resp.json()
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

        # Rug indicators from full report (summary may carry a shorter list)
        top_holders = full.get("topHolders") or summary.get("topHolders") or []
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
        lp_info = self._extract_lp_info(markets, summary, on_bonding_curve)
        # Prefer market-level lock stats when summary is blank/misleading
        if lp_info.get("lp_locked_pct") is not None:
            lp_locked = float(lp_info["lp_locked_pct"])
        if lp_info.get("quote_usd"):
            liquidity = max(liquidity, float(lp_info["quote_usd"]))

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

        insider_detected = bool(
            full.get("graphInsidersDetected") or summary.get("graphInsidersDetected")
        )
        insider_networks = len(
            full.get("insiderNetworks") or summary.get("insiderNetworks") or []
        )
        creator_token_rows = full.get("creatorTokens") or []
        if not isinstance(creator_token_rows, list):
            creator_token_rows = []
        creator_tokens = len(creator_token_rows)
        # Count prior graduations / migrations when RugCheck exposes signals
        creator_migrated = 0
        for ct in creator_token_rows:
            if not isinstance(ct, dict):
                continue
            if (
                ct.get("migrated")
                or ct.get("complete")
                or ct.get("raydiumPool")
                or ct.get("raydium")
                or ct.get("graduated")
                or str(ct.get("status") or "").lower()
                in ("migrated", "graduated", "complete")
            ):
                creator_migrated += 1
                continue
            try:
                # High historical mcap on creator's other mints ≈ migrated/ran
                mc = float(ct.get("marketCap") or ct.get("usd_market_cap") or 0)
                if mc >= 50_000:
                    creator_migrated += 1
            except (TypeError, ValueError):
                pass
        total_holders = int(full.get("totalHolders") or summary.get("totalHolders") or 0)
        rugged = bool(full.get("rugged") or summary.get("rugged"))
        insider_holders = [h for h in top_holders if h.get("insider")]

        # Re-evaluate LP with market data.
        # NOTE: pump.fun always reports lpLockedPct=100 — do NOT trust that alone.
        quote_sol = float(lp_info.get("quote_sol") or 0)
        unlocked = float(lp_info.get("lp_unlocked") or 0)
        if on_bonding_curve:
            # Exit liquidity = real SOL left on the curve
            lp_ok = quote_sol >= 0.5
            if not lp_ok:
                issues.append(
                    f"Bonding curve drained — only {quote_sol:.3f} SOL left "
                    "(devs/sellers already pulled exit liquidity)"
                )
        else:
            lp_ok = lp_locked >= MIN_LP_LOCKED_PCT and unlocked <= 0
            if unlocked > 0:
                issues.append("LP tokens unlocked — liquidity can be pulled")
            elif lp_locked < MIN_LP_LOCKED_PCT:
                issues.append(
                    f"LP only {lp_locked:.0f}% locked — can be pulled"
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

        result = {
            "chain": "solana",
            "type": "solana",
            "mint": mint,
            "passed": passed,
            "on_bonding_curve": on_bonding_curve,
            "is_honeypot": is_honeypot,
            "sell_tax_detected": sell_tax_detected,
            "rug_score": score_norm,
            "rug_score_raw": score_raw,
            "lp_locked_pct": lp_locked,
            "liquidity_usd": liquidity,
            "lp_quote_sol": lp_info.get("quote_sol", 0),
            "lp_quote_usd": lp_info.get("quote_usd", 0),
            "lp_unlocked": lp_info.get("lp_unlocked", 0),
            "lp_locked_usd": lp_info.get("lp_locked_usd", 0),
            "market_type": lp_info.get("market_type", ""),
            "mint_authority": mint_authority,
            "freeze_authority": freeze_authority,
            "mutable_metadata": mutable_metadata,
            "danger_risks": len(danger_risks),
            "warn_risks": len(warn_risks),
            "token_name": token_meta.get("name", ""),
            "token_symbol": token_meta.get("symbol", ""),
            "token_meta": token_meta,
            "metadata_uri": token_meta.get("uri") or "",
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
            "creator_migrated_count": creator_migrated,
            "creator_tokens": creator_token_rows[:25],
            "total_holders": total_holders,
            "rugged": rugged,
            "insider_holders": insider_holders[:5],
            # Raw graph flag for bundle detector / Bubblemaps-style clustering
            "graphInsidersDetected": insider_detected,
        }
        # Attach bundle/sniper analysis while we have full holder data
        try:
            from services.bundle_sniper import analyze_bundle_and_snipers

            result["bundleSniper"] = analyze_bundle_and_snipers(
                result,
                pump_coin,
                {},
            )
            if result["bundleSniper"].get("hard_reject"):
                result["passed"] = False
                tag = f"BUNDLE/SNIPER: {result['bundleSniper'].get('summary')}"
                if tag not in result.get("issues", []):
                    result.setdefault("issues", []).append(tag)
        except Exception:
            pass
        result = self._merge_padre_audit(result, padre_audit)
        return self._apply_avoid_filters(result, pump_coin, mint)

    @staticmethod
    def _extract_lp_info(
        markets: list, summary: dict, on_bonding_curve: bool
    ) -> dict[str, Any]:
        """Parse RugCheck markets[].lp for real SOL/USD + lock state."""
        best = {
            "quote_sol": 0.0,
            "quote_usd": 0.0,
            "base_usd": 0.0,
            "lp_locked_pct": None,
            "lp_unlocked": 0.0,
            "lp_locked": 0.0,
            "lp_locked_usd": 0.0,
            "market_type": "",
        }
        if summary.get("lpLockedPct") is not None:
            try:
                best["lp_locked_pct"] = float(summary["lpLockedPct"])
            except (TypeError, ValueError):
                pass

        for m in markets or []:
            lp = m.get("lp") or {}
            if not lp and not m.get("marketType"):
                continue
            try:
                quote = float(lp.get("quote") or 0)
                quote_usd = float(lp.get("quoteUSD") or 0)
                base_usd = float(lp.get("baseUSD") or 0)
                locked_pct = lp.get("lpLockedPct")
                unlocked = float(lp.get("lpUnlocked") or 0)
                locked = float(lp.get("lpLocked") or 0)
                locked_usd = float(lp.get("lpLockedUSD") or 0)
            except (TypeError, ValueError):
                continue
            # Prefer market with more quote SOL (real exit liquidity)
            if quote >= best["quote_sol"] or quote_usd >= best["quote_usd"]:
                best.update(
                    {
                        "quote_sol": quote,
                        "quote_usd": quote_usd,
                        "base_usd": base_usd,
                        "lp_unlocked": unlocked,
                        "lp_locked": locked,
                        "lp_locked_usd": locked_usd,
                        "market_type": str(m.get("marketType") or ""),
                    }
                )
                if locked_pct is not None:
                    try:
                        best["lp_locked_pct"] = float(locked_pct)
                    except (TypeError, ValueError):
                        pass
        return best

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
            "mint": mint,
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
            "token_meta": {},
            "metadata_uri": coin.get("uri") or coin.get("metadata_uri") or "",
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
        result = SolanaAnalyzer._merge_padre_audit(result, padre_audit)
        return SolanaAnalyzer._apply_avoid_filters(result, coin, mint)

    @staticmethod
    def _apply_avoid_filters(
        result: dict[str, Any],
        pump_coin: dict | None,
        mint: str,
        pair: dict | None = None,
    ) -> dict[str, Any]:
        avoid = analyze_avoid_flags(result, pump_coin, mint=mint, pair=pair)
        result["avoid"] = avoid
        if avoid.get("avoid"):
            result["passed"] = False
            for reason in avoid.get("reasons") or []:
                tag = f"AVOID: {reason}"
                if tag not in result.get("issues", []):
                    result.setdefault("issues", []).append(tag)
        return result

    @staticmethod
    def _no_data(mint: str) -> dict:
        return {
            "chain": "solana",
            "type": "solana",
            "passed": False,
            "error": True,
            "issues": [f"No RugCheck data for {mint[:10]}..."],
        }

    @staticmethod
    def _error(mint: str, msg: str) -> dict:
        return {
            "chain": "solana",
            "type": "solana",
            "passed": False,
            "error": True,
            "issues": [f"Analysis error: {msg[:100]}"],
        }
