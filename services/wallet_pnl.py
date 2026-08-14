"""Wallet PnL for FOMO KOL dropdown (1d / 7d / 30d).

Sources (first hit wins per field, merged):
  1. Optional BIRDEYE_API_KEY
  2. Optional CIELO_API_KEY / CIELO_API_TOKEN
  3. Local FOMO realized exits (only trades we observed)

Cached in-memory ~15 min to avoid hammering providers.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from config import HELIUS_API_KEY
from services.http_client import get_client

logger = logging.getLogger("moon-scanner.wallet-pnl")

# Optional keys (not always in config yet)
import os

BIRDEYE_API_KEY = (os.getenv("BIRDEYE_API_KEY") or "").strip()
CIELO_API_KEY = (
    os.getenv("CIELO_API_KEY") or os.getenv("CIELO_API_TOKEN") or ""
).strip()

_CACHE_TTL = 15 * 60.0
_cache: dict[str, dict[str, Any]] = {}


def _f(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _empty_pnl(address: str, label: str = "") -> dict[str, Any]:
    return {
        "address": address,
        "label": label,
        "pnl_1d": None,
        "pnl_7d": None,
        "pnl_30d": None,
        "pnl_1d_pct": None,
        "pnl_7d_pct": None,
        "pnl_30d_pct": None,
        "source": None,
        "updated": time.time(),
        "ok": False,
    }


async def _birdeye_wallet_pnl(address: str) -> dict[str, Any] | None:
    if not BIRDEYE_API_KEY:
        return None
    client = get_client()
    headers = {"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana", "accept": "application/json"}
    # Try net-worth change endpoints / portfolio over windows if available
    out: dict[str, Any] = {}
    for period, key in (("1d", "pnl_1d"), ("7d", "pnl_7d"), ("30d", "pnl_30d")):
        try:
            # Birdeye wallet balance change / PnL (varies by plan)
            url = "https://public-api.birdeye.so/v1/wallet/net_worth_stats"
            r = await client.get(
                url,
                params={"wallet": address, "type": period},
                headers=headers,
                timeout=12.0,
            )
            if r.status_code != 200:
                # fallback endpoint
                r = await client.get(
                    "https://public-api.birdeye.so/v1/wallet/token_list",
                    params={"wallet": address},
                    headers=headers,
                    timeout=12.0,
                )
                if r.status_code != 200:
                    continue
                # token_list doesn't give period PnL
                continue
            data = (r.json() or {}).get("data") or r.json() or {}
            # Flexible parse
            change = (
                data.get("change")
                or data.get("pnl")
                or data.get("profit")
                or data.get("valueChange")
            )
            if isinstance(change, dict):
                change = change.get("usd") or change.get("value")
            val = _f(change)
            if val is not None:
                out[key] = val
                pct = data.get("changePercent") or data.get("pnlPercent")
                out[key + "_pct"] = _f(pct)
        except Exception as exc:
            logger.debug("birdeye pnl %s %s: %s", address[:6], period, exc)
    if not out:
        return None
    out["source"] = "birdeye"
    out["ok"] = True
    return out


async def _cielo_wallet_pnl(address: str) -> dict[str, Any] | None:
    if not CIELO_API_KEY:
        return None
    client = get_client()
    headers = {
        "Authorization": f"Bearer {CIELO_API_KEY}",
        "Accept": "application/json",
        "User-Agent": "moon-scanner-fomo",
    }
    try:
        r = await client.get(
            f"https://feed-api.cielo.finance/api/v1/profile/{address}/pnl",
            headers=headers,
            timeout=14.0,
        )
        if r.status_code != 200:
            r = await client.get(
                f"https://feed-api.cielo.finance/api/v1/{address}/pnl",
                headers=headers,
                timeout=14.0,
            )
        if r.status_code != 200:
            return None
        data = r.json() or {}
        # Nested data variations
        d = data.get("data") or data
        def pick(*keys: str) -> float | None:
            for k in keys:
                if k in d and d[k] is not None:
                    return _f(d[k])
                # nested periods
                for period_key in ("1d", "24h", "day", "7d", "week", "30d", "month"):
                    sub = d.get(period_key) or d.get(period_key.upper())
                    if isinstance(sub, dict) and k in sub:
                        return _f(sub[k])
            return None

        out = {
            "pnl_1d": pick("pnl_1d", "pnl1d", "realized_1d", "usd_1d"),
            "pnl_7d": pick("pnl_7d", "pnl7d", "realized_7d", "usd_7d"),
            "pnl_30d": pick("pnl_30d", "pnl30d", "realized_30d", "usd_30d"),
            "source": "cielo",
            "ok": True,
        }
        # Map from period objects if present
        for pk, field in (("1d", "pnl_1d"), ("7d", "pnl_7d"), ("30d", "pnl_30d")):
            if out.get(field) is not None:
                continue
            sub = d.get(pk) or {}
            if isinstance(sub, dict):
                out[field] = _f(
                    sub.get("realized_pnl_usd")
                    or sub.get("pnl_usd")
                    or sub.get("pnl")
                    or sub.get("total")
                )
        if all(out.get(k) is None for k in ("pnl_1d", "pnl_7d", "pnl_30d")):
            return None
        return out
    except Exception as exc:
        logger.debug("cielo pnl %s: %s", address[:6], exc)
        return None


def _local_fomo_pnl(address: str) -> dict[str, Any] | None:
    """Rough realized PnL from FOMO exit events we recorded (limited history)."""
    try:
        from services.fomo_watch import status as fomo_status

        events = (fomo_status() or {}).get("events") or []
    except Exception:
        return None
    now = time.time()
    windows = {"pnl_1d": 86400, "pnl_7d": 7 * 86400, "pnl_30d": 30 * 86400}
    sums = {k: 0.0 for k in windows}
    hits = {k: 0 for k in windows}
    for ev in events:
        if str(ev.get("wallet") or "") != address:
            continue
        if str(ev.get("side") or "").lower() not in ("sell",):
            continue
        ts = float(ev.get("ts") or 0)
        entry = _f(ev.get("entry_mcap"))
        exit_m = _f(ev.get("mcap"))
        # Proxy: mcap multiple * notional unknown → use % move as signal only
        if entry and exit_m and entry > 0:
            # Without size, approximate "units" of R as mcap return * 100 (display unit)
            ret = (exit_m / entry - 1.0) * 1000.0  # scaled proxy $
        else:
            continue
        age = now - ts
        for key, win in windows.items():
            if age <= win:
                sums[key] += ret
                hits[key] += 1
    if not any(hits.values()):
        return None
    return {
        "pnl_1d": sums["pnl_1d"] if hits["pnl_1d"] else None,
        "pnl_7d": sums["pnl_7d"] if hits["pnl_7d"] else None,
        "pnl_30d": sums["pnl_30d"] if hits["pnl_30d"] else None,
        "source": "fomo_local",
        "ok": True,
        "note": "Proxy from FOMO-tracked exits only (not full wallet PnL)",
    }


async def fetch_wallet_pnl(address: str, *, label: str = "", force: bool = False) -> dict[str, Any]:
    addr = (address or "").strip()
    if not addr:
        return _empty_pnl("", label)
    now = time.time()
    cached = _cache.get(addr)
    if cached and not force and now - float(cached.get("updated") or 0) < _CACHE_TTL:
        out = dict(cached)
        if label:
            out["label"] = label
        return out

    base = _empty_pnl(addr, label)
    for fetcher in (_cielo_wallet_pnl, _birdeye_wallet_pnl):
        try:
            got = await fetcher(addr)
        except Exception:
            got = None
        if got and got.get("ok"):
            base.update({k: v for k, v in got.items() if v is not None or k == "source"})
            base["ok"] = True
            base["address"] = addr
            base["label"] = label or base.get("label") or ""
            base["updated"] = now
            _cache[addr] = base
            return base

    local = _local_fomo_pnl(addr)
    if local and local.get("ok"):
        base.update({k: v for k, v in local.items() if v is not None or k in ("source", "note")})
        base["ok"] = True
        base["address"] = addr
        base["label"] = label
        base["updated"] = now
        _cache[addr] = base
        return base

    base["source"] = None
    base["note"] = (
        "PnL unavailable — set BIRDEYE_API_KEY or CIELO_API_KEY for live 1d/7d/30d"
        if not (BIRDEYE_API_KEY or CIELO_API_KEY)
        else "PnL provider returned no data"
    )
    base["updated"] = now
    # still cache misses briefly
    _cache[addr] = base
    return base


async def fetch_pnl_for_wallets(
    wallets: list[dict[str, Any]],
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Attach pnl fields to wallet rows for dropdown/API."""
    import asyncio

    async def one(w: dict[str, Any]) -> dict[str, Any]:
        addr = str(w.get("address") or "")
        label = str(w.get("label") or "")
        pnl = await fetch_wallet_pnl(addr, label=label, force=force)
        row = dict(w)
        row["pnl"] = {
            "1d": pnl.get("pnl_1d"),
            "7d": pnl.get("pnl_7d"),
            "30d": pnl.get("pnl_30d"),
            "1d_pct": pnl.get("pnl_1d_pct"),
            "7d_pct": pnl.get("pnl_7d_pct"),
            "30d_pct": pnl.get("pnl_30d_pct"),
            "source": pnl.get("source"),
            "note": pnl.get("note"),
            "ok": pnl.get("ok"),
        }
        # flat for easy UI
        row["pnl_1d"] = pnl.get("pnl_1d")
        row["pnl_7d"] = pnl.get("pnl_7d")
        row["pnl_30d"] = pnl.get("pnl_30d")
        row["pnl_source"] = pnl.get("source")
        return row

    # Bound concurrency
    out: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(4)

    async def guarded(w: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await one(w)

    results = await asyncio.gather(
        *[guarded(w) for w in wallets],
        return_exceptions=True,
    )
    for w, r in zip(wallets, results):
        if isinstance(r, Exception):
            row = dict(w)
            row["pnl_1d"] = row["pnl_7d"] = row["pnl_30d"] = None
            row["pnl"] = {"ok": False}
            out.append(row)
        else:
            out.append(r)
    return out


def fmt_pnl_usd(v: Any) -> str:
    try:
        if v is None:
            return "—"
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if n >= 0 else "-"
    a = abs(n)
    if a >= 1_000_000:
        return f"{sign}${a / 1e6:.2f}M"
    if a >= 1_000:
        return f"{sign}${a / 1e3:.1f}k"
    return f"{sign}${a:.0f}"
