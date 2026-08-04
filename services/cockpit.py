"""Germanus-inspired cockpit: measurable on-chain facts, not buy/sell verdicts.

Philosophy (from germanus.app/doc):
  - Facts with evidence, n/a when unknown — never invent
  - Snapshot archive enables change-over-time
  - No traffic-light "safe" scores as the product
"""

from __future__ import annotations

from typing import Any


def _f(x: Any, d: float | None = None) -> float | None:
    if x is None:
        return d
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _i(x: Any, d: int | None = None) -> int | None:
    if x is None:
        return d
    try:
        return int(x)
    except (TypeError, ValueError):
        return d


def _authority_state(val: Any, *, field_seen: bool = True) -> str:
    """revoked | present | n/a — Germanus-style labels.

    RugCheck / chain: null authority = revoked. Missing field entirely = n/a.
    """
    if not field_seen:
        return "n/a"
    if val is None or val is False or val == 0 or val == "":
        return "revoked"
    if val is True:
        return "present"
    s = str(val).strip()
    if s.lower() in ("false", "0", "revoked", "none", "null"):
        return "revoked"
    if len(s) >= 32:
        return "present"
    return "n/a"


def _lp_status(safety: dict[str, Any], market: dict[str, Any]) -> str:
    """program_custodied | locked | unlocked | unknown | n/a"""
    lp_locked = safety.get("lp_locked")
    lock_pct = _f(safety.get("lp_locked_pct") or safety.get("lp_lock_pct"))
    if lp_locked is True or (lock_pct is not None and lock_pct >= 95):
        return "locked"
    if safety.get("lp_burned") or safety.get("lp_burn"):
        return "burned"
    # pump.fun bonding / graduated often program-held LP
    if safety.get("on_bonding_curve") is True:
        return "program_custodied"
    if market.get("is_pumpfun_synthetic"):
        return "program_custodied"
    liq = _f((market.get("liquidity") or {}).get("usd") if isinstance(market.get("liquidity"), dict) else market.get("liquidity_usd") or market.get("liquidity"))
    if liq is not None and liq <= 0:
        return "unknown"
    if lp_locked is False:
        return "unlocked"
    return "unknown"


