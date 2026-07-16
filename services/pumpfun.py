"""pump.fun API client — real-time new Solana launches on bonding curve."""

from __future__ import annotations

import time
from typing import Any

import httpx

from config import PUMPFUN_API_URL, REQUEST_TIMEOUT

PUMP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Origin": "https://pump.fun",
    "Referer": "https://pump.fun/",
}

# Tokens start with ~793.1T base units in the bonding curve reserve
_INITIAL_REAL_TOKEN_RESERVES = 793_100_000_000_000
_GRADUATION_MCAP_USD = 69_000


class PumpFunClient:
    def __init__(self) -> None:
        self._base = PUMPFUN_API_URL

    async def get_latest_coins(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        params = {
            "limit": limit,
            "offset": offset,
            "sort": "created_timestamp",
            "order": "DESC",
            "includeNsfw": "false",
        }
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, headers=PUMP_HEADERS
        ) as client:
            resp = await client.get(f"{self._base}/coins", params=params)
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, list) else []

    async def get_coin(self, mint: str) -> dict | None:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, headers=PUMP_HEADERS
        ) as client:
            resp = await client.get(f"{self._base}/coins/{mint}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def coin_age_minutes(coin: dict) -> float:
        ts = coin.get("created_timestamp") or 0
        if not ts:
            return 9999.0
        return (time.time() * 1000 - ts) / 60_000

    @staticmethod
    def bonding_progress(coin: dict) -> float:
        """0-100% progress toward Raydium graduation."""
        mcap = float(coin.get("usd_market_cap") or 0)
        if mcap > 0:
            return min(100.0, (mcap / _GRADUATION_MCAP_USD) * 100)

        reserves = float(coin.get("real_token_reserves") or 0)
        if reserves > 0:
            sold = 1 - (reserves / _INITIAL_REAL_TOKEN_RESERVES)
            return min(100.0, max(0.0, sold * 100))
        return 0.0

    @staticmethod
    def to_candidate(coin: dict) -> dict:
        mint = coin.get("mint", "")
        return {
            "chainId": "solana",
            "tokenAddress": mint,
            "source": "pump.fun",
            "url": f"https://pump.fun/coin/{mint}",
            "description": coin.get("description", ""),
            "links": _extract_links(coin),
            "icon": coin.get("image_uri", ""),
            "pumpfun": coin,
        }

    @staticmethod
    def to_market_pair(coin: dict, dex_pair: dict | None = None) -> dict:
        """Build a pair dict compatible with scorer/signals. Dex data merged if available."""
        mint = coin.get("mint", "")
        created = coin.get("created_timestamp")
        mcap = float(coin.get("usd_market_cap") or 0)
        progress = PumpFunClient.bonding_progress(coin)
        age_min = PumpFunClient.coin_age_minutes(coin)

        if dex_pair:
            pair = dict(dex_pair)
            pair.setdefault("baseToken", {})
            pair["baseToken"].setdefault("address", mint)
            pair["baseToken"].setdefault("name", coin.get("name", ""))
            pair["baseToken"].setdefault("symbol", coin.get("symbol", ""))
            pair["pumpfun"] = coin
            return pair

        # Synthetic pair from live pump.fun stats (no DexScreener listing yet)
        virtual_sol = float(coin.get("virtual_sol_reserves") or 0) / 1e9
        sol_price_est = 140.0  # rough USD estimate for display liquidity
        liq_usd = max(virtual_sol * sol_price_est * 0.1, mcap * 0.05)

        return {
            "chainId": "solana",
            "dexId": "pumpfun",
            "url": f"https://pump.fun/coin/{mint}",
            "pairAddress": coin.get("bonding_curve", ""),
            "baseToken": {
                "address": mint,
                "name": coin.get("name", ""),
                "symbol": coin.get("symbol", ""),
            },
            "quoteToken": {"symbol": "SOL", "name": "Solana"},
            "priceUsd": str(mcap / 1_000_000_000) if mcap else "0",
            "priceChange": {
                "m5": min(progress * 2, 50) if age_min < 15 else 0,
                "h1": min(progress * 3, 80) if age_min < 60 else 0,
                "h6": 0,
                "h24": 0,
            },
            "volume": {
                "m5": mcap * 0.1 if age_min < 10 else 0,
                "h1": mcap * 0.3,
                "h24": mcap,
            },
            "liquidity": {"usd": liq_usd},
            "marketCap": mcap,
            "fdv": mcap,
            "pairCreatedAt": created,
            "txns": {
                "m5": {"buys": coin.get("reply_count", 0), "sells": 0},
                "h1": {"buys": max(coin.get("reply_count", 0), 1), "sells": 0},
                "h24": {"buys": coin.get("reply_count", 0), "sells": 0},
            },
            "pumpfun": coin,
            "is_pumpfun_synthetic": True,
        }


def _extract_links(coin: dict) -> list[dict]:
    links = []
    for key, label in (
        ("website", "Website"),
        ("twitter", "Twitter"),
        ("telegram", "Telegram"),
    ):
        val = coin.get(key)
        if val:
            links.append({"label": label, "url": val})
    return links