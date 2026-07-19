"""Full safety report for Padre Trenches tokens — bundle, dev, sniper, community."""

from __future__ import annotations

from typing import Any

from services.avoid_filters import analyze_avoid_flags
from services.trench_analyzer import analyze_community, analyze_snipers


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _check_bundle_risks(safety: dict) -> dict[str, Any]:
    """Detect bundled launches via RugCheck insider graph + risk names."""
    risks = safety.get("risks") or []
    bundle_flags: list[str] = []
    bundled = False

    for risk in risks:
        name = (risk.get("name") or "").lower()
        desc = (risk.get("description") or "").lower()
        level = risk.get("level", "")
        text = f"{name} {desc}"
        if any(
            kw in text
            for kw in (
                "bundle", "bundled", "insider", "sniper",
                "single holder", "high ownership", "concentrated",
            )
        ):
            if level in ("danger", "critical", "warn"):
                bundle_flags.append(risk.get("name") or desc[:60])
                if level in ("danger", "critical"):
                    bundled = True

    if safety.get("insider_detected"):
        bundled = True
        bundle_flags.append("RugCheck insider graph detected")
    if int(safety.get("insider_networks") or 0) > 0:
        bundled = True
        bundle_flags.append(
            f"{safety['insider_networks']} linked insider network(s)"
        )
    for h in safety.get("insider_holders") or []:
        bundled = True
        bundle_flags.append(
            f"Insider wallet holds {h.get('pct', 0):.1f}%"
        )

    return {
        "bundled": bundled,
        "flags": bundle_flags[:6],
        "risk_level": (
            "critical" if bundled else "low" if not bundle_flags else "medium"
        ),
    }


