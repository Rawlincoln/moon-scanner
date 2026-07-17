"""Detect major-trader / whale buys as an anti-rug confidence signal.

Uses:
  1. Curated known smart-money / KOL wallets (expandable)
  2. RugCheck top-holder heuristics (healthy whale bags, not snipers)
  3. DexScreener paid orders (boost / community takeover = capital spent)
"""

from __future__ import annotations

from typing import Any

import httpx

from config import REQUEST_TIMEOUT, USER_AGENT

# Public tracked wallets — labels for display only (not financial advice).
# Expand this map with wallets you follow on Solscan / GMGN / Twitter.
KNOWN_MAJOR_TRADERS: dict[str, dict[str, str]] = {
    # Well-known Solana ecosystem / trader wallets (publicly discussed)
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": {
        "label": "Raydium Authority",
        "tier": "protocol",
    },
    # Placeholders — replace/add your tracked KOLs:
    # "WalletBase58...": {"label": "KOL Name", "tier": "major"},
}

# Skip system / LP-like owners when ranking whales
_SKIP_LABELS = {"protocol", "lp", "pool", "bonding", "curve"}

# Healthy whale band (% of supply) — large enough to matter, not sniper dump size
WHALE_MIN_PCT = 0.8
WHALE_MAX_PCT = 12.0
# Bonding curve / pool usually shows as huge bag
POOL_PCT_MIN = 40.0
# Minimum estimated USD bag to flag whale interest
WHALE_MIN_USD = 400.0


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _owner_key(h: dict) -> str:
    return str(h.get("owner") or h.get("address") or "").strip()


