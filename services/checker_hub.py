"""Multi-tool security checker hub — RugCheck, Padre, DexScreener, pump.fun."""

from __future__ import annotations

from typing import Any

RUGCHECK_WEB = "https://rugcheck.xyz/tokens"
SOLSCAN = "https://solscan.io/token"
BIRDEYE = "https://birdeye.so/token"
DEXSCREENER = "https://dexscreener.com/solana"
BUBBLEMAPS = "https://app.bubblemaps.io/sol/token"
GMGN = "https://gmgn.ai/sol/token"


def _status_from_flags(
    fail: bool, warn: bool, unknown: bool = False
) -> str:
    if unknown:
        return "unknown"
    if fail:
        return "fail"
    if warn:
        return "warn"
    return "pass"


def _checker(
    checker_id: str,
    name: str,
    status: str,
    summary: str,
    details: list[str] | None = None,
    issues: list[str] | None = None,
    url: str | None = None,
    score: str | None = None,
) -> dict[str, Any]:
    icon = {"pass": "✓", "warn": "⚠", "fail": "✗", "unknown": "?"}.get(status, "?")
    return {
        "id": checker_id,
        "name": name,
        "status": status,
        "icon": icon,
        "summary": summary,
        "score": score,
        "details": (details or [])[:8],
        "issues": (issues or [])[:6],
        "url": url,
    }


def build_checker_links(chain_id: str, mint: str, dex_url: str | None = None) -> dict[str, str]:
    links = {
        "rugcheck": f"{RUGCHECK_WEB}/{mint}",
        "solscan": f"{SOLSCAN}/{mint}",
        "birdeye": f"{BIRDEYE}/{mint}",
        "dexscreener": dex_url or f"{DEXSCREENER}/{mint}",
    }
    if chain_id == "solana":
        links["pumpfun"] = f"https://pump.fun/coin/{mint}"
        links["bubblemaps"] = f"{BUBBLEMAPS}/{mint}"
        links["gmgn"] = f"{GMGN}/{mint}"
    return links


