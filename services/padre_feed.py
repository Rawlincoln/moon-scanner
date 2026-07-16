"""Padre Trenches / New Pairs / Trending feed — merged with pump.fun.

Padre's live gaze WebSocket requires authenticated sessions. This module
proxies the same intent using public APIs:
  - pump.fun  → Trenches NEW, Almost Bonded, Recently Bonded
  - DexScreener → Trending (boosts) + New Pairs (profiles)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from config import MCAP_INVEST_MAX_USD, MCAP_INVEST_MIN_USD, REQUEST_TIMEOUT
from services.dexscreener import DexScreenerClient
from services.pumpfun import PumpFunClient
from services.trench_analyzer import is_approaching_6k_candidate

# Maps to Padre Trenches column names shown in trade.padre.gg/trenches
GAZE_NEW = "padre_trenches_new"
GAZE_ALMOST_BONDED = "padre_trenches_almost_bonded"
GAZE_RECENTLY_BONDED = "padre_trenches_recently_bonded"
GAZE_TRENDING = "padre_trending"
GAZE_NEW_PAIRS = "padre_new_pairs"
GAZE_APPROACHING_6K = "approaching_6k"
SOURCE_PUMPFUN = "pump.fun"

_ALMOST_BONDED_MIN_PCT = 75.0
_RECENT_BONDED_MAX_AGE_MIN = 180.0


class PadreFeedClient:
    def __init__(self) -> None:
        self.pump = PumpFunClient()
        self.dex = DexScreenerClient()

    async def discover_unified(
        self,
        limit: int = 50,
        max_age_minutes: float = 30.0,
        exclude_graduated: bool = True,
    ) -> list[dict]:
        """Return deduplicated candidates tagged with Padre + pump.fun sources."""
        tasks = [
            self._from_pumpfun_approaching_6k(limit * 2, max_age_minutes),
            self._from_pumpfun_active_climbers(limit * 2, max_age_minutes),
            self._from_pumpfun_almost_bonded(limit, max_age_minutes),
            self._from_dex_trending(limit),
            self._from_dex_new_pairs(limit),
        ]
        batches = await asyncio.gather(*tasks, return_exceptions=True)

        merged: dict[str, dict] = {}
        for batch in batches:
            if isinstance(batch, Exception):
                continue
            for cand in batch:
                key = cand["tokenAddress"]
                if key in merged:
                    existing = merged[key]
                    for src in cand.get("sources", []):
                        if src not in existing["sources"]:
                            existing["sources"].append(src)
                    if cand.get("pumpfun") and not existing.get("pumpfun"):
                        existing["pumpfun"] = cand["pumpfun"]
                    existing["source_overlap"] = len(existing["sources"])
                    if cand.get("icon") and not existing.get("icon"):
                        existing["icon"] = cand["icon"]
                    if cand.get("description") and not existing.get("description"):
                        existing["description"] = cand["description"]
                else:
                    cand["source_overlap"] = len(cand.get("sources", []))
                    merged[key] = cand

        results = list(merged.values())
        results.sort(
            key=lambda c: (
                -c.get("_mcap_closeness", 0),
                -c.get("source_overlap", 0),
                c.get("_sort_priority", 99),
            )
        )
        return results[: limit * 2]

    async def fetch_trenches_columns(
        self,
        per_column: int = 20,
        max_age_minutes: float = 30.0,
    ) -> dict[str, list[dict]]:
        """Mirror Padre Trenches columns: NEW, Almost Bonded, Recently Bonded."""
        new_task = self._from_pumpfun_new(
            per_column * 2, max_age_minutes, exclude_graduated=True
        )
        almost_task = self._from_pumpfun_almost_bonded(per_column, max_age_minutes)
        bonded_task = self._from_pumpfun_recently_bonded(per_column, max_age_minutes)
        new, almost, bonded = await asyncio.gather(new_task, almost_task, bonded_task)

        def dedup(items: list[dict]) -> list[dict]:
            seen: set[str] = set()
            out: list[dict] = []
            for c in items:
                mint = c.get("tokenAddress", "")
                if not mint or mint in seen:
                    continue
                seen.add(mint)
                out.append(c)
            return out[:per_column]

        return {
            "new": dedup(new),
            "almost_bonded": dedup(almost),
            "recently_bonded": dedup(bonded),
        }

    async def _fetch_pump_sorted(
        self, sort: str, limit: int
    ) -> list[dict]:
        try:
            async with __import__("httpx").AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Origin": "https://pump.fun",
                },
            ) as client:
                resp = await client.get(
                    "https://frontend-api-v3.pump.fun/coins",
                    params={
                        "limit": limit,
                        "sort": sort,
                        "order": "DESC",
                        "includeNsfw": "false",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, list) else []
        except Exception:
            pass
        return []

    async def _from_pumpfun_approaching_6k(
        self, limit: int, max_age_minutes: float
    ) -> list[dict]:
        """Tokens in $4k–$7.5k band — the trencher sweet spot."""
        coins = await self._fetch_pump_sorted("market_cap", limit * 3)
        coins += await self._fetch_pump_sorted("last_trade_timestamp", limit * 3)
        out: list[dict] = []
        seen: set[str] = set()
        for coin in coins:
            mint = coin.get("mint", "")
            if not mint or mint in seen:
                continue
            seen.add(mint)
            if not is_approaching_6k_candidate(coin):
                continue
            age = self.pump.coin_age_minutes(coin)
            if age > max_age_minutes:
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_APPROACHING_6K]
            cand["_sort_priority"] = 0
            cand["_age_minutes"] = age
            cand["_mcap_closeness"] = max(
                0, 100 - abs(mcap - 6000) / 60
            )
            out.append(cand)
        return out[:limit]

    async def _from_pumpfun_active_climbers(
        self, limit: int, max_age_minutes: float
    ) -> list[dict]:
        """Recently traded tokens climbing toward $6k."""
        coins = await self._fetch_pump_sorted("last_trade_timestamp", limit * 4)
        out: list[dict] = []
        for coin in coins:
            if coin.get("complete") or coin.get("is_banned"):
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            if mcap < MCAP_INVEST_MIN_USD * 0.9 or mcap > MCAP_INVEST_MAX_USD:
                continue
            age = self.pump.coin_age_minutes(coin)
            if age > max_age_minutes or age < 2:
                continue
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_APPROACHING_6K]
            cand["_sort_priority"] = 0
            cand["_age_minutes"] = age
            cand["_mcap_closeness"] = max(0, 100 - abs(mcap - 6000) / 60)
            out.append(cand)
        return out[:limit]

    async def _from_pumpfun_new(
        self,
        limit: int,
        max_age_minutes: float,
        exclude_graduated: bool,
    ) -> list[dict]:
        coins = await self.pump.get_latest_coins(limit=limit)
        out: list[dict] = []
        for coin in coins:
            age = self.pump.coin_age_minutes(coin)
            if age > max_age_minutes:
                continue
            if exclude_graduated and coin.get("complete"):
                continue
            if coin.get("is_banned"):
                continue
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_NEW]
            cand["_sort_priority"] = 0
            cand["_age_minutes"] = age
            out.append(cand)
        return out

    async def _from_pumpfun_almost_bonded(
        self, limit: int, max_age_minutes: float
    ) -> list[dict]:
        coins = await self.pump.get_latest_coins(
            limit=limit * 4, offset=0
        )
        # Also pull by market cap for curve-near-graduation tokens
        try:
            async with __import__("httpx").AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Origin": "https://pump.fun",
                },
            ) as client:
                resp = await client.get(
                    "https://frontend-api-v3.pump.fun/coins",
                    params={
                        "limit": limit * 2,
                        "sort": "market_cap",
                        "order": "DESC",
                        "includeNsfw": "false",
                    },
                )
                if resp.status_code == 200:
                    extra = resp.json()
                    if isinstance(extra, list):
                        seen = {c.get("mint") for c in coins}
                        for c in extra:
                            if c.get("mint") not in seen:
                                coins.append(c)
        except Exception:
            pass

        out: list[dict] = []
        for coin in coins:
            if coin.get("complete") or coin.get("is_banned"):
                continue
            progress = self.pump.bonding_progress(coin)
            age = self.pump.coin_age_minutes(coin)
            if age > max_age_minutes * 3:
                continue
            if progress < _ALMOST_BONDED_MIN_PCT:
                continue
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_ALMOST_BONDED]
            cand["_sort_priority"] = 1
            cand["_age_minutes"] = age
            cand["bonding_progress"] = progress
            out.append(cand)
        return out[:limit]

    async def _from_pumpfun_recently_bonded(
        self, limit: int, max_age_minutes: float
    ) -> list[dict]:
        try:
            async with __import__("httpx").AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Origin": "https://pump.fun",
                },
            ) as client:
                resp = await client.get(
                    "https://frontend-api-v3.pump.fun/coins",
                    params={
                        "limit": limit * 3,
                        "sort": "last_trade_timestamp",
                        "order": "DESC",
                        "includeNsfw": "false",
                    },
                )
                coins = resp.json() if resp.status_code == 200 else []
        except Exception:
            coins = []

        out: list[dict] = []
        for coin in coins if isinstance(coins, list) else []:
            if not coin.get("complete"):
                continue
            age = self.pump.coin_age_minutes(coin)
            if age > _RECENT_BONDED_MAX_AGE_MIN:
                continue
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_RECENTLY_BONDED]
            cand["_sort_priority"] = 2
            cand["_age_minutes"] = age
            out.append(cand)
        return out[:limit]

    async def _from_dex_trending(self, limit: int) -> list[dict]:
        """DexScreener boosts ≈ Padre Trending (high attention tokens)."""
        boosts, top = await asyncio.gather(
            self.dex.get_latest_boosts(),
            self.dex.get_top_boosts(),
        )
        out: list[dict] = []
        seen: set[str] = set()

        for item in top + boosts:
            chain = item.get("chainId", "")
            addr = item.get("tokenAddress", "")
            if chain != "solana" or not addr or addr in seen:
                continue
            seen.add(addr)
            out.append(
                {
                    "chainId": chain,
                    "tokenAddress": addr,
                    "sources": [GAZE_TRENDING],
                    "source": GAZE_TRENDING,
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                    "icon": item.get("icon", ""),
                    "_sort_priority": 3,
                    "_age_minutes": 9999,
                }
            )
            if len(out) >= limit:
                break

        # Supplement with high 1h-volume pump tokens from DexScreener search
        if len(out) < limit:
            try:
                pairs = await self.dex.search_pairs("pumpfun")
                pairs.sort(
                    key=lambda p: float(
                        (p.get("volume") or {}).get("h1") or 0
                    ),
                    reverse=True,
                )
                for pair in pairs[: limit * 2]:
                    base = pair.get("baseToken") or {}
                    addr = base.get("address", "")
                    if not addr or addr in seen:
                        continue
                    if not addr.lower().endswith("pump"):
                        continue
                    seen.add(addr)
                    out.append(
                        {
                            "chainId": "solana",
                            "tokenAddress": addr,
                            "sources": [GAZE_TRENDING],
                            "source": GAZE_TRENDING,
                            "url": pair.get("url", ""),
                            "description": "",
                            "icon": "",
                            "_sort_priority": 3,
                            "_age_minutes": _pair_age_minutes(pair),
                            "_dex_pair": pair,
                        }
                    )
                    if len(out) >= limit:
                        break
            except Exception:
                pass

        return out

    async def _from_dex_new_pairs(self, limit: int) -> list[dict]:
        profiles = await self.dex.get_latest_profiles()
        out: list[dict] = []
        for item in profiles:
            chain = item.get("chainId", "")
            addr = item.get("tokenAddress", "")
            if chain != "solana" or not addr:
                continue
            out.append(
                {
                    "chainId": chain,
                    "tokenAddress": addr,
                    "sources": [GAZE_NEW_PAIRS],
                    "source": GAZE_NEW_PAIRS,
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                    "icon": item.get("icon", ""),
                    "_sort_priority": 4,
                    "_age_minutes": 9999,
                }
            )
            if len(out) >= limit:
                break
        return out


def _pair_age_minutes(pair: dict) -> float:
    created = pair.get("pairCreatedAt")
    if not created:
        return 9999.0
    return (time.time() * 1000 - created) / 60_000


def source_label(source: str) -> str:
    labels = {
        SOURCE_PUMPFUN: "pump.fun",
        GAZE_NEW: "Padre NEW",
        GAZE_ALMOST_BONDED: "Padre Almost Bonded",
        GAZE_RECENTLY_BONDED: "Padre Recently Bonded",
        GAZE_TRENDING: "Padre Trending",
        GAZE_NEW_PAIRS: "Padre New Pairs",
        GAZE_APPROACHING_6K: "Approaching $6K",
    }
    return labels.get(source, source)