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

from config import (
    MCAP_INVEST_MAX_USD,
    MCAP_INVEST_MIN_USD,
    MIGRATION_ALMOST_MIN_PCT,
    MIGRATION_CLIMBING_MIN_PCT,
    MIGRATION_MCAP_MAX_USD,
    MIGRATION_NEAR_MIN_PCT,
    REQUEST_TIMEOUT,
    SCAN_MCAP_FOCUS_MAX_USD,
    SCAN_MCAP_MAX_USD,
    SIXK_ENTRY_SWEET_MAX,
    SIXK_ENTRY_SWEET_MIN,
    SIXK_RADAR_MAX_USD,
    SIXK_RADAR_MIN_USD,
    TARGET_MCAP_USD,
    UNDER25K_MAX_USD,
    UNDER25K_MIN_USD,
)
from services.avoid_filters import BLOCKED_MINTS
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

_ALMOST_BONDED_MIN_PCT = MIGRATION_ALMOST_MIN_PCT  # ~55% ≈ $38k — real pre-migration
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

    @staticmethod
    def _candidate_mcap(cand: dict) -> float:
        pf = cand.get("pumpfun") or {}
        try:
            return float(
                pf.get("usd_market_cap")
                or cand.get("marketCap")
                or cand.get("_mcap")
                or 0
            )
        except (TypeError, ValueError):
            return 0.0

    def _keep_early_mcap(self, cand: dict) -> bool:
        """Drop tokens already past the early window ($25k)."""
        mcap = self._candidate_mcap(cand)
        # Unknown mcap (0) — keep for brand-new launches
        if mcap <= 0:
            return True
        return mcap <= SCAN_MCAP_MAX_USD

    def _keep_migration_mcap(self, cand: dict) -> bool:
        """Allow up to graduation zone for almost-bonded / mid-curve."""
        mcap = self._candidate_mcap(cand)
        if mcap <= 0:
            return True
        return mcap <= MIGRATION_MCAP_MAX_USD

    def _rank_early(self, cand: dict) -> tuple:
        """$6k entry band first, then younger — never high mcap first."""
        mcap = self._candidate_mcap(cand)
        age = float(cand.get("_age_minutes") or cand.get("age_minutes") or 999)
        if SIXK_ENTRY_SWEET_MIN <= mcap <= SIXK_ENTRY_SWEET_MAX:
            band = 0  # ideal $3.5k–$7.5k
        elif SIXK_RADAR_MIN_USD <= mcap < SIXK_ENTRY_SWEET_MIN:
            band = 1  # pre-$6k climb
        elif SIXK_ENTRY_SWEET_MAX < mcap <= SIXK_RADAR_MAX_USD:
            band = 2  # just past 6k — still usable
        elif mcap <= SCAN_MCAP_FOCUS_MAX_USD:
            band = 3
        else:
            band = 4
        closeness = abs(mcap - TARGET_MCAP_USD) if mcap > 0 else 99999
        return (band, closeness, age)

    def _in_sixk_band(self, cand: dict) -> bool:
        mcap = self._candidate_mcap(cand)
        return SIXK_RADAR_MIN_USD <= mcap <= SIXK_RADAR_MAX_USD

    async def fetch_sixk_radar(
        self,
        limit: int = 30,
        max_age_minutes: float = 45.0,
    ) -> list[dict]:
        """Dedicated $2k–$9k climber radar — last-trade first (not brand-new dust)."""
        # Pull large last-trade feed so we catch climbers mid-run, not only brand-new
        coins = await self._fetch_pump_sorted("last_trade_timestamp", max(limit * 8, 80))
        # Second pass: market_cap DESC is wrong for early; still merge last-trade only
        coins2 = await self._fetch_pump_sorted("last_trade_timestamp", max(limit * 4, 40))
        coins = (coins or []) + (coins2 or [])
        out: list[dict] = []
        seen: set[str] = set()
        for coin in coins:
            mint = coin.get("mint", "")
            if not mint or mint in seen or mint in BLOCKED_MINTS:
                continue
            if coin.get("complete") or coin.get("is_banned"):
                continue
            mcap = float(coin.get("usd_market_cap") or 0)
            if mcap < SIXK_RADAR_MIN_USD or mcap > SIXK_RADAR_MAX_USD:
                continue
            age = self.pump.coin_age_minutes(coin)
            if age > max_age_minutes or age < 0.5:
                continue
            seen.add(mint)
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_APPROACHING_6K, "sixk_radar"]
            cand["_sort_priority"] = 0
            cand["_age_minutes"] = age
            cand["_mcap"] = mcap
            cand["_mcap_closeness"] = max(0, 100 - abs(mcap - TARGET_MCAP_USD) / 60)
            cand["_sixk_radar"] = True
            # Quick narrative flags (no RugCheck) for instant feed ranking
            desc = (coin.get("description") or "").lower()
            tw = str(coin.get("twitter") or "").lower()
            web = str(coin.get("website") or "").lower()
            viral = any(h in f"{desc} {tw} {web}" for h in (
                "tiktok.com", "youtube.com", "youtu.be", "instagram.com"
            ))
            own_x = "status/" not in tw and (
                "x.com/" in tw or "twitter.com/" in tw
            )
            # Penalize status-link-only / empty-desc entry traps (CEO of Sex style)
            status_x = "status/" in tw
            empty_desc = len((coin.get("description") or "").strip()) < 8
            adult = any(
                k in f"{(coin.get('name') or '')} {(coin.get('symbol') or '')}".lower()
                for k in (
                    "sex", "porn", "nude", "onlyfans", "xxx", "nsfw", "milf", "hentai",
                )
            )
            if status_x and empty_desc:
                cand["_entry_trap"] = True
            if adult:
                cand["_adult_bait"] = True
            # Near-empty real SOL on curve while still "trading" (USWR-style)
            real_sol = float(coin.get("real_sol_reserves") or 0) / 1e9
            if 0 < real_sol < 0.5 and mcap >= 2000:
                cand["_drained_hint"] = True
            cand["_quick_alpha"] = (
                int(viral) * 2
                + int(own_x and not status_x)
                + int(bool(web and "x.com" not in web and "instagram" not in web))
                - int(status_x) * 3
                - int(adult) * 5
                - int(empty_desc and not viral)
                - int(bool(cand.get("_drained_hint"))) * 4
            )
            out.append(cand)

        # Drop obvious entry traps from radar
        out = [
            c
            for c in out
            if not c.get("_adult_bait")
            and not c.get("_entry_trap")
            and not c.get("_drained_hint")
        ]
        out.sort(
            key=lambda c: (
                0 if SIXK_ENTRY_SWEET_MIN <= self._candidate_mcap(c) <= SIXK_ENTRY_SWEET_MAX else 1,
                abs(self._candidate_mcap(c) - TARGET_MCAP_USD),
                -c.get("_quick_alpha", 0),
                c.get("_age_minutes", 999),
            )
        )
        return out[:limit]

    async def fetch_trenches_columns(
        self,
        per_column: int = 20,
        max_age_minutes: float = 30.0,
    ) -> dict[str, list[dict]]:
        """Trenches + dedicated $6k radar (analyzed first)."""
        radar_limit = max(per_column * 3, 24)
        sixk_task = self.fetch_sixk_radar(
            limit=radar_limit,
            max_age_minutes=max(max_age_minutes, 40),
        )
        new_task = self._from_pumpfun_new(
            per_column * 2, max_age_minutes, exclude_graduated=True
        )
        almost_task = self._from_pumpfun_almost_bonded(per_column, max_age_minutes)
        bonded_task = self._from_pumpfun_recently_bonded(
            max(5, per_column // 2), max_age_minutes
        )
        sixk, new, almost, bonded = await asyncio.gather(
            sixk_task, new_task, almost_task, bonded_task
        )

        def prepare(
            items: list[dict],
            n: int,
            prefer_sixk: bool = False,
            *,
            allow_migration_mcap: bool = False,
            rank_bonding: bool = False,
        ) -> list[dict]:
            seen: set[str] = set()
            out: list[dict] = []
            for c in items:
                mint = c.get("tokenAddress", "")
                if not mint or mint in seen:
                    continue
                if mint in BLOCKED_MINTS:
                    continue
                if allow_migration_mcap:
                    if not self._keep_migration_mcap(c):
                        continue
                elif not self._keep_early_mcap(c):
                    continue
                pf = c.get("pumpfun") or {}
                replies = int(pf.get("reply_count") or 0)
                has_social = bool(
                    pf.get("twitter") or pf.get("telegram") or pf.get("website")
                )
                desc = (pf.get("description") or "").strip()
                if replies == 0 and not has_social and len(desc) < 4:
                    c["_ghost_risk"] = True
                seen.add(mint)
                out.append(c)
            if rank_bonding:
                out.sort(
                    key=lambda x: (
                        1 if x.get("_ghost_risk") else 0,
                        -(float(x.get("bonding_progress") or 0)),
                        -self._candidate_mcap(x),
                        x.get("_age_minutes", 999),
                    )
                )
            else:
                out.sort(
                    key=lambda x: (
                        0 if (prefer_sixk and x.get("_sixk_radar")) else 1,
                        1 if x.get("_ghost_risk") else 0,
                        *self._rank_early(x),
                    )
                )
            return out[:n]

        # Six-k climbers get their own column + overflow into NEW
        sixk_col = prepare(sixk, max(per_column * 2, 16), prefer_sixk=True)
        sixk_mints = {c["tokenAddress"] for c in sixk_col}
        new_rest = [c for c in new if c.get("tokenAddress") not in sixk_mints]
        # Mid-curve under $25k (not pure $6k dust, not yet almost-bonded)
        mid = [
            c
            for c in (almost + new)
            if c.get("tokenAddress") not in sixk_mints
            and UNDER25K_MIN_USD
            <= self._candidate_mcap(c)
            <= UNDER25K_MAX_USD
        ]

        return {
            "sixk_radar": sixk_col,
            "new": prepare(new_rest, per_column),
            "under_25k": prepare(mid, max(per_column, 10), allow_migration_mcap=False),
            "almost_bonded": prepare(
                almost,
                max(per_column, 12),
                allow_migration_mcap=True,
                rank_bonding=True,
            ),
            "recently_bonded": prepare(
                bonded,
                max(5, per_column // 2),
                allow_migration_mcap=True,
            ),
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
            mcap = float(coin.get("usd_market_cap") or 0)
            if mcap > SCAN_MCAP_MAX_USD:
                continue
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_NEW]
            cand["_sort_priority"] = 0
            cand["_age_minutes"] = age
            cand["_mcap"] = mcap
            out.append(cand)
        # Youngest + lowest mcap first
        out.sort(
            key=lambda c: (
                c.get("_age_minutes", 999),
                c.get("_mcap") or 0,
            )
        )
        return out

    async def _from_pumpfun_almost_bonded(
        self, limit: int, max_age_minutes: float
    ) -> list[dict]:
        coins = await self.pump.get_latest_coins(
            limit=limit * 4, offset=0
        )
        # last_trade + market_cap: high mcap on-curve tokens are the real migration set
        for sort_key in ("last_trade_timestamp", "market_cap"):
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
                            "limit": limit * 5,
                            "sort": sort_key,
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
            mcap = float(coin.get("usd_market_cap") or 0)
            # Critical: graduation ~$69k — do NOT cap at $25k or almost-bonded is empty
            if mcap > MIGRATION_MCAP_MAX_USD:
                continue
            progress = self.pump.bonding_progress(coin)
            age = self.pump.coin_age_minutes(coin)
            if age > max_age_minutes * 3:
                continue
            # Include climbing (28%+) and near/almost bonded so migration path is visible
            if progress < MIGRATION_CLIMBING_MIN_PCT:
                continue
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_ALMOST_BONDED]
            cand["_sort_priority"] = 1
            cand["_age_minutes"] = age
            cand["_mcap"] = mcap
            cand["bonding_progress"] = progress
            cand["_migration_lane"] = (
                "almost"
                if progress >= MIGRATION_ALMOST_MIN_PCT
                else "near"
                if progress >= MIGRATION_NEAR_MIN_PCT
                else "climbing"
            )
            out.append(cand)
        # Highest bonding first — these are the ones that can actually migrate
        out.sort(
            key=lambda c: (
                -(float(c.get("bonding_progress") or 0)),
                -(c.get("_mcap") or 0),
                c.get("_age_minutes", 999),
            )
        )
        return out[: max(limit * 2, 16)]

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
            mcap = float(coin.get("usd_market_cap") or 0)
            # Skip dust "complete" corpses and mega bags outside scan window
            if mcap < 15_000 or mcap > MIGRATION_MCAP_MAX_USD * 3:
                continue
            age = self.pump.coin_age_minutes(coin)
            if age > min(_RECENT_BONDED_MAX_AGE_MIN, max_age_minutes * 4):
                continue
            cand = self.pump.to_candidate(coin)
            cand["sources"] = [SOURCE_PUMPFUN, GAZE_RECENTLY_BONDED]
            cand["_sort_priority"] = 2
            cand["_age_minutes"] = age
            cand["_mcap"] = mcap
            cand["bonding_progress"] = 100.0
            cand["_migration_lane"] = "migrated"
            out.append(cand)
        out.sort(key=lambda c: (c.get("_age_minutes", 999), -(c.get("_mcap") or 0)))
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