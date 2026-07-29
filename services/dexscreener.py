"""DexScreener API client for token discovery and market data."""

from __future__ import annotations

import asyncio
from typing import Any

from config import REQUEST_TIMEOUT, USER_AGENT
from services.http_client import get as http_get

BASE_URL = "https://api.dexscreener.com"


class DexScreenerClient:
    def __init__(self) -> None:
        self._headers = {"User-Agent": USER_AGENT}

    async def _get(self, path: str) -> Any:
        resp = await http_get(
            f"{BASE_URL}{path}",
            headers=self._headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_latest_profiles(self) -> list[dict]:
        data = await self._get("/token-profiles/latest/v1")
        return data if isinstance(data, list) else []

    async def get_latest_boosts(self) -> list[dict]:
        data = await self._get("/token-boosts/latest/v1")
        return data if isinstance(data, list) else []

    async def get_top_boosts(self) -> list[dict]:
        data = await self._get("/token-boosts/top/v1")
        return data if isinstance(data, list) else []

    async def get_token_pairs(
        self, chain_id: str, token_address: str
    ) -> list[dict]:
        data = await self._get(
            f"/token-pairs/v1/{chain_id}/{token_address}"
        )
        return data if isinstance(data, list) else []

    async def search_pairs(self, query: str) -> list[dict]:
        data = await self._get(f"/latest/dex/search?q={query}")
        return data.get("pairs", []) if isinstance(data, dict) else []

    async def discover_tokens(
        self, chains: list[str] | None = None, limit: int = 50
    ) -> list[dict]:
        """Aggregate candidates from profiles and boosts, deduplicated."""
        profiles, boosts, top_boosts = await asyncio.gather(
            self.get_latest_profiles(),
            self.get_latest_boosts(),
            self.get_top_boosts(),
        )

        seen: set[str] = set()
        candidates: list[dict] = []

        for source_list, source in (
            (profiles, "profile"),
            (boosts, "boost"),
            (top_boosts, "top_boost"),
        ):
            for item in source_list:
                chain = item.get("chainId", "")
                addr = item.get("tokenAddress", "")
                if not chain or not addr:
                    continue
                if chains and chain not in chains:
                    continue
                key = f"{chain}:{addr.lower()}"
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "chainId": chain,
                        "tokenAddress": addr,
                        "source": source,
                        "url": item.get("url", ""),
                        "description": item.get("description", ""),
                        "links": item.get("links", []),
                        "icon": item.get("icon", ""),
                    }
                )
                if len(candidates) >= limit:
                    return candidates

        return candidates

    @staticmethod
    def pick_best_pair(pairs: list[dict]) -> dict | None:
        if not pairs:
            return None
        return max(
            pairs,
            key=lambda p: float(
                (p.get("liquidity") or {}).get("usd") or 0
            ),
        )