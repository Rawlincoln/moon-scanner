"""Elite trader registry — 20 smart-money wallets + learned winners.

Buy signal = elite wallet appears as holder/buyer on a token that still
passes our full safety stack (hard avoid, flash, wash, mint/freeze, etc.).

Seeds live in data/elite_traders.json (user-editable). Learning promotes
wallets that repeatedly show up on HEAT/MOON quality tokens / win outcomes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR

logger = logging.getLogger("moon-scanner.elite")

_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_POOL_PCT = 40.0
_SEED_PATH = Path(DATA_DIR) / "elite_traders.json"
_SEED_REPO = Path(__file__).resolve().parent / "elite_traders_seed.json"
_LEARNED_PATH = Path(DATA_DIR) / "elite_learned.json"

# 20 elite desk slots (replace addresses with real GMGN/Kolscan wallets)
DEFAULT_ELITE_SEEDS: list[dict[str, str]] = [
    {"id": "elite_01", "address": "H47kiuPLUEXGsw8JFPi2BtubU4ovSwLbcxu5tHR4byzX", "label": "Alpha Desk", "tier": "S", "style": "early_curve", "note": "Replace with your #1 tracked KOL"},
    {"id": "elite_02", "address": "9Vv95EqzTmrDmGGPTPgruUR7hpH65QMiSQSo9JqNz7HH", "label": "Moon Sniper", "tier": "S", "style": "sniper_survivor", "note": "Post-sniper second leg"},
    {"id": "elite_03", "address": "Gyyra5osiYuy2GSGso4RBwdENYkr8bDx8KNop8mJV2YU", "label": "Curve King", "tier": "S", "style": "near_migration", "note": "High bond specialists"},
    {"id": "elite_04", "address": "2tTKBMmd5goAuN3tNKoMy5AjvDw19dmacTR46wuW7GH2", "label": "Narrative Wolf", "tier": "A", "style": "narrative", "note": "CT + community"},
    {"id": "elite_05", "address": "GW1aozFm4EwhLUVbx5AZoaCtaPS7p7EYzRFXZkeyb2NV", "label": "Clean Book", "tier": "A", "style": "anti_bundle", "note": "Avoids bundles"},
    {"id": "elite_06", "address": "5ZaTiqSSSgrKxBTvMA4f8vE5TGiTG4aLCJEkPS5AyRV9", "label": "Size Patient", "tier": "A", "style": "conviction", "note": "Fewer larger bags"},
    {"id": "elite_07", "address": "4zB4VvT29gDAPjh2XZXGM7EhSFCHcqQLcxRDf8eeaysb", "label": "2x Scalper", "tier": "A", "style": "scalp", "note": "Fast 1.5–2×"},
    {"id": "elite_08", "address": "BWijX8PZETi4fdPw1miVh4mr2So6U4JVxgy8Dr3xEC74", "label": "Organic Heat", "tier": "A", "style": "organic", "note": "Heat band entries"},
    {"id": "elite_09", "address": "6CzNsxR5meDh1xCPC3UesXyYvdxebp187kpkAQjJB1An", "label": "Dev Filter", "tier": "A", "style": "safety", "note": "Skips serial farms"},
    {"id": "elite_10", "address": "9CqHqkto6TNLrejwemjtB1bV7hoUdJfJQoEFi6TNebdF", "label": "Bond Climber", "tier": "A", "style": "climb", "note": "Mid-curve"},
    {"id": "elite_11", "address": "7zeoDcvyAensT2eKvW67hgP8vixNsdV2MjsjLiGcE75J", "label": "Whale Follow", "tier": "B", "style": "follow", "note": "Stacks with smart bags"},
    {"id": "elite_12", "address": "4Y7GFWkZhJ1d65hVbBq4XtZssV8B1RmTSaMWXETCiMda", "label": "Reply Heat", "tier": "B", "style": "community", "note": "High replies"},
    {"id": "elite_13", "address": "8haZGHAn7YTTHgoKb7dsnTGWZkzhnd4cxLD7tS2fyqtB", "label": "ATH Guard", "tier": "B", "style": "momentum", "note": "Near ATH"},
    {"id": "elite_14", "address": "CR5foJN7y6avSaAP54GdZ6hVjPBUCEWFu89573YgenaK", "label": "Fee Organic", "tier": "B", "style": "fee_flow", "note": "No flash fees"},
    {"id": "elite_15", "address": "C1KsgbEohzsVj9okbt3ER9FvhsuUee9PQJftXJnnqdBp", "label": "First Dev", "tier": "B", "style": "fresh_dev", "note": "First-token devs"},
    {"id": "elite_16", "address": "Cy4ZZpMdXSCyVMeom9GXekTDhWAW2nFdasAxgaQ6nFBv", "label": "Grad Path", "tier": "B", "style": "migration", "note": "Graduation path"},
    {"id": "elite_17", "address": "94y8UmxDgeq9X1KJBrVn7yE1fKNqH4huPTUdKHRyGXYL", "label": "Anti Trap", "tier": "B", "style": "anti_spoof", "note": "No status-link socials"},
    {"id": "elite_18", "address": "EXyeLh3JvzuZNZ15PYH8E3wfNsz1Y1eKE1QsgFAEf2UY", "label": "Volume Real", "tier": "B", "style": "two_way", "note": "Two-way flow"},
    {"id": "elite_19", "address": "4qfZJEcNnbRG5mV2hGYET67r4tdwJn2ucdWjm9ka9oTd", "label": "Unique Ticker", "tier": "B", "style": "ticker", "note": "Avoids copycats"},
    {"id": "elite_20", "address": "49FbqWyanE8sid1sbtoqWL8tYeGrJLseBvVjWUH4izrT", "label": "Desk Reserve", "tier": "B", "style": "flex", "note": "20th tracked wallet"},
]

# Never treat as traders
_SKIP_ADDRS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "ComputeBudget111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "SysvarRent111111111111111111111111111111111",
    "SysvarC1ock11111111111111111111111111111111",
}

_learned_cache: dict[str, Any] | None = None
_seed_cache: list[dict[str, Any]] | None = None


def _valid_addr(a: str) -> bool:
    a = (a or "").strip()
    return bool(a and _SOL_RE.match(a) and a not in _SKIP_ADDRS)


def load_seed_traders(*, force: bool = False) -> list[dict[str, Any]]:
    """Load the 20 elite slots from JSON (or empty on missing file)."""
    global _seed_cache
    if _seed_cache is not None and not force:
        return list(_seed_cache)
    import os

    path = _SEED_PATH
    env_path = (os.getenv("ELITE_TRADERS_JSON") or "").strip()
    if env_path:
        path = Path(env_path)

    traders: list[dict[str, Any]] = []
    raw_list: list[dict] = []
    try:
        if path.exists():
            raw_list = list((json.loads(path.read_text(encoding="utf-8")) or {}).get("traders") or [])
        elif _SEED_REPO.exists():
            raw_list = list(
                (json.loads(_SEED_REPO.read_text(encoding="utf-8")) or {}).get("traders")
                or []
            )
        else:
            raw_list = list(DEFAULT_ELITE_SEEDS)
            # Persist editable copy under DATA_DIR for operators
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "version": 2,
                            "note": "Edit addresses to real GMGN/Kolscan wallets",
                            "traders": DEFAULT_ELITE_SEEDS,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
    except Exception as exc:
        logger.warning("elite seed load failed: %s — using defaults", exc)
        raw_list = list(DEFAULT_ELITE_SEEDS)

    for t in raw_list:
        if not isinstance(t, dict):
            continue
        addr = str(t.get("address") or "").strip()
        tier = str(t.get("tier") or "B").upper()
        if tier == "SKIP" or not _valid_addr(addr):
            continue
        traders.append(
            {
                "id": t.get("id") or addr[:8],
                "address": addr,
                "label": str(t.get("label") or "Elite")[:40],
                "tier": tier if tier in ("S", "A", "B") else "B",
                "style": str(t.get("style") or "flex")[:32],
                "note": str(t.get("note") or "")[:160],
                "source": "seed",
            }
        )
    if not traders:
        traders = [
            {
                "id": t["id"],
                "address": t["address"],
                "label": t["label"],
                "tier": t.get("tier") or "B",
                "style": t.get("style") or "flex",
                "note": t.get("note") or "",
                "source": "seed",
            }
            for t in DEFAULT_ELITE_SEEDS
            if _valid_addr(t.get("address") or "")
        ]
    _seed_cache = traders
    return list(traders)


def _load_learned() -> dict[str, Any]:
    global _learned_cache
    if _learned_cache is not None:
        return _learned_cache
    try:
        if _LEARNED_PATH.exists():
            _learned_cache = json.loads(_LEARNED_PATH.read_text(encoding="utf-8"))
        else:
            _learned_cache = {"wallets": {}, "updated": 0}
    except Exception:
        _learned_cache = {"wallets": {}, "updated": 0}
    return _learned_cache


def _save_learned(data: dict[str, Any]) -> None:
    global _learned_cache
    try:
        _LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
        data["updated"] = time.time()
        _LEARNED_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _learned_cache = data
    except Exception as exc:
        logger.debug("elite learned save failed: %s", exc)


def credit_wallet(
    address: str,
    *,
    points: float = 1.0,
    mint: str | None = None,
    reason: str = "quality_token",
    label_hint: str | None = None,
) -> None:
    """Boost a wallet score when seen on quality / winning tokens."""
    addr = (address or "").strip()
    if not _valid_addr(addr):
        return
    data = _load_learned()
    wallets = data.setdefault("wallets", {})
    row = wallets.get(addr) or {
        "address": addr,
        "score": 0.0,
        "hits": 0,
        "wins": 0,
        "losses": 0,
        "mints": [],
        "label": label_hint or f"Learned {addr[:4]}…{addr[-4:]}",
        "tier": "B",
        "source": "learned",
        "last_seen": 0,
    }
    row["score"] = float(row.get("score") or 0) + float(points)
    row["hits"] = int(row.get("hits") or 0) + 1
    row["last_seen"] = time.time()
    if label_hint and not str(row.get("label") or "").startswith("Learned"):
        pass
    elif label_hint:
        row["label"] = label_hint
    mints = list(row.get("mints") or [])
    if mint and mint not in mints:
        mints = ([mint] + mints)[:24]
        row["mints"] = mints
    if reason == "win":
        row["wins"] = int(row.get("wins") or 0) + 1
        row["tier"] = "A" if row["wins"] >= 2 else row.get("tier") or "B"
    if reason == "loss":
        row["losses"] = int(row.get("losses") or 0) + 1
        row["score"] = max(0.0, float(row["score"]) - abs(points))
    # Promote tier by score
    sc = float(row["score"])
    if sc >= 25 or int(row.get("wins") or 0) >= 3:
        row["tier"] = "S"
    elif sc >= 12 or int(row.get("wins") or 0) >= 1:
        row["tier"] = "A"
    wallets[addr] = row
    _save_learned(data)


def credit_holders_from_token(
    token: dict[str, Any],
    *,
    points: float = 2.0,
    reason: str = "quality_token",
) -> int:
    """Credit non-pool top holders on a quality pick (HEAT/MOON/SNIPE)."""
    safety = token.get("safety") or {}
    top = safety.get("top_holders") or []
    creator = str(
        safety.get("creator")
        or (token.get("pumpfun") or {}).get("creator")
        or ""
    )
    mint = str(token.get("tokenAddress") or token.get("mint") or "")
    n = 0
    for h in top:
        if not isinstance(h, dict):
            continue
        owner = str(h.get("owner") or h.get("address") or "").strip()
        pct = float(h.get("pct") or 0)
        if not owner or owner == creator or pct >= _POOL_PCT:
            continue
        if h.get("insider"):
            continue
        if pct < 0.25 or pct > 15.0:
            continue
        credit_wallet(owner, points=points, mint=mint, reason=reason)
        n += 1
    return n


def get_elite_roster(*, limit: int = 20) -> list[dict[str, Any]]:
    """Return up to 20 active elites: seeds first, then top learned fill."""
    seeds = load_seed_traders()
    by_addr: dict[str, dict[str, Any]] = {}
    for t in seeds:
        by_addr[t["address"]] = dict(t)

    learned = _load_learned().get("wallets") or {}
    ranked = sorted(
        (v for v in learned.values() if isinstance(v, dict)),
        key=lambda x: (
            -float(x.get("score") or 0),
            -int(x.get("wins") or 0),
            -int(x.get("hits") or 0),
        ),
    )
    for v in ranked:
        addr = str(v.get("address") or "")
        if not _valid_addr(addr):
            continue
        if addr in by_addr:
            # Merge scores onto seed
            cur = by_addr[addr]
            cur["score"] = float(v.get("score") or 0)
            cur["hits"] = int(v.get("hits") or 0)
            cur["wins"] = int(v.get("wins") or 0)
            cur["learned"] = True
            continue
        if len(by_addr) >= limit:
            break
        by_addr[addr] = {
            "id": f"learned_{addr[:6]}",
            "address": addr,
            "label": str(v.get("label") or f"Learned {addr[:4]}…")[:40],
            "tier": str(v.get("tier") or "B"),
            "style": "learned",
            "note": f"Auto-learned · score {float(v.get('score') or 0):.0f} · "
            f"wins {int(v.get('wins') or 0)}",
            "source": "learned",
            "score": float(v.get("score") or 0),
            "hits": int(v.get("hits") or 0),
            "wins": int(v.get("wins") or 0),
        }

    # Prefer S/A tiers, then score
    out = list(by_addr.values())
    tier_r = {"S": 0, "A": 1, "B": 2}
    out.sort(
        key=lambda x: (
            tier_r.get(str(x.get("tier") or "B"), 9),
            0 if x.get("source") == "seed" else 1,
            -float(x.get("score") or 0),
        )
    )
    return out[:limit]


def elite_address_set() -> set[str]:
    return {t["address"] for t in get_elite_roster(limit=20) if t.get("address")}


def elite_lookup() -> dict[str, dict[str, Any]]:
    return {t["address"]: t for t in get_elite_roster(limit=20)}


def match_elites_on_token(token: dict[str, Any]) -> list[dict[str, Any]]:
    """Find elite wallets in top holders (and optional known smart_money hits)."""
    lookup = elite_lookup()
    if not lookup:
        return []
    safety = token.get("safety") or {}
    top = safety.get("top_holders") or []
    creator = str(
        safety.get("creator") or (token.get("pumpfun") or {}).get("creator") or ""
    )
    mcap = float(token.get("mcap_usd") or 0)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in top:
        if not isinstance(h, dict):
            continue
        owner = str(h.get("owner") or h.get("address") or "").strip()
        if not owner or owner in seen or owner == creator:
            continue
        pct = float(h.get("pct") or 0)
        if pct >= _POOL_PCT:
            continue
        info = lookup.get(owner)
        if not info:
            continue
        seen.add(owner)
        est = round(mcap * pct / 100.0) if mcap and pct else 0
        hits.append(
            {
                "address": owner,
                "label": info.get("label"),
                "tier": info.get("tier"),
                "style": info.get("style"),
                "source": info.get("source"),
                "pct": round(pct, 2),
                "est_usd": est,
                "score": info.get("score"),
                "wins": info.get("wins"),
            }
        )
    # Also check smart_money known hits if present
    sm = token.get("smartMoney") or {}
    for kh in sm.get("known_hits") or sm.get("known") or []:
        if not isinstance(kh, dict):
            continue
        owner = str(kh.get("owner") or "").strip()
        if owner in seen or owner not in lookup:
            continue
        seen.add(owner)
        info = lookup[owner]
        hits.append(
            {
                "address": owner,
                "label": info.get("label") or kh.get("label"),
                "tier": info.get("tier"),
                "style": info.get("style"),
                "source": "smart_money",
                "pct": kh.get("pct"),
                "est_usd": kh.get("est_usd"),
            }
        )
    tier_r = {"S": 0, "A": 1, "B": 2}
    hits.sort(key=lambda x: (tier_r.get(str(x.get("tier") or "B"), 9), -float(x.get("pct") or 0)))
    return hits


def roster_public() -> dict[str, Any]:
    """API shape for /api/elite/traders."""
    roster = get_elite_roster(limit=20)
    return {
        "ok": True,
        "count": len(roster),
        "traders": [
            {
                "id": t.get("id"),
                "address": t.get("address"),
                "label": t.get("label"),
                "tier": t.get("tier"),
                "style": t.get("style"),
                "note": t.get("note"),
                "source": t.get("source"),
                "score": t.get("score"),
                "hits": t.get("hits"),
                "wins": t.get("wins"),
                "solscan": f"https://solscan.io/account/{t.get('address')}",
            }
            for t in roster
        ],
        "hint": (
            "Replace seed addresses in data/elite_traders.json with real GMGN/Kolscan "
            "wallets. Learned wallets auto-fill from quality HEAT/MOON tokens."
        ),
    }
