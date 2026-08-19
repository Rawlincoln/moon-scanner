"""Persist Moons "Checked but filtered" tokens for 6 hours.

Surfaces rejected charts with mint so the UI can link Padre Terminal.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR

logger = logging.getLogger("moon-scanner.moon-filtered")

_PATH = Path(DATA_DIR) / "moon_filtered.json"
_TTL_SEC = 6 * 3600.0
_MAX = 80
_cache: list[dict[str, Any]] | None = None


def _load() -> list[dict[str, Any]]:
    global _cache
    if _cache is not None:
        return list(_cache)
    rows: list[dict[str, Any]] = []
    try:
        if _PATH.is_file():
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            rows = list(raw.get("tokens") or []) if isinstance(raw, dict) else []
    except Exception as exc:
        logger.debug("moon filtered load: %s", exc)
    now = time.time()
    rows = [
        r
        for r in rows
        if isinstance(r, dict)
        and r.get("tokenAddress")
        and now - float(r.get("ts") or 0) < _TTL_SEC
    ]
    _cache = rows
    return list(rows)


def _save(rows: list[dict[str, Any]]) -> None:
    global _cache
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated": time.time(),
            "ttl_sec": _TTL_SEC,
            "tokens": rows[:_MAX],
        }
        _PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _cache = list(rows[:_MAX])
    except Exception as exc:
        logger.warning("moon filtered save failed: %s", exc)


def _row(m: dict[str, Any], *, now: float) -> dict[str, Any] | None:
    mint = str(m.get("tokenAddress") or m.get("mint") or "").strip()
    if not mint or len(mint) < 32:
        return None
    try:
        mcap = float(m.get("mcap_usd") or 0) or None
    except (TypeError, ValueError):
        mcap = None
    try:
        age = float(m.get("age_minutes")) if m.get("age_minutes") is not None else None
    except (TypeError, ValueError):
        age = None
    return {
        "tokenAddress": mint,
        "mint": mint,
        "symbol": str(m.get("symbol") or "?")[:24],
        "name": str(m.get("name") or "")[:48],
        "mcap_usd": mcap,
        "ath_mcap": m.get("ath_mcap"),
        "age_minutes": age,
        "reject": str(m.get("reject") or m.get("reject_key") or "filtered")[:160],
        "reject_key": str(m.get("reject_key") or "")[:40],
        "padre_url": f"https://trade.padre.gg/trade/solana/{mint}",
        "pump_url": f"https://pump.fun/coin/{mint}",
        "ts": float(m.get("ts") or now),
    }


def remember_filtered(misses: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    """Merge this scan's near-misses into the 6h list. Returns full list (newest first)."""
    now = time.time()
    by_mint: dict[str, dict[str, Any]] = {}
    for r in _load():
        mint = str(r.get("tokenAddress") or "")
        if mint:
            by_mint[mint] = r

    for m in (misses or [])[:limit]:
        if not isinstance(m, dict):
            continue
        row = _row(m, now=now)
        if not row:
            continue
        prev = by_mint.get(row["tokenAddress"])
        # Refresh ts + reject reason on re-see
        if prev:
            row["ts"] = now
        by_mint[row["tokenAddress"]] = row

    rows = sorted(by_mint.values(), key=lambda r: -float(r.get("ts") or 0))
    rows = [r for r in rows if now - float(r.get("ts") or 0) < _TTL_SEC]
    _save(rows)
    return rows[:_MAX]


def list_filtered(*, limit: int = 40) -> list[dict[str, Any]]:
    now = time.time()
    rows = [
        r
        for r in _load()
        if now - float(r.get("ts") or 0) < _TTL_SEC
    ]
    rows.sort(key=lambda r: -float(r.get("ts") or 0))
    return rows[:limit]


def clear_cache() -> None:
    global _cache
    _cache = None
