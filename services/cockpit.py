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
    # Holders: do not use `or` chain on 0 (0 is valid and must show)
    holders = None
    for key_src in (
        (safety, "total_holders"),
        (safety, "holder_count"),
        (safety, "holders"),
        (safety, "totalHolders"),
        (pf, "holder_count"),
        (pf, "holders"),
        (result, "total_holders"),
    ):
        src, key = key_src
        if not isinstance(src, dict):
            continue
        if key in src and src.get(key) is not None:
            holders = _i(src.get(key))
            break
    top = (
        safety.get("top_holders")
        or safety.get("topHolders")
        or result.get("top_holders")
        or []
    )
    top1 = None
    top5 = None
    top10 = None
    wallets_gt_1pct = None
    if isinstance(top, list) and top:
        pcts: list[float] = []
        for h in top:
            if not isinstance(h, dict):
                continue
            # skip pure zero-amount dust rows
            p = _f(h.get("pct") or h.get("percentage") or h.get("share"))
            if p is not None:
                pcts.append(p)
        # Prefer non-pool rows for top1 when possible
        non_pool = []
        for h in top:
            if not isinstance(h, dict):
                continue
            if h.get("is_pool") or h.get("pool"):
                continue
            p = _f(h.get("pct") or h.get("percentage") or h.get("share"))
            if p is not None and p > 0:
                non_pool.append(p)
        use = non_pool if non_pool else [p for p in pcts if p is not None]
        if use:
            top1 = round(use[0], 2)
            top5 = round(sum(use[:5]), 2)
            top10 = round(sum(use[:10]), 2)
            wallets_gt_1pct = sum(1 for p in use if p >= 1.0)
        # If total_holders missing, estimate from top list length (weak)
        if holders is None and len(top) > 0:
            holders = len(top)  # incomplete — mark below
    holders_estimated = False
    if holders is None and isinstance(top, list) and top:
        holders = len(top)
        holders_estimated = True

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

    # Flash holder velocity (facts for Lab)
    age_m = result.get("age_minutes")
    if age_m is None:
        age_m = (result.get("pumpfun") or pf or {}).get("age_minutes")
    try:
        age_f = float(age_m) if age_m is not None else None
    except (TypeError, ValueError):
        age_f = None
    holders_per_min = None
    flash_holders = False
    if holders and age_f is not None and age_f > 0:
        holders_per_min = round(holders / max(age_f, 0.15), 1)
        if (age_f <= 3 and holders >= 80) or (age_f <= 5 and holders >= 120):
            flash_holders = True
        elif age_f <= 12 and holders_per_min >= 35 and holders >= 50:
            flash_holders = True

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
        "holders_estimated": holders_estimated,
        "top1_pct": top1,
        "top5_pct": top5,
        "top10_pct": top10,
        "wallets_gt_1pct": wallets_gt_1pct,
        "holders_known": bool(top) or (holders is not None and holders > 0),
        "age_minutes": round(age_f, 2) if age_f is not None else None,
        "holders_per_min": holders_per_min,
        "flash_holders": flash_holders,
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


def token_to_cockpit_input(token: dict[str, Any]) -> dict[str, Any]:
    """Normalize a moon/snipe feed card into extract_cockpit input shape."""
    safety = token.get("safety") or {}
    market = token.get("market") or {}
    if not market and token.get("priceChange"):
        market = {
            "priceChange": token.get("priceChange"),
            "liquidity": token.get("liquidity"),
            "volume": token.get("volume"),
            "marketCap": token.get("mcap_usd"),
        }
    return {
        "tokenAddress": token.get("tokenAddress") or token.get("mint") or "",
        "chainId": token.get("chainId") or "solana",
        "symbol": token.get("symbol"),
        "name": token.get("name"),
        "mcap_usd": token.get("mcap_usd"),
        "analyzedAt": token.get("analyzedAt") or token.get("scanned_at"),
        "safety": safety,
        "market": {
            **market,
            "pumpfun": token.get("pumpfun") or market.get("pumpfun") or {},
            "liquidity": market.get("liquidity")
            or token.get("liquidity")
            or {"usd": (token.get("market") or {}).get("liquidity_usd")},
        },
        "pumpfun": token.get("pumpfun") or {},
        "bundleSniper": token.get("bundleSniper") or token.get("bundle_sniper") or {},
    }


def control_surface_gate(cockpit: dict[str, Any]) -> tuple[bool, str | None]:
    """Money-mode fail-closed on mint/freeze.

    - present → never alert (can inflate / freeze bags)
    - n/a → never alert (incomplete audit — not proven clean)
    - revoked → ok for control surface
    """
    mint_s = str(cockpit.get("mint_authority") or "n/a").lower()
    freeze_s = str(cockpit.get("freeze_authority") or "n/a").lower()
    if mint_s == "present":
        return False, "mint authority still PRESENT — can inflate supply"
    if freeze_s == "present":
        return False, "freeze authority still PRESENT — can lock sells"
    if mint_s == "n/a":
        return False, "mint authority n/a — incomplete control surface"
    if freeze_s == "n/a":
        return False, "freeze authority n/a — incomplete control surface"
    if mint_s != "revoked" or freeze_s != "revoked":
        return False, f"control surface not clean (mint={mint_s}, freeze={freeze_s})"
    return True, None


def format_cockpit_telegram(cockpit: dict[str, Any]) -> str:
    """Short Germanus-style fact blurb for money alerts."""

    def _u(n: Any) -> str:
        try:
            v = float(n)
        except (TypeError, ValueError):
            return "n/a"
        if v >= 1e6:
            return f"${v / 1e6:.2f}M"
        if v >= 1e3:
            return f"${v / 1e3:.1f}k"
        return f"${v:.0f}"

    mint_s = cockpit.get("mint_authority") or "n/a"
    freeze_s = cockpit.get("freeze_authority") or "n/a"
    lp = cockpit.get("lp_status") or "n/a"
    top1 = cockpit.get("top1_pct")
    top1_s = f"{top1}%" if top1 is not None else "n/a"
    hold = cockpit.get("holders")
    hold_s = str(hold) if hold is not None else "n/a"
    liq = _u(cockpit.get("liquidity_usd"))
    bun = cockpit.get("bundled_pct")
    bun_s = f"{bun}%" if bun is not None else "n/a"
    cov = cockpit.get("coverage_pct")
    cov_s = f"{cov}%" if cov is not None else "n/a"
    flash = ""
    if cockpit.get("flash_holders"):
        hpm = cockpit.get("holders_per_min")
        age = cockpit.get("age_minutes")
        flash = (
            f"\n⚠ <b>FLASH HOLDERS</b> {hold_s} in {age}m"
            + (f" (~{hpm}/min)" if hpm is not None else "")
            + " — snipers/bundle concealment"
        )
    return (
        f"\n\n🔬 <b>LAB</b> (facts · not a buy call)\n"
        f"mint <b>{mint_s}</b> · freeze <b>{freeze_s}</b> · LP {lp}\n"
        f"liq {liq} · top1 {top1_s} · holders {hold_s}\n"
        f"bundled {bun_s} · coverage {cov_s}"
        f"{flash}"
    )


def liquidity_drift_pct(prev_liq: float | None, cur_liq: float | None) -> float | None:
    """Freshness probe — Germanus uses 10% liquidity drift threshold."""
    if prev_liq is None or cur_liq is None:
        return None
    if prev_liq <= 0:
        return 100.0 if cur_liq > 0 else 0.0
    return abs(cur_liq - prev_liq) / prev_liq * 100.0