def analyze_smart_money(
    safety: dict,
    pair: dict | None = None,
    mcap_usd: float = 0.0,
    dex_orders: list | None = None,
) -> dict[str, Any]:
    """Return major-trader / whale signal for a token."""
    pair = pair or {}
    pump = pair.get("pumpfun") or {}
    top_holders = safety.get("top_holders") or []
    creator = str(safety.get("creator") or pump.get("creator") or "")
    mcap = mcap_usd or _safe_float(
        pump.get("usd_market_cap")
        or (pair.get("marketCap") if isinstance(pair.get("marketCap"), (int, float)) else 0)
        or (pair.get("fdv") if isinstance(pair.get("fdv"), (int, float)) else 0)
    )

    known_hits: list[dict[str, Any]] = []
    whale_hits: list[dict[str, Any]] = []

    for h in top_holders:
        owner = _owner_key(h)
        if not owner:
            continue
        pct = _safe_float(h.get("pct"))
        if pct >= POOL_PCT_MIN:
            continue  # bonding curve / LP
        if safety.get("insider_detected") and h.get("insider"):
            continue
        if creator and creator in owner:
            continue

        known = KNOWN_MAJOR_TRADERS.get(owner)
        if known and known.get("tier") not in _SKIP_LABELS:
            est = round(mcap * pct / 100.0) if mcap else 0
            known_hits.append(
                {
                    "owner": owner,
                    "label": known.get("label", "Known trader"),
                    "tier": known.get("tier", "major"),
                    "pct": round(pct, 2),
                    "est_usd": est,
                    "source": "known_wallet",
                }
            )
            continue

        # Heuristic whale: sizeable bag, not insider, not dump-range sniper
        if WHALE_MIN_PCT <= pct <= WHALE_MAX_PCT and not h.get("insider"):
            est = round(mcap * pct / 100.0) if mcap else 0
            if est >= WHALE_MIN_USD or pct >= 1.5:
                whale_hits.append(
                    {
                        "owner": owner,
                        "label": "Whale holder",
                        "tier": "whale",
                        "pct": round(pct, 2),
                        "est_usd": est,
                        "source": "holder_heuristic",
                    }
                )

    # Mid-size distribution (multiple independent bags) = organic interest
    mid_bags = [
        h
        for h in top_holders
        if 0.35 <= _safe_float(h.get("pct")) < WHALE_MIN_PCT
        and not h.get("insider")
        and _safe_float(h.get("pct")) < POOL_PCT_MIN
        and not (creator and creator in _owner_key(h))
    ]

    paid_interest: list[dict[str, Any]] = []
    for order in dex_orders or []:
        status = str(order.get("status") or "").lower()
        if status not in ("approved", "processing", "live", "active"):
            continue
        otype = str(order.get("type") or "boost")
        paid_interest.append(
            {
                "type": otype,
                "status": status,
                "label": f"DexScreener {otype}",
            }
        )

    known_hits.sort(key=lambda x: -x.get("pct", 0))
    whale_hits.sort(key=lambda x: -x.get("pct", 0))
    whale_hits = whale_hits[:6]
    known_hits = known_hits[:6]

    has_known = len(known_hits) > 0
    has_whale = len(whale_hits) > 0
    has_paid = len(paid_interest) > 0
    multi_mid = len(mid_bags) >= 3

    # Anti-rug confidence: known trader or healthy whale without insider snipers
    insider_poison = bool(
        safety.get("insider_detected")
        or safety.get("rugged")
        or safety.get("is_honeypot")
    )
    anti_rug = False
    signal = "NONE"
    summary = "No major trader or whale buy detected"

    if has_known and not insider_poison:
        signal = "MAJOR_TRADER"
        anti_rug = True
        labels = ", ".join(k["label"] for k in known_hits[:3])
        summary = f"Major trader in: {labels} — stronger anti-rug signal"
    elif has_whale and not insider_poison:
        signal = "WHALE_BUY"
        anti_rug = True
        top = whale_hits[0]
        summary = (
            f"Whale bag {top['pct']}% (~${top['est_usd']:,}) — "
            f"{len(whale_hits)} large non-insider holder(s)"
        )
    elif multi_mid and not insider_poison:
        signal = "DISTRIBUTED_WHALES"
        anti_rug = True
        summary = (
            f"{len(mid_bags)} mid-size holders — distributed interest "
            "(less single-wallet dump risk)"
        )
    elif has_paid and not insider_poison:
        signal = "PAID_INTEREST"
        anti_rug = False  # marketing ≠ safety, but capital was spent
        summary = f"DexScreener paid promotion: {paid_interest[0]['label']}"
    elif insider_poison and (has_known or has_whale):
        signal = "TAINTED"
        summary = "Large bags present but insider/honeypot risk — not trusted"

    confidence = 0
    if signal == "MAJOR_TRADER":
        confidence = 85
    elif signal == "WHALE_BUY":
        confidence = 70
    elif signal == "DISTRIBUTED_WHALES":
        confidence = 55
    elif signal == "PAID_INTEREST":
        confidence = 40
    elif signal == "TAINTED":
        confidence = 15

    return {
        "detected": anti_rug,
        "highlight": anti_rug or signal == "PAID_INTEREST",
        "signal": signal,
        "summary": summary,
        "confidence": confidence,
        "anti_rug_signal": anti_rug,
        "known_traders": known_hits,
        "whale_holders": whale_hits,
        "paid_interest": paid_interest[:4],
        "mid_holder_count": len(mid_bags),
        "mcap_used": round(mcap) if mcap else 0,
    }


async def fetch_dex_orders(chain_id: str, token_address: str) -> list[dict]:
    """DexScreener paid orders (boosts / community takeover)."""
    if chain_id != "solana" or not token_address:
        return []
    url = f"https://api.dexscreener.com/orders/v1/{chain_id}/{token_address}"
    try:
        async with httpx.AsyncClient(
            timeout=min(REQUEST_TIMEOUT, 8.0),
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("orders") or []
    except Exception:
        return []


async def analyze_smart_money_async(
    safety: dict,
    pair: dict | None,
    chain_id: str,
    token_address: str,
    mcap_usd: float = 0.0,
) -> dict[str, Any]:
    orders = await fetch_dex_orders(chain_id, token_address)
    return analyze_smart_money(
        safety, pair=pair, mcap_usd=mcap_usd, dex_orders=orders
    )
