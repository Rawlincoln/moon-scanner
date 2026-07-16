"""Token discovery — pump.fun for early Solana, DexScreener for EVM."""

from __future__ import annotations

import time
from typing import Any

from config import (
    DEFAULT_MAX_AGE_MINUTES,
    EARLY_MCAP_MAX_USD,
    EARLY_MCAP_MIN_USD,
    EXCLUDE_GRADUATED_DEFAULT,
    MCAP_INVEST_MAX_USD,
    MCAP_INVEST_MIN_USD,
)
from services.trench_analyzer import is_approaching_6k_candidate
from services.dexscreener import DexScreenerClient
from services.padre_feed import PadreFeedClient
from services.pumpfun import PumpFunClient


def is_dead_token(
    candidate: dict, pair: dict | None, max_age_minutes: float
) -> bool:
    """Filter tokens that already pumped and died."""
    pump = candidate.get("pumpfun") or (pair or {}).get("pumpfun")
    if pump:
        age = PumpFunClient.coin_age_minutes(pump)
        mcap = float(pump.get("usd_market_cap") or 0)
        replies = int(pump.get("reply_count") or 0)
        if pump.get("complete") and age > max_age_minutes:
            return True
        if age > max_age_minutes * 2 and mcap < 2000 and replies == 0:
            return True
        return False

    if not pair:
        return True

    created = pair.get("pairCreatedAt")
    if not created:
        return False

    age_hours = (time.time() * 1000 - created) / 3_600_000
    changes = pair.get("priceChange") or {}
    h24 = float(changes.get("h24") or 0)
    vol = pair.get("volume") or {}
    vol_h24 = float(vol.get("h24") or 0)

    if age_hours > 24 and h24 < -60:
        return True
    if age_hours > 6 and h24 < -40 and vol_h24 < 500:
        return True
    return False


def is_early_eligible(
    candidate: dict,
    pair: dict | None,
    max_age_minutes: float,
    exclude_graduated: bool,
) -> tuple[bool, str]:
    pump = candidate.get("pumpfun") or (pair or {}).get("pumpfun")
    if pump:
        age = PumpFunClient.coin_age_minutes(pump)
        if age > max_age_minutes:
            return False, f"Too old ({age:.0f}m > {max_age_minutes:.0f}m)"
        if exclude_graduated and pump.get("complete"):
            return False, "Graduated from bonding curve"
        if pump.get("is_banned"):
            return False, "Banned on pump.fun"
        if not is_approaching_6k_candidate(pump):
            mcap = float(pump.get("usd_market_cap") or 0)
            return (
                False,
                f"Not approaching $6k (mcap ${mcap:,.0f}, "
                f"need ${MCAP_INVEST_MIN_USD:,.0f}–${MCAP_INVEST_MAX_USD:,.0f})",
            )
        return True, "approaching $6k on bonding curve"

    if not pair or not pair.get("pairCreatedAt"):
        return False, "No pair age data"

    age_min = (time.time() * 1000 - pair["pairCreatedAt"]) / 60_000
    if age_min > max_age_minutes:
        return False, f"Too old ({age_min:.0f}m)"
    return True, "evm early pair"


class DiscoveryService:
    def __init__(self) -> None:
        self.pump = PumpFunClient()
        self.dex = DexScreenerClient()
        self.padre_feed = PadreFeedClient()

    async def discover_early(
        self,
        chains: list[str],
        limit: int,
        max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES,
        exclude_graduated: bool = EXCLUDE_GRADUATED_DEFAULT,
    ) -> list[dict]:
        candidates: list[dict] = []
        seen: set[str] = set()

        if "solana" in chains:
            unified = await self.padre_feed.discover_unified(
                limit=limit * 2,
                max_age_minutes=max_age_minutes,
                exclude_graduated=exclude_graduated,
            )
            for cand in unified:
                mint = cand.get("tokenAddress", "")
                if not mint:
                    continue
                key = mint.lower()
                if key in seen:
                    continue
                seen.add(key)
                cand.setdefault("sources", [cand.get("source", "pump.fun")])
                cand["source"] = ",".join(cand["sources"])
                candidates.append(cand)

        evm_chains = [c for c in chains if c != "solana"]
        if evm_chains:
            dex_cands = await self.dex.discover_tokens(
                evm_chains, limit=limit * 3
            )
            for c in dex_cands:
                key = f"{c['chainId']}:{c['tokenAddress'].lower()}"
                if key in seen:
                    continue
                seen.add(key)
                c["source"] = c.get("source", "dexscreener")
                c["sources"] = [c["source"]]
                candidates.append(c)

        return candidates