def extract_cockpit(result: dict[str, Any]) -> dict[str, Any]:
    """Build a Germanus-style fact grid from an analyze_token result."""
    safety = result.get("safety") or {}
    market = result.get("market") or {}
    pf = market.get("pumpfun") or result.get("pumpfun") or {}
    bs = result.get("bundleSniper") or {}
    if not isinstance(bs, dict):
        bs = {}

    # Liquidity
    liq = None
    raw_liq = market.get("liquidity")
    if isinstance(raw_liq, dict):
        liq = _f(raw_liq.get("usd"))
    if liq is None:
        liq = _f(market.get("liquidity_usd") or market.get("liquidity") or safety.get("liquidity"))

    # Pools / pairs
    pools = _i(market.get("pair_count") or market.get("pools") or safety.get("pools"))
    if pools is None and market.get("url"):
        pools = 1  # at least one pair if dex URL exists
    if pools is None:
        pools = None  # n/a

    # Holders
    holders = _i(
        safety.get("total_holders")
        or safety.get("holder_count")
        or safety.get("holders")
        or pf.get("holder_count")
    )
    top = safety.get("top_holders") or []
    top1 = None
    top5 = None
    top10 = None
    wallets_gt_1pct = None
    if isinstance(top, list) and top:
        pcts: list[float] = []
        for h in top:
            if not isinstance(h, dict):
                continue
            # skip known pool labels if flagged
            if h.get("is_pool") or h.get("pool") or h.get("insider"):
                # still count unless clearly pool
                pass
            p = _f(h.get("pct") or h.get("percentage") or h.get("share"))
            if p is not None:
                pcts.append(p)
        if pcts:
            top1 = round(pcts[0], 2)
            top5 = round(sum(pcts[:5]), 2)
            top10 = round(sum(pcts[:10]), 2)
            wallets_gt_1pct = sum(1 for p in pcts if p >= 1.0)

    mint_seen = "mint_authority" in safety or "mintAuthority" in safety
    freeze_seen = "freeze_authority" in safety or "freezeAuthority" in safety
    mint_auth = safety.get("mint_authority", safety.get("mintAuthority"))
    freeze_auth = safety.get("freeze_authority", safety.get("freezeAuthority"))

    # RugCheck often uses mintAuthority: null when revoked
    mint_s = _authority_state(mint_auth, field_seen=mint_seen)
    freeze_s = _authority_state(freeze_auth, field_seen=freeze_seen)
    # Also check risks list
    risks = safety.get("risks") or safety.get("issues") or []
    risk_text = " ".join(str(r) for r in risks).lower() if isinstance(risks, list) else str(risks).lower()
    if "mint authority" in risk_text and "still" in risk_text:
        mint_s = "present"
    if "freeze" in risk_text and "still" in risk_text:
        freeze_s = "present"

    mcap = _f(
        result.get("mcap_usd")
        or market.get("marketCap")
        or market.get("mcap")
        or pf.get("usd_market_cap")
    )
    vol24 = None
    vol = market.get("volume")
    if isinstance(vol, dict):
        vol24 = _f(vol.get("h24") or vol.get("h24_usd"))
    if vol24 is None:
        vol24 = _f(market.get("volume_h24") or market.get("volume24h"))

    bun = None
    if isinstance(bs.get("bundle"), dict):
        bun = _f(bs["bundle"].get("bundled_pct"))
    if bun is None:
        bun = _f(bs.get("bundled_pct"))

    sn_lv = None
    if isinstance(bs.get("snipers"), dict):
        sn_lv = bs["snipers"].get("risk_level")

    # Coverage: how many core cells resolved
    cells = {
        "mint_authority": mint_s != "n/a",
        "freeze_authority": freeze_s != "n/a",
        "liquidity": liq is not None,
        "holders": holders is not None,
        "top1": top1 is not None,
        "pools": pools is not None,
        "mcap": mcap is not None,
        "lp_status": True,
    }
    covered = sum(1 for v in cells.values() if v)
    coverage_pct = round(100 * covered / max(len(cells), 1), 1)

    unresolved = covered < 5 or (mint_s == "n/a" and freeze_s == "n/a" and top1 is None)

    return {
        "philosophy": "facts_with_evidence_no_verdict",
        "symbol": result.get("symbol") or pf.get("symbol") or market.get("symbol") or "?",
        "name": result.get("name") or pf.get("name") or market.get("name") or "",
        "mint": result.get("tokenAddress") or result.get("mint") or "",
        "chain": result.get("chainId") or "solana",
        # Control surface
        "mint_authority": mint_s,
        "freeze_authority": freeze_s,
        "lp_status": _lp_status(safety, market),
        # Market
        "liquidity_usd": round(liq, 2) if liq is not None else None,
        "mcap_usd": round(mcap, 2) if mcap is not None else None,
        "volume_24h_usd": round(vol24, 2) if vol24 is not None else None,
        "pools": pools,
        # Distribution
        "holders": holders,
        "top1_pct": top1,
        "top5_pct": top5,
        "top10_pct": top10,
        "wallets_gt_1pct": wallets_gt_1pct,
        "holders_known": bool(top),
        # Book (our extra, still facts)
        "bundled_pct": round(bun, 2) if bun is not None else None,
        "sniper_risk": sn_lv or "n/a",
        # Meta
        "coverage_pct": coverage_pct,
        "cells_resolved": covered,
        "cells_total": len(cells),
        "unresolved": unresolved,
        "n_a_policy": "missing shown as null/n/a — never zero-filled",
        "analyzed_at": result.get("analyzedAt") or result.get("analyzed_at"),
        "sources": ["rugcheck", "dexscreener", "pumpfun", "bundle_sniper"],
    }


def cockpit_delta(prev: dict[str, Any] | None, cur: dict[str, Any]) -> dict[str, Any]:
    """Change vs previous snapshot (Germanus phase 9)."""
    if not prev:
        return {"has_prev": False, "note": "first scan — nothing to compare"}
    keys = (
        "liquidity_usd",
        "mcap_usd",
        "holders",
        "top1_pct",
        "top10_pct",
        "volume_24h_usd",
        "bundled_pct",
        "pools",
    )
    changes: dict[str, Any] = {}
    for k in keys:
        a, b = prev.get(k), cur.get(k)
        if a is None and b is None:
            continue
        if a is None or b is None:
            changes[k] = {"from": a, "to": b, "pct": None}
            continue
        try:
            fa, fb = float(a), float(b)
            pct = ((fb - fa) / fa * 100) if fa != 0 else None
            changes[k] = {
                "from": fa,
                "to": fb,
                "pct": round(pct, 2) if pct is not None else None,
            }
        except (TypeError, ValueError):
            changes[k] = {"from": a, "to": b, "pct": None}
    # Authority flips are critical facts
    for k in ("mint_authority", "freeze_authority", "lp_status"):
        if prev.get(k) != cur.get(k):
            changes[k] = {"from": prev.get(k), "to": cur.get(k)}
    return {"has_prev": True, "changes": changes}


def liquidity_drift_pct(prev_liq: float | None, cur_liq: float | None) -> float | None:
    """Freshness probe — Germanus uses 10% liquidity drift threshold."""
    if prev_liq is None or cur_liq is None:
        return None
    if prev_liq <= 0:
        return 100.0 if cur_liq > 0 else 0.0
    return abs(cur_liq - prev_liq) / prev_liq * 100.0
