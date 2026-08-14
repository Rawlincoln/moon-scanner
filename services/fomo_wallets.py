"""Managed FOMO watchlist — add/remove wallets in the app UI.

Persists to DATA_DIR/fomo_wallets.json so the FOMO poller keeps alerting
on every wallet you add until you remove it.

On free Render the disk is ephemeral. Durability comes from:
  - ``user_touched`` flag (prevents re-seeding after you customize)
  - GHA cache sync (scripts/gha_fomo_wallets_sync.py every ~5 min)
  - Optional browser localStorage restore in the FOMO UI

On first empty load (never customized), seeds from elite S-tier roster.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR

logger = logging.getLogger("moon-scanner.fomo-wallets")

_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_PATH = Path(DATA_DIR) / "fomo_wallets.json"

_SKIP = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
}

_cache: list[dict[str, Any]] | None = None
_meta: dict[str, Any] = {
    "user_touched": False,
    "updated": 0.0,
    "version": 1,
}


def valid_address(addr: str) -> bool:
    a = (addr or "").strip()
    return bool(a and _SOL_RE.match(a) and a not in _SKIP)


def _normalize(row: dict[str, Any]) -> dict[str, Any] | None:
    addr = str(row.get("address") or "").strip()
    if not valid_address(addr):
        return None
    label = str(row.get("label") or f"{addr[:4]}…{addr[-4:]}").strip()[:40]
    tier = str(row.get("tier") or "S").upper()
    if tier not in ("S", "A", "B"):
        tier = "S"
    return {
        "id": str(row.get("id") or addr[:12]),
        "address": addr,
        "label": label or "Wallet",
        "tier": tier,
        "note": str(row.get("note") or "")[:160],
        "added_at": float(row.get("added_at") or time.time()),
        "source": str(row.get("source") or "manual"),
    }


def _seed_from_elite() -> list[dict[str, Any]]:
    try:
        from services.elite_traders import get_elite_roster

        roster = get_elite_roster(limit=40)
    except Exception:
        roster = []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in roster:
        tier = str(t.get("tier") or "").upper()
        if tier not in ("S", "A"):
            continue
        row = _normalize(
            {
                "address": t.get("address"),
                "label": t.get("label"),
                "tier": tier,
                "note": t.get("note") or "Seeded from elite desk",
                "source": "elite_seed",
                "id": t.get("id") or t.get("address"),
            }
        )
        if not row or row["address"] in seen:
            continue
        seen.add(row["address"])
        out.append(row)
    return out


def _dedupe(wallets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for w in wallets:
        a = w["address"]
        if a in seen:
            continue
        seen.add(a)
        uniq.append(w)
    return uniq


def _save(
    wallets: list[dict[str, Any]],
    *,
    user_touched: bool | None = None,
    updated: float | None = None,
) -> None:
    """Write wallet list + durability metadata to disk."""
    global _cache, _meta
    if user_touched is not None:
        _meta["user_touched"] = bool(user_touched)
    if updated is not None:
        _meta["updated"] = float(updated)
    else:
        _meta["updated"] = time.time()
    _meta["version"] = 1
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated": _meta["updated"],
            "user_touched": bool(_meta.get("user_touched")),
            "wallets": wallets,
        }
        _PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _cache = list(wallets)
    except Exception as exc:
        logger.warning("fomo wallets save failed: %s", exc)


def meta() -> dict[str, Any]:
    """Durability metadata (user_touched / updated). Loads file if needed."""
    list_wallets()
    return {
        "user_touched": bool(_meta.get("user_touched")),
        "updated": float(_meta.get("updated") or 0),
        "path": str(_PATH),
        "count": len(_cache or []),
    }


def export_payload() -> dict[str, Any]:
    """Full snapshot for GHA / browser backup."""
    wallets = list_wallets()
    return {
        "ok": True,
        "version": 1,
        "updated": float(_meta.get("updated") or 0),
        "user_touched": bool(_meta.get("user_touched")),
        "count": len(wallets),
        "wallets": wallets,
        "path": str(_PATH),
    }


def list_wallets(*, force: bool = False) -> list[dict[str, Any]]:
    """Return managed FOMO wallets (create seed file if empty & not customized)."""
    global _cache, _meta
    if _cache is not None and not force:
        return list(_cache)

    wallets: list[dict[str, Any]] = []
    user_touched = False
    updated = 0.0
    try:
        if _PATH.exists():
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            user_touched = bool(raw.get("user_touched"))
            updated = float(raw.get("updated") or 0)
            for row in raw.get("wallets") or []:
                if not isinstance(row, dict):
                    continue
                n = _normalize(row)
                if n:
                    wallets.append(n)
    except Exception as exc:
        logger.warning("fomo wallets load failed: %s", exc)

    # Migration: old files without user_touched but with manual adds count as customized
    if not user_touched and wallets:
        if any(str(w.get("source") or "") == "manual" for w in wallets):
            user_touched = True
            updated = updated or time.time()

    _meta["user_touched"] = user_touched
    _meta["updated"] = updated

    # Only auto-seed when the operator has NEVER customized the list.
    # If user_touched, empty list is intentional (or waiting for GHA restore).
    if not wallets and not user_touched:
        wallets = _seed_from_elite()
        if wallets:
            _save(wallets, user_touched=False)
            logger.info("FOMO wallets seeded %s from elite desk", len(wallets))
            return list(_cache or wallets)

    wallets = _dedupe(wallets)
    _cache = wallets
    # Persist migrated user_touched flag so restarts keep it
    if user_touched and _PATH.exists():
        try:
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
            if not raw.get("user_touched"):
                _save(wallets, user_touched=True, updated=updated or time.time())
        except Exception:
            pass
    return list(wallets)


def add_wallet(
    address: str,
    *,
    label: str | None = None,
    tier: str = "S",
    note: str = "",
) -> dict[str, Any]:
    """Add or update a FOMO wallet. Returns the row."""
    addr = (address or "").strip()
    if not valid_address(addr):
        raise ValueError("Invalid Solana wallet address")
    wallets = list_wallets(force=True)
    label_s = (label or "").strip() or f"{addr[:4]}…{addr[-4:]}"
    row = _normalize(
        {
            "address": addr,
            "label": label_s,
            "tier": tier or "S",
            "note": note or "Added from FOMO app",
            "source": "manual",
            "added_at": time.time(),
            "id": f"fomo_{addr[:10]}",
        }
    )
    assert row is not None
    out = [w for w in wallets if w["address"] != addr]
    out.insert(0, row)
    _save(out, user_touched=True)
    return row


def remove_wallet(address: str) -> bool:
    """Remove wallet by address. Returns True if removed."""
    addr = (address or "").strip()
    if not addr:
        return False
    wallets = list_wallets(force=True)
    new = [w for w in wallets if w["address"] != addr]
    if len(new) == len(wallets):
        return False
    _save(new, user_touched=True)
    return True


def replace_wallets(
    rows: list[dict[str, Any]],
    *,
    user_touched: bool = True,
    updated: float | None = None,
) -> list[dict[str, Any]]:
    """Replace entire watchlist (GHA restore / browser restore)."""
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        n = _normalize(row)
        if n:
            out.append(n)
    out = _dedupe(out)
    _save(
        out,
        user_touched=user_touched,
        updated=updated if updated is not None else time.time(),
    )
    return list(_cache or out)


def clear_cache() -> None:
    global _cache, _meta
    _cache = None
    _meta = {"user_touched": False, "updated": 0.0, "version": 1}
