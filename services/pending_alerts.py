"""Pending alerts — wait for user "I took this trade" before journal/risk slots.

Telegram fires → stash here. Money desk Take → open journal (counts risk).
Skip → dismiss without opening a position.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR

logger = logging.getLogger("moon-scanner.pending-alerts")

_PATH = Path(DATA_DIR) / "pending_alerts.json"
_MAX = 40
_TTL_SEC = 6 * 3600.0
_cache: list[dict[str, Any]] | None = None


def _load() -> list[dict[str, Any]]:
    global _cache
    if _cache is not None:
        return list(_cache)
    rows: list[dict[str, Any]] = []
    try:
        if _PATH.is_file():
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            rows = list(raw.get("alerts") or []) if isinstance(raw, dict) else []
    except Exception as exc:
        logger.debug("pending load: %s", exc)
    now = time.time()
    rows = [r for r in rows if isinstance(r, dict) and now - float(r.get("ts") or 0) < _TTL_SEC]
    _cache = rows
    return list(rows)


def _save(rows: list[dict[str, Any]]) -> None:
    global _cache
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "updated": time.time(), "alerts": rows[:_MAX]}
        _PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _cache = list(rows[:_MAX])
    except Exception as exc:
        logger.warning("pending save failed: %s", exc)


def list_pending(*, limit: int = 20) -> list[dict[str, Any]]:
    rows = _load()
    rows.sort(key=lambda r: -float(r.get("ts") or 0))
    return rows[:limit]


def add_pending(
    *,
    feed: str,
    token: dict[str, Any],
    plan: dict[str, Any] | None = None,
    label: str = "",
) -> dict[str, Any]:
    """Stash an alerted pick for optional Take on Money desk."""
    mint = str(token.get("tokenAddress") or token.get("mint") or "").strip()
    if not mint:
        return {}
    feed_l = (feed or "moon").lower().strip()
    now = time.time()
    rows = [r for r in _load() if not (r.get("mint") == mint and r.get("feed") == feed_l)]
    entry = 0.0
    try:
        entry = float(
            (plan or {}).get("entry_mcap")
            or token.get("mcap_usd")
            or token.get("mcap")
            or 0
        )
    except (TypeError, ValueError):
        entry = 0.0
    sizing = (plan or {}).get("sizing") or {}
    row = {
        "id": f"{feed_l}_{mint[:12]}_{int(now)}",
        "ts": now,
        "feed": feed_l,
        "label": (label or token.get("moon_label") or token.get("snipe_label") or "").upper(),
        "mint": mint,
        "symbol": token.get("symbol") or "?",
        "name": token.get("name") or "",
        "entry_mcap": entry,
        "size_usd": sizing.get("size_usd") or (plan or {}).get("size_usd"),
        "risk_usd": sizing.get("risk_usd") or (plan or {}).get("risk_usd"),
        "plan": plan or {},
        "status": "pending",
    }
    rows.insert(0, row)
    _save(rows)
    return row


def get_pending(pending_id: str) -> dict[str, Any] | None:
    for r in _load():
        if r.get("id") == pending_id:
            return dict(r)
    return None


def remove_pending(pending_id: str | None = None, *, mint: str | None = None) -> bool:
    rows = _load()
    n = len(rows)
    if pending_id:
        rows = [r for r in rows if r.get("id") != pending_id]
    elif mint:
        m = mint.strip()
        rows = [r for r in rows if r.get("mint") != m]
    else:
        return False
    if len(rows) == n:
        return False
    _save(rows)
    return True


def clear_cache() -> None:
    global _cache
    _cache = None