def build_safety_report(
    safety: dict,
    pair: dict,
    trench: dict | None = None,
    checker_hub: dict | None = None,
    smart_money: dict | None = None,
) -> dict[str, Any]:
    """Human-readable safety breakdown for trench traders."""
    pump = pair.get("pumpfun") or {}
    snipers = analyze_snipers(safety, pump)
    community = analyze_community(pump, pair)
    bundle = _check_bundle_risks(safety)
    trench = trench or {}
    sm = smart_money or {}

    dev_pct = _safe_float(safety.get("creator_pct"))
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, weight: int = 1):
        checks.append({"name": name, "ok": ok, "detail": detail, "weight": weight})

    hub = checker_hub or {}
    consensus = hub.get("consensus") or {}
    if consensus:
        add(
            "checker_consensus",
            consensus.get("verdict") == "PASS",
            f"{consensus.get('passed', 0)}/{consensus.get('total', 0)} checkers passed "
            f"({consensus.get('score', 0)}%)",
            weight=2,
        )
    if sm:
        add(
            "major_trader_or_whale",
            bool(sm.get("anti_rug_signal")),
            sm.get("summary") or "No major trader / whale buy",
            weight=2 if sm.get("anti_rug_signal") else 1,
        )
    add("rugcheck_pass", safety.get("passed"), "RugCheck + Padre audit clearance")
    add("not_rugged", not safety.get("rugged"), "Not flagged rugged")
    add("no_honeypot", not safety.get("is_honeypot"), "No honeypot detected")
    add("mint_revoked", not safety.get("mint_authority"), "Mint authority revoked")
    add("freeze_revoked", not safety.get("freeze_authority"), "Freeze authority revoked")
    add(
        "dev_clean",
        dev_pct <= 8 and not bundle["bundled"],
        f"Dev holds {dev_pct:.1f}% — bundle risk {bundle['risk_level']}",
        weight=2,
    )
    add(
        "no_insider_snipers",
        snipers["insider_count"] == 0 and snipers["risk_level"] != "critical",
        f"Insiders: {snipers['insider_count']}, max wallet {snipers['max_wallet_pct']:.1f}%",
        weight=2,
    )
    add(
        "holder_count",
        int(safety.get("total_holders") or 0) >= 8,
        f"{safety.get('total_holders', 0)} holders (want ≥8 for distribution)",
    )
    top5 = sum(float(h.get("pct") or 0) for h in (safety.get("top_holders") or [])[:5])
    add(
        "holder_distribution",
        top5 <= 40 or not safety.get("top_holders"),
        f"Top 5 wallets hold {top5:.1f}% (want ≤40%)",
    )
    add(
        "real_dex_data",
        not pair.get("is_pumpfun_synthetic"),
        "DexScreener trade data available" if not pair.get("is_pumpfun_synthetic")
        else "No Dex data yet — too early to verify volume",
    )
    add(
        "community_signal",
        community["active"],
        f"{community['reply_count']} replies, {community['buys_m5']} buys in 5m",
    )

    mint = safety.get("mint") or ""
    avoid = safety.get("avoid") or analyze_avoid_flags(
        safety, pump, mint=mint, pair=pair
    )
    add(
        "not_junk_pattern",
        not avoid.get("avoid"),
        avoid.get("summary") or "No ghost-launch / blocklist flags",
        weight=2,
    )

    score = sum(c["weight"] for c in checks if c["ok"])
    max_score = sum(c["weight"] for c in checks)
    pct = round(score / max(max_score, 1) * 100)

    blockers = [c for c in checks if not c["ok"] and c["weight"] >= 2]
    warnings = [c for c in checks if not c["ok"] and c["weight"] < 2]

    if avoid.get("hard_avoid") or avoid.get("avoid"):
        tier = "UNSAFE"
        blockers.append({
            "name": "avoid_filter",
            "detail": avoid.get("summary") or "Junk / ghost-launch pattern",
        })
    elif consensus.get("verdict") == "FAIL":
        tier = "UNSAFE"
    elif bundle["bundled"] or safety.get("is_honeypot") or safety.get("rugged"):
        tier = "UNSAFE"
    elif not safety.get("passed") or snipers["risk_level"] == "critical":
        tier = "HIGH_RISK"
    elif snipers["risk_level"] == "high":
        tier = "CAUTION"
        blockers.append({
            "name": "whale_sniper",
            "detail": f"Largest wallet {snipers['max_wallet_pct']:.1f}% — likely sniper",
        })
    elif blockers:
        tier = "CAUTION"
    elif pct >= 85 and trench.get("passed"):
        tier = "SAFE_ENTRY"
    elif pct >= 70:
        tier = "WATCH"
    else:
        tier = "AVOID"

    return {
        "tier": tier,
        "score": pct,
        "checks": checks,
        "bundle": bundle,
        "snipers": snipers,
        "community": community,
        "dev": {
            "creator": safety.get("creator"),
            "holds_pct": dev_pct,
            "sold": safety.get("creator_sold"),
            "token_count": safety.get("creator_token_count", 0),
            "mint_authority": safety.get("mint_authority"),
        },
        "blockers": [{"name": c["name"], "detail": c["detail"]} for c in blockers],
        "warnings": [{"name": c["name"], "detail": c["detail"]} for c in warnings],
        "checkerHub": hub,
        "smartMoney": sm,
        "avoid": avoid,
        "verdict": _verdict_text(
            tier, blockers, bundle, trench, smart_money=sm, avoid=avoid
        ),
    }


def _verdict_text(
    tier: str,
    blockers: list,
    bundle: dict,
    trench: dict,
    smart_money: dict | None = None,
    avoid: dict | None = None,
) -> str:
    sm = smart_money or {}
    avoid = avoid or {}
    sm_note = ""
    if sm.get("anti_rug_signal"):
        sm_note = f" · {sm.get('signal', 'WHALE').replace('_', ' ')} anti-rug signal"
    if tier == "UNSAFE":
        if avoid.get("avoid"):
            return f"UNSAFE — {avoid.get('summary') or 'junk pattern'}. Skip."
        if bundle.get("bundled"):
            return "UNSAFE — bundled/insider launch detected. Do not enter."
        return "UNSAFE — honeypot or rugged. Do not enter."
    if tier == "HIGH_RISK":
        return "HIGH RISK — failed core safety. Wait or skip."
    if tier == "CAUTION":
        return f"CAUTION — {blockers[0]['detail'] if blockers else 'issues detected'}{sm_note}"
    if tier == "SAFE_ENTRY":
        return (
            "SAFE ENTRY — passed bundle/dev/sniper checks with real momentum"
            + sm_note
        )
    if tier == "WATCH":
        base = "WATCH — safe-ish but wait for $6k approach + real volume"
        if sm.get("anti_rug_signal"):
            return base + " · major trader/whale buy supports not-a-rug case"
        return base
    return "AVOID — insufficient safety or momentum"