def run_checker_hub(
    chain_id: str,
    mint: str,
    safety: dict,
    pair: dict | None = None,
    padre_audit: dict | None = None,
) -> dict[str, Any]:
    """Aggregate all security tools into one consensus report."""
    pair = pair or {}
    pump = pair.get("pumpfun") or {}
    padre_parsed = safety.get("padre") or {}
    risks = safety.get("risks") or []

    danger = [r for r in risks if r.get("level") in ("danger", "critical")]
    warns = [r for r in risks if r.get("level") == "warn"]

    # --- RugCheck ---
    rug_unknown = safety.get("pumpfun_native") and not safety.get("rug_score")
    rug_fail = bool(
        safety.get("rugged")
        or safety.get("is_honeypot")
        or len(danger) > 0
        or safety.get("rug_score", 0) > 50
    )
    rug_warn = bool(
        not rug_fail
        and (
            safety.get("rug_score", 0) > 25
            or len(warns) > 0
            or safety.get("warn_risks", 0) > 2
        )
    )
    rug_status = _status_from_flags(rug_fail, rug_warn, rug_unknown)
    rug_issues = [f"{r.get('name')}: {r.get('description', '')}" for r in danger[:4]]
    rug_details = [
        f"Rug score {safety.get('rug_score', '?')}/100 (lower = safer)",
        f"LP locked {safety.get('lp_locked_pct', 0):.0f}%",
        f"{safety.get('total_holders', 0)} holders",
        f"{len(danger)} danger · {len(warns)} warn risks",
    ]
    rug_checker = _checker(
        "rugcheck",
        "RugCheck",
        rug_status,
        "Clean" if rug_status == "pass" else "Risks detected" if rug_warn else "Failed" if rug_fail else "Not indexed yet",
        details=rug_details,
        issues=rug_issues,
        url=f"{RUGCHECK_WEB}/{mint}",
        score=f"{safety.get('rug_score', '?')}/100",
    )

    # --- RugCheck Insider / Bundle scan ---
    insider_fail = bool(
        safety.get("insider_detected")
        or safety.get("insider_holders")
        or int(safety.get("insider_networks") or 0) > 0
    )
    insider_warn = not insider_fail and any(
        "insider" in (r.get("name") or "").lower()
        or "bundle" in (r.get("name") or "").lower()
        for r in risks
    )
    insider_details = []
    if safety.get("insider_detected"):
        insider_details.append("Insider wallet graph detected")
    if safety.get("insider_networks"):
        insider_details.append(f"{safety['insider_networks']} linked insider network(s)")
    for h in (safety.get("insider_holders") or [])[:3]:
        insider_details.append(f"Insider holds {h.get('pct', 0):.1f}%")
    bundle_checker = _checker(
        "insider_scan",
        "Bundle / Insider Scan",
        _status_from_flags(insider_fail, insider_warn, rug_unknown),
        "No insiders" if not insider_fail and not insider_warn else "Insider activity detected",
        details=insider_details or ["No insider wallets flagged"],
        url=f"{RUGCHECK_WEB}/{mint}",
    )

    # --- Padre Terminal audit ---
    padre_unknown = not padre_parsed.get("available")
    padre_fail = bool(
        padre_parsed.get("honeypot")
        or padre_parsed.get("danger_checks", 0) > 0
    )
    padre_warn = bool(
        not padre_fail and padre_parsed.get("warn_checks", 0) > 0
    )
    padre_details = []
    if padre_parsed.get("available"):
        padre_details = [
            f"{padre_parsed.get('rugcheck_checks', 0)} rug checks",
            f"{padre_parsed.get('danger_checks', 0)} danger · {padre_parsed.get('warn_checks', 0)} warn",
        ]
    padre_checker = _checker(
        "padre",
        "Padre Terminal",
        _status_from_flags(padre_fail, padre_warn, padre_unknown),
        "Audit clear" if not padre_fail and not padre_warn else "Warnings" if padre_warn else "Failed" if padre_fail else "No audit data",
        details=padre_details,
        issues=(padre_parsed.get("issues") or [])[:4],
        url=f"https://trade.padre.gg/trade/solana/{mint}",
    )

    # --- Authority / Dev checker ---
    auth_fail = bool(
        safety.get("mint_authority")
        or safety.get("freeze_authority")
        or float(safety.get("creator_pct") or 0) > 15
    )
    auth_warn = bool(
        not auth_fail
        and (
            float(safety.get("creator_pct") or 0) > 8
            or safety.get("mutable_metadata")
        )
    )
    auth_checker = _checker(
        "authorities",
        "Mint / Dev Check",
        _status_from_flags(auth_fail, auth_warn, rug_unknown),
        "Authorities revoked" if not auth_fail and not auth_warn else "Dev risk",
        details=[
            f"Mint authority: {'ACTIVE ⚠' if safety.get('mint_authority') else 'Revoked ✓'}",
            f"Freeze authority: {'ACTIVE ⚠' if safety.get('freeze_authority') else 'Revoked ✓'}",
            f"Dev holds: {safety.get('creator_pct', 0):.1f}%",
            f"Creator tokens launched: {safety.get('creator_token_count', 0)}",
        ],
        url=f"{SOLSCAN}/{mint}",
    )

    # --- Liquidity / pull-risk checker ---
    on_curve = bool(safety.get("on_bonding_curve"))
    quote_sol = float(safety.get("lp_quote_sol") or 0)
    lp_locked = float(safety.get("lp_locked_pct") or 0)
    lp_unlocked = float(safety.get("lp_unlocked") or 0)
    liq_fail = bool(
        (on_curve and 0 < quote_sol < 0.5)
        or (not on_curve and (lp_unlocked > 0 or (0 < lp_locked < 80)))
        or (safety.get("avoid") or {}).get("flags")
        and any(
            f in ((safety.get("avoid") or {}).get("flags") or [])
            for f in ("drained_curve", "lp_unlocked", "lp_not_locked")
        )
    )
    liq_warn = bool(
        not liq_fail
        and (
            (on_curve and quote_sol < 2.0)
            or (not on_curve and lp_locked < 95)
            or safety.get("creator_sold")
        )
    )
    liq_details = [
        f"Market: {safety.get('market_type') or ('pump curve' if on_curve else 'dex')}",
        f"Exit SOL on curve/pool: {quote_sol:.3f}",
        f"LP locked: {lp_locked:.0f}% · unlocked units: {lp_unlocked:g}",
        f"Creator sold: {'YES ⚠' if safety.get('creator_sold') else 'No'}",
    ]
    liq_checker = _checker(
        "liquidity",
        "Liquidity / Pull Risk",
        _status_from_flags(liq_fail, liq_warn, rug_unknown and not quote_sol),
        "Drained / unlockable" if liq_fail else "Exit liquidity OK" if not liq_warn else "Thin liquidity",
        details=liq_details,
        issues=[
            r
            for r in (safety.get("issues") or [])
            if any(k in r.lower() for k in ("liquidity", "lp ", "curve", "drained", "unlock"))
        ][:4],
        url=f"{RUGCHECK_WEB}/{mint}",
    )

    # --- DexScreener market verification ---
    synthetic = bool(pair.get("is_pumpfun_synthetic"))
    vol_m5 = float((pair.get("volume") or {}).get("m5") or 0)
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    dex_fail = False
    dex_warn = synthetic or vol_m5 < 100
    dex_details = []
    if not synthetic:
        m5_txns = (pair.get("txns") or {}).get("m5") or {}
        dex_details = [
            f"5m volume ${vol_m5:,.0f}",
            f"Liquidity ${liq:,.0f}",
            f"Buys {m5_txns.get('buys', 0)} / sells {m5_txns.get('sells', 0)} (5m)",
            "Real DexScreener trade data ✓",
        ]
    else:
        dex_details = ["No DexScreener pair — too early to verify volume"]
    dex_checker = _checker(
        "dexscreener",
        "DexScreener",
        _status_from_flags(dex_fail, dex_warn, synthetic),
        "Verified trades" if not synthetic and not dex_warn else "Synthetic / low vol" if synthetic else "Low activity",
        details=dex_details,
        url=pair.get("url") or f"{DEXSCREENER}/{mint}",
    )

    # --- pump.fun native ---
    pf_unknown = not pump
    pf_fail = bool(pump.get("is_banned") or pump.get("complete"))
    pf_warn = pf_unknown
    pf_details = []
    if pump:
        pf_details = [
            f"Bonding curve: {'Graduated' if pump.get('complete') else 'On curve ✓'}",
            f"MCap ${float(pump.get('usd_market_cap') or 0):,.0f}",
            f"Replies: {pump.get('reply_count', 0)}",
            f"Banned: {'Yes ⚠' if pump.get('is_banned') else 'No'}",
        ]
    pump_checker = _checker(
        "pumpfun",
        "pump.fun",
        _status_from_flags(pf_fail, pf_warn, pf_unknown),
        "Active on curve" if not pf_fail and not pf_unknown else "Banned / graduated" if pf_fail else "Unknown",
        details=pf_details,
        url=f"https://pump.fun/coin/{mint}" if chain_id == "solana" else None,
    )

    # --- Holder distribution (RugCheck top holders) ---
    top_holders = safety.get("top_holders") or []
    top5_pct = sum(float(h.get("pct") or 0) for h in top_holders[:5])
    top10_pct = sum(float(h.get("pct") or 0) for h in top_holders[:10])
    holder_unknown = rug_unknown and not top_holders
    holder_fail = top5_pct > 55 or top10_pct > 75
    holder_warn = not holder_fail and (top5_pct > 40 or top10_pct > 60)
    holder_details = []
    if top_holders:
        holder_details = [
            f"Top 5 hold {top5_pct:.1f}%",
            f"Top 10 hold {top10_pct:.1f}%",
            f"{safety.get('total_holders', 0)} total holders",
        ]
        for h in top_holders[:3]:
            label = "insider" if h.get("insider") else "wallet"
            holder_details.append(f"{label}: {float(h.get('pct', 0)):.1f}%")
    else:
        holder_details = ["Awaiting RugCheck holder index"]
    holder_checker = _checker(
        "holders",
        "Holder Distribution",
        _status_from_flags(holder_fail, holder_warn, holder_unknown),
        "Well distributed" if not holder_fail and not holder_warn else "Concentrated supply",
        details=holder_details,
        url=f"{BUBBLEMAPS}/{mint}" if chain_id == "solana" else f"{SOLSCAN}/{mint}",
        score=f"top5 {top5_pct:.0f}%" if top_holders else None,
    )

    checkers = [
        rug_checker,
        bundle_checker,
        padre_checker,
        auth_checker,
        liq_checker,
        holder_checker,
        dex_checker,
        pump_checker,
    ]

    weights = {"pass": 1.0, "warn": 0.5, "unknown": 0.25, "fail": 0.0}
    scored = [weights.get(c["status"], 0) for c in checkers]
    consensus_score = round(sum(scored) / len(scored) * 100)

    passed = sum(1 for c in checkers if c["status"] == "pass")
    warned = sum(1 for c in checkers if c["status"] == "warn")
    failed = sum(1 for c in checkers if c["status"] == "fail")
    unknown = sum(1 for c in checkers if c["status"] == "unknown")

    if failed >= 2 or any(
        c["status"] == "fail" for c in checkers[:2]
    ):
        verdict = "FAIL"
        verdict_text = f"{failed} checker(s) failed — do not enter"
    elif failed == 1 or warned >= 2:
        verdict = "WARN"
        verdict_text = f"{warned + failed} warning(s) — proceed with caution"
    elif consensus_score >= 75:
        verdict = "PASS"
        verdict_text = f"{passed}/{len(checkers)} checkers passed"
    else:
        verdict = "WARN"
        verdict_text = "Incomplete data — wait for more checkers to index"

    all_issues: list[str] = []
    for c in checkers:
        for issue in c.get("issues") or []:
            if issue not in all_issues:
                all_issues.append(issue)

    return {
        "consensus": {
            "score": consensus_score,
            "verdict": verdict,
            "summary": verdict_text,
            "passed": passed,
            "warned": warned,
            "failed": failed,
            "unknown": unknown,
            "total": len(checkers),
        },
        "checkers": checkers,
        "links": build_checker_links(chain_id, mint, pair.get("url")),
        "issues": all_issues[:10],
    }