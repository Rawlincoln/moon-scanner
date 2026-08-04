"""Jito-style bundle + sniper detection.

A bundle = group of txs (often via Jito) that land atomically in the same block
(or tightly coordinated across 2–5 consecutive blocks). Classic launch package:

  1. Token creation / bonding curve init
  2. Multiple buys from wallets the deployer controls

From outside it looks organic. In reality one entity captured cheap supply.

Patterns we score (best-effort from RugCheck holders/risks + flow):
  - Classic same-block / multi-wallet same-slot (insider graph, risk text)
  - Gradual staggered (many similar mid-bags — multi-wallet control)
  - Funding cluster (insider networks / graph)
  - Similar-size buys (near-identical holder %)
  - Early concentration → estimated "bundled %" of non-pool supply

Practical thresholds (2026 trader norms):
  Bundled % of supply:  <5% noise · 5–12% medium · 12–25% high · >25% critical
  Same-block buyers:    1–3 ok · 4–8 suspicious · 9–20 almost always bundle · 20+ critical
  First 1–2 blocks %:   <8% · 8–15% · 15–30% · >30%
  Combined red flags (2+): bundled>12% · 8+ early wallets · shared funding · majority fresh
"""

from __future__ import annotations

import re
from typing import Any

from config import MAX_DEV_HOLD_PCT, MAX_SNIPER_WALLET_PCT

# Bonding-curve / AMM pool bags — not retail snipers
_POOL_PCT_MIN = 40.0
# Mid bags typical of multi-wallet Jito fills (not dust, not single whale)
_CLUSTER_PCT_MIN = 1.5
_CLUSTER_PCT_MAX = 12.0
_CLUSTER_SIMILARITY = 0.22  # near-identical buy sizes

# --- Bundled % of supply (core) ---
BUNDLE_PCT_NOISE = 5.0       # under 5% → usually ignore
BUNDLE_PCT_MEDIUM = 5.0      # 5–12% → watch / medium
BUNDLE_PCT_HIGH = 12.0       # 12–25% → high risk coordinated dump
BUNDLE_PCT_CRITICAL = 25.0   # >25% → extremely dangerous, skip
BUNDLE_PCT_HARD_SKIP = 20.0  # >20% + other signals → hard skip for most

# --- Same-block / early multi-wallet counts ---
SAME_BLOCK_OK = 3            # 1–3 wallets → normal (dev + real snipers)
SAME_BLOCK_SUSPICIOUS = 4    # 4–8 → suspicious
SAME_BLOCK_BUNDLE = 9        # 9–20 → almost always a bundle
SAME_BLOCK_CRITICAL = 20     # 20+ → critical

# --- First 1–2 blocks concentration (early non-pool supply) ---
EARLY_CONC_NOISE = 8.0
EARLY_CONC_MEDIUM = 15.0
EARLY_CONC_HIGH = 30.0

# --- Fresh wallet ratio among early buyers (when age data exists) ---
FRESH_RATIO_MEDIUM = 0.30
FRESH_RATIO_HIGH = 0.60
FRESH_RATIO_CRITICAL = 0.80

_PCT_IN_TEXT = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*%",
    re.I,
)


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _i(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _non_pool_holders(top_holders: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in top_holders or []:
        pct = _f(h.get("pct"))
        if pct <= 0 or pct >= _POOL_PCT_MIN:
            continue
        owner = str(h.get("owner") or h.get("address") or h.get("wallet") or "")
        out.append(
            {
                "pct": pct,
                "owner": owner,
                "insider": bool(h.get("insider")),
                "short": (owner[:8] + "…") if len(owner) > 10 else owner,
            }
        )
    return out


def _extract_bundled_pct_from_risks(safety: dict) -> float | None:
    """Parse explicit bundled % from RugCheck / Padre risk text when present."""
    candidates: list[float] = []
    for risk in safety.get("risks") or []:
        name = str(risk.get("name") or "")
        desc = str(risk.get("description") or "")
        value = str(risk.get("value") or "")
        text = f"{name} {desc} {value}"
        tl = text.lower()
        if not any(k in tl for k in ("bundle", "bundled", "insider", "sniper")):
            continue
        for m in _PCT_IN_TEXT.finditer(text):
            p = _f(m.group(1))
            if 0 < p <= 100:
                candidates.append(p)
        # value field sometimes is just the number
        try:
            raw = risk.get("value")
            if raw is not None and not isinstance(raw, str):
                p = float(raw)
                if 0 < p <= 100:
                    candidates.append(p)
                elif p > 100:  # basis points style
                    candidates.append(min(100.0, p / 100.0))
        except (TypeError, ValueError):
            pass
    # Direct fields some APIs expose
    for key in (
        "bundled_pct",
        "bundle_pct",
        "bundledPercent",
        "bundlePercent",
        "insider_pct",
        "insiderPercent",
    ):
        p = _f(safety.get(key))
        if 0 < p <= 100:
            candidates.append(p)
    if not candidates:
        return None
    return max(candidates)


def _detect_similar_size_cluster(holders: list[dict[str, Any]]) -> dict[str, Any]:
    """Similar-size buys across many wallets = classic multi-wallet bundle fill.

    Wallet counts map to same-block buyer thresholds:
      1–3 → noise · 4–8 → suspicious · 9–20 → almost always bundle · 20+ critical
    """
    mids = [
        h
        for h in holders
        if _CLUSTER_PCT_MIN <= h["pct"] <= _CLUSTER_PCT_MAX
    ]
    if len(mids) < 3:
        return {
            "clustered": False,
            "count": len(mids),
            "largest_cluster": 0,
            "cluster_pct_sum": 0.0,
            "same_block_wallets": len(mids),
            "flags": [],
            "pattern": None,
        }

    flags: list[str] = []
    used: set[int] = set()
    best_group: list[dict] = []
    for i, a in enumerate(mids):
        if i in used:
            continue
        group = [a]
        for j, b in enumerate(mids):
            if j <= i or j in used:
                continue
            mid = (a["pct"] + b["pct"]) / 2
            if mid <= 0:
                continue
            if abs(a["pct"] - b["pct"]) / mid <= _CLUSTER_SIMILARITY:
                group.append(b)
                used.add(j)
        used.add(i)
        if len(group) > len(best_group):
            best_group = group

    cluster_sum = sum(h["pct"] for h in best_group)
    n = len(best_group)
    pattern = None
    # Use total mid bags as same-block proxy when cluster is tight
    same_block_est = max(n, len(mids) if n >= 4 else n)

    if n >= SAME_BLOCK_CRITICAL or same_block_est >= SAME_BLOCK_CRITICAL:
        pattern = "classic_same_block_critical"
        flags.append(
            f"Critical same-block style: {max(n, same_block_est)} wallets "
            f"tight range (~{best_group[0]['pct']:.1f}% each) — "
            f"~{cluster_sum:.0f}% coordinated"
        )
    elif n >= SAME_BLOCK_BUNDLE:
        pattern = "classic_same_block_style"
        flags.append(
            f"Almost always a bundle: {n} wallets ~{best_group[0]['pct']:.1f}% each "
            f"(9+ same-block style) — ~{cluster_sum:.0f}% supply"
        )
    elif n >= SAME_BLOCK_SUSPICIOUS:
        pattern = "similar_size_buys"
        flags.append(
            f"Suspicious: {n} wallets similar buy sizes ~{best_group[0]['pct']:.1f}% "
            f"(4–8 same-block zone) — ~{cluster_sum:.0f}% supply"
        )
    elif n >= 3:
        pattern = "similar_size_buys"
        flags.append(
            f"Several close buy sizes: {n} wallets ~{best_group[0]['pct']:.1f}% each"
        )

    # Many mid bags without tight similarity = staggered bundle (2–5 blocks)
    if len(mids) >= SAME_BLOCK_BUNDLE and not flags:
        pattern = "staggered_bundle"
        flags.append(
            f"Staggered multi-wallet book: {len(mids)} mid bags "
            f"({_CLUSTER_PCT_MIN}–{_CLUSTER_PCT_MAX}%) — coordinated across slots"
        )
        cluster_sum = sum(h["pct"] for h in mids[:15])
        best_group = mids[:15]
        same_block_est = len(mids)

    return {
        "clustered": bool(flags) or n >= SAME_BLOCK_SUSPICIOUS,
        "count": len(mids),
        "largest_cluster": len(best_group),
        "cluster_pct_sum": round(cluster_sum, 2),
        "same_block_wallets": same_block_est,
        "flags": flags[:3],
        "pattern": pattern,
    }


def _risk_text_flags(safety: dict) -> list[str]:
    flags: list[str] = []
    for risk in safety.get("risks") or []:
        name = str(risk.get("name") or "")
        desc = str(risk.get("description") or "")
        level = str(risk.get("level") or "")
        value = str(risk.get("value") or "")
        text = f"{name} {desc} {value}".lower()
        if any(
            kw in text
            for kw in (
                "bundle",
                "bundled",
                "jito",
                "insider",
                "sniper",
                "snipe",
                "same block",
                "same-block",
                "same slot",
                "coordinated",
                "high ownership",
                "concentrated",
                "single holder",
                "funding",
                "linked wallet",
            )
        ):
            label = name or desc[:50] or "risk"
            flags.append(f"[{level or 'risk'}] {label}" + (f" {value}" if value else ""))
    for issue in (safety.get("padre") or {}).get("issues") or []:
        t = str(issue).lower()
        if any(k in t for k in ("bundle", "sniper", "insider", "jito", "holder")):
            flags.append(str(issue)[:90])
    for issue in safety.get("issues") or []:
        t = str(issue).lower()
        if any(k in t for k in ("bundle", "sniper", "insider", "concentrated", "jito")):
            flags.append(str(issue)[:90])
    return flags[:10]


def analyze_bundle_and_snipers(
    safety: dict | None = None,
    pump: dict | None = None,
    pair: dict | None = None,
    *,
    age_minutes: float | None = None,
    mcap_usd: float | None = None,
) -> dict[str, Any]:
    """Full Jito-bundle + sniper report with estimated bundled %."""
    safety = safety or {}
    pump = pump or {}
    pair = pair or {}

    top = safety.get("top_holders") or []
    holders = _non_pool_holders(top)
    creator = str(safety.get("creator") or pump.get("creator") or "")
    creator_pct = _f(safety.get("creator_pct"))
    if creator_pct <= 0 and creator and holders:
        for h in holders:
            if creator and creator in h.get("owner", ""):
                creator_pct = h["pct"]
                break

    insider_wallets = [h for h in holders if h.get("insider")]
    for h in safety.get("insider_holders") or []:
        pct = _f(h.get("pct"))
        if pct >= _POOL_PCT_MIN:
            continue
        owner = str(h.get("owner") or h.get("address") or "")
        if not any(owner and owner == x.get("owner") for x in insider_wallets):
            insider_wallets.append(
                {
                    "pct": pct,
                    "owner": owner,
                    "insider": True,
                    "short": (owner[:8] + "…") if len(owner) > 10 else owner,
                }
            )

    max_wallet_pct = max(
        (h["pct"] for h in holders if not h.get("insider")), default=0.0
    )
    large_early = [h for h in holders if h["pct"] >= 10 and not h.get("insider")]
    mid_large = [h for h in holders if h["pct"] >= 6]
    top5 = sum(h["pct"] for h in holders[:5])
    top10 = sum(h["pct"] for h in holders[:10])
    total_holders = _i(safety.get("total_holders"))
    insider_networks = _i(safety.get("insider_networks"))
    insider_detected = bool(safety.get("insider_detected")) or bool(
        safety.get("graphInsidersDetected")
    )

    cluster = _detect_similar_size_cluster(holders)
    risk_flags = _risk_text_flags(safety)
    reported_bundle_pct = _extract_bundled_pct_from_risks(safety)

    # --- Estimate coordinated / bundled % of supply ---
    # Insiders + similar-size cluster (dedupe by taking max of overlapping)
    insider_pct = sum(h["pct"] for h in insider_wallets)
    cluster_pct = float(cluster.get("cluster_pct_sum") or 0)
    large_early_pct = sum(h["pct"] for h in large_early)
    # Conservative estimate: max of explicit sources (avoid double-count)
    estimated_bundle_pct = max(
        insider_pct,
        cluster_pct,
        reported_bundle_pct or 0.0,
    )
    # If multiple large early bags without full cluster match, add partial
    if len(large_early) >= 2 and estimated_bundle_pct < large_early_pct * 0.85:
        estimated_bundle_pct = max(estimated_bundle_pct, large_early_pct * 0.9)
    # Cap at non-pool top10 (can't bundle more than we see)
    if top10 > 0:
        estimated_bundle_pct = min(estimated_bundle_pct, top10)
    estimated_bundle_pct = round(min(100.0, estimated_bundle_pct), 1)

    # --- Bundle score (0–100) ---
    bundle_flags: list[str] = []
    bundle_score = 0
    patterns: list[str] = []

    # Early concentration proxy for "first 1–2 blocks" (non-pool early bags)
    early_conc = estimated_bundle_pct  # coordinated early supply
    same_block_wallets = int(
        cluster.get("same_block_wallets")
        or cluster.get("largest_cluster")
        or len(mid_large)
    )
    # Funding cluster signal
    shared_funding = insider_detected or insider_networks > 0 or len(insider_wallets) >= 2
    # Fresh-wallet proxy: many early bags + few total holders / insider graph
    # (true wallet-age needs chain; we approximate when book is sniper-dominated)
    fresh_proxy = 0.0
    if same_block_wallets >= SAME_BLOCK_SUSPICIOUS and total_holders > 0:
        fresh_proxy = min(1.0, same_block_wallets / max(total_holders, 1) * 2.5)
    if insider_detected and same_block_wallets >= 4:
        fresh_proxy = max(fresh_proxy, 0.65)
    majority_fresh = fresh_proxy >= FRESH_RATIO_HIGH

    if reported_bundle_pct is not None:
        bundle_flags.append(f"Reported bundled ~{reported_bundle_pct:.0f}%")
        if reported_bundle_pct >= BUNDLE_PCT_CRITICAL:
            bundle_score += 50
        elif reported_bundle_pct >= BUNDLE_PCT_HARD_SKIP:
            bundle_score += 42
        elif reported_bundle_pct >= BUNDLE_PCT_HIGH:
            bundle_score += 35
        elif reported_bundle_pct >= BUNDLE_PCT_NOISE:
            bundle_score += 18

    if insider_detected:
        bundle_score += 38
        bundle_flags.append(
            "Shared funding / insider graph (Bubblemaps-style tight cluster)"
        )
        patterns.append("funding_cluster")
    if insider_networks > 0:
        bundle_score += min(28, 10 * insider_networks)
        bundle_flags.append(
            f"{insider_networks} linked network(s) — multi-layer funding chain"
        )
        patterns.append("funding_cluster")
    if insider_wallets:
        bundle_score += min(28, 8 * len(insider_wallets))
        for h in insider_wallets[:3]:
            bundle_flags.append(f"Insider holds {h['pct']:.1f}%")
        patterns.append("insider_holders")

    if cluster.get("clustered"):
        bundle_score += 30
        bundle_flags.extend(cluster.get("flags") or ["Similar-size multi-wallet buys"])
        if cluster.get("pattern"):
            patterns.append(str(cluster["pattern"]))

    # --- Bundled % of supply (2026 trader bands) ---
    # <5% ignore · 5–12% medium · 12–25% high · >25% critical
    if estimated_bundle_pct >= BUNDLE_PCT_CRITICAL:
        bundle_score += 40
        bundle_flags.append(
            f"~{estimated_bundle_pct:.0f}% bundled supply "
            f"(>{BUNDLE_PCT_CRITICAL:.0f}% = extremely dangerous — skip)"
        )
        patterns.append("bundled_pct_critical")
    elif estimated_bundle_pct >= BUNDLE_PCT_HARD_SKIP:
        bundle_score += 32
        bundle_flags.append(
            f"~{estimated_bundle_pct:.0f}% bundled "
            f"(>{BUNDLE_PCT_HARD_SKIP:.0f}% = hard skip for most)"
        )
        patterns.append("bundled_pct_high")
    elif estimated_bundle_pct >= BUNDLE_PCT_HIGH:
        bundle_score += 28
        bundle_flags.append(
            f"~{estimated_bundle_pct:.0f}% bundled supply "
            f"(≥{BUNDLE_PCT_HIGH:.0f}% = high risk of coordinated dump)"
        )
        patterns.append("bundled_pct_high")
    elif estimated_bundle_pct >= BUNDLE_PCT_NOISE:
        bundle_score += 16
        bundle_flags.append(
            f"~{estimated_bundle_pct:.0f}% bundled "
            f"(5–12% = watch closely, check if still holding)"
        )
        patterns.append("bundled_pct_medium")
    # under 5% → noise, no score bump

    # --- First 1–2 blocks concentration ---
    if early_conc >= EARLY_CONC_HIGH:
        bundle_score += 22
        bundle_flags.append(
            f"First-block concentration ~{early_conc:.0f}% (>{EARLY_CONC_HIGH:.0f}% critical)"
        )
    elif early_conc >= EARLY_CONC_MEDIUM:
        bundle_score += 14
        bundle_flags.append(
            f"First-block concentration ~{early_conc:.0f}% (15–30% high risk)"
        )
    elif early_conc >= EARLY_CONC_NOISE:
        bundle_score += 8
        bundle_flags.append(
            f"First-block concentration ~{early_conc:.0f}% (8–15% elevated)"
        )

    # --- Same-block buyer count ---
    if same_block_wallets >= SAME_BLOCK_CRITICAL:
        bundle_score += 35
        bundle_flags.append(
            f"{same_block_wallets} same-block style wallets (20+ = critical)"
        )
        patterns.append("same_block_20plus")
    elif same_block_wallets >= SAME_BLOCK_BUNDLE:
        bundle_score += 30
        bundle_flags.append(
            f"{same_block_wallets} same-block style wallets (9+ = almost always a bundle)"
        )
        patterns.append("same_block_9plus")
    elif same_block_wallets >= SAME_BLOCK_SUSPICIOUS:
        bundle_score += 18
        bundle_flags.append(
            f"{same_block_wallets} same-block style wallets (4–8 = suspicious)"
        )
        patterns.append("same_block_4to8")
    # 1–3 is normal noise

    if majority_fresh:
        bundle_score += 20
        bundle_flags.append(
            f"Majority of early buyers look fresh "
            f"(proxy {fresh_proxy * 100:.0f}% ≥ {FRESH_RATIO_HIGH * 100:.0f}%)"
        )
        patterns.append("fresh_wallets")
    elif fresh_proxy >= FRESH_RATIO_MEDIUM:
        bundle_score += 10
        bundle_flags.append(f"Elevated fresh-wallet share (~{fresh_proxy * 100:.0f}%)")

    if top10 >= 55 and len(holders) >= 5:
        bundle_score += 10
        bundle_flags.append(
            f"Top 10 non-pool hold {top10:.0f}% — looks distributed, still concentrated"
        )
    if len(mid_large) >= 5:
        bundle_score += 10
        bundle_flags.append(
            f"{len(mid_large)} wallets ≥6% — multi-wallet same-block style book"
        )
        patterns.append("multi_wallet_same_block")

    for rf in risk_flags:
        rl = rf.lower()
        if any(k in rl for k in ("bundle", "jito", "same block", "same slot", "coordinated")):
            bundle_score += 22
            bundle_flags.append(rf)
            patterns.append("same_block_risk_flag")
        elif "insider" in rl or "funding" in rl:
            bundle_score += 12
            bundle_flags.append(rf)

    # One-way wash early = bot/Jito fill before organic flow
    txns = (pair.get("txns") or {}).get("m5") or {}
    buys = _i(txns.get("buys"))
    sells = _i(txns.get("sells"))
    if buys >= 35 and sells <= 6 and buys / max(sells, 1) >= 7:
        bundle_score += 16
        bundle_flags.append(
            f"One-way {buys}B/{sells}S — bot/Jito fill, not organic staggered demand"
        )
        patterns.append("bot_fill")

    # Flash launch: create + buys in first moments
    age = age_minutes if age_minutes is not None else _f(pump.get("_age_minutes"))
    if age is None or age <= 0:
        age = _f(pump.get("age_minutes"))
    mcap = mcap_usd if mcap_usd is not None else _f(
        pump.get("usd_market_cap") or pair.get("marketCap")
    )
    if (
        age is not None
        and 0 < age < 1.5
        and mcap >= 10_000
        and estimated_bundle_pct >= BUNDLE_PCT_HIGH
    ):
        bundle_score += 20
        bundle_flags.append(
            f"Block-0 style: ${mcap:,.0f} in {age:.1f}m with ~{estimated_bundle_pct:.0f}% early bags"
        )
        patterns.append("classic_same_block")

    # Impossible holder count for age (245 @ 2m) — snipers/bundle looking "organic"
    flash_holders_hit = False
    flash_holders_note = ""
    if age is not None and age > 0 and total_holders > 0:
        hpm = total_holders / max(age, 0.15)
        if (age <= 3 and total_holders >= 80) or (
            age <= 5 and total_holders >= 120
        ) or (age <= 12 and hpm >= 35 and total_holders >= 50):
            flash_holders_hit = True
            flash_holders_note = (
                f"{total_holders} holders @ {age:.1f}m (velocity ~{hpm:.0f}/min)"
            )
            bundle_score += 28
            bundle_flags.append(
                f"Flash holders: {total_holders} in {age:.1f}m (~{hpm:.0f}/min) "
                "— concealed sniper/bot book, not organic"
            )
            patterns.append("flash_holders")

    # --- Combined red-flag score (2+ of these = high risk) ---
    red_flags: list[str] = []
    if estimated_bundle_pct > BUNDLE_PCT_HIGH:
        red_flags.append(f"bundled%>{BUNDLE_PCT_HIGH:.0f}%")
    if same_block_wallets >= 8:
        red_flags.append("8+ first-block wallets")
    if shared_funding:
        red_flags.append("shared funding source")
    if majority_fresh:
        red_flags.append("majority fresh early wallets")
    if len(red_flags) >= 2:
        bundle_score += 25
        bundle_flags.append(
            f"Combined red flags ({len(red_flags)}/4): {', '.join(red_flags)} — high risk"
        )
        patterns.append("combined_red_flags")

    bundle_score = min(100, bundle_score)

    # Level from 2026 bands + score
    if (
        estimated_bundle_pct >= BUNDLE_PCT_CRITICAL
        or early_conc >= EARLY_CONC_HIGH
        or same_block_wallets >= SAME_BLOCK_CRITICAL
        or bundle_score >= 75
        or (insider_detected and insider_networks > 0 and estimated_bundle_pct >= 12)
    ):
        bundle_level = "critical"
    elif (
        estimated_bundle_pct >= BUNDLE_PCT_HIGH
        or early_conc >= EARLY_CONC_MEDIUM
        or same_block_wallets >= SAME_BLOCK_BUNDLE
        or len(red_flags) >= 2
        or bundle_score >= 48
        or insider_detected
        or len(insider_wallets) >= 2
    ):
        bundle_level = "high"
    elif (
        estimated_bundle_pct >= BUNDLE_PCT_NOISE
        or early_conc >= EARLY_CONC_NOISE
        or same_block_wallets >= SAME_BLOCK_SUSPICIOUS
        or bundle_score >= 22
    ):
        bundle_level = "medium"
    elif estimated_bundle_pct > 0 or bundle_score >= 8:
        bundle_level = "low"
    else:
        bundle_level = "clean"

    bundled = bundle_level in ("high", "critical") or (
        estimated_bundle_pct >= BUNDLE_PCT_HIGH
    ) or insider_detected or bool(insider_wallets)

    # --- Sniper score (single/multi large bags, flash) ---
    sniper_flags: list[str] = []
    sniper_score = 0
    if flash_holders_hit:
        sniper_score += 35
        sniper_flags.append(flash_holders_note or "flash holder velocity")

    if max_wallet_pct >= MAX_SNIPER_WALLET_PCT:
        sniper_score += 40
        sniper_flags.append(
            f"Largest wallet {max_wallet_pct:.1f}% ≥ {MAX_SNIPER_WALLET_PCT:.0f}% sniper bag"
        )
    elif max_wallet_pct >= MAX_SNIPER_WALLET_PCT * 0.7:
        sniper_score += 22
        sniper_flags.append(f"Large early bag {max_wallet_pct:.1f}%")

    if len(large_early) >= 2:
        sniper_score += 25
        sniper_flags.append(f"{len(large_early)} wallets ≥10% — multi-sniper / bundle")
    elif len(large_early) == 1:
        sniper_score += 12
        sniper_flags.append(f"Single large early bag {large_early[0]['pct']:.1f}%")

    if creator_pct > MAX_DEV_HOLD_PCT:
        sniper_score += 18
        sniper_flags.append(f"Dev holds {creator_pct:.1f}%")

    if age is not None and 0 < age < 2.0 and mcap >= 12_000:
        sniper_score += 22
        sniper_flags.append(f"Flash ${mcap:,.0f} in {age:.1f}m — sniper-pumped open")
    if total_holders > 0 and total_holders < 18 and mcap >= 8_000:
        sniper_score += 12
        sniper_flags.append(f"Only {total_holders} holders — sniper-dominated")

    if insider_wallets or insider_detected:
        sniper_score += 25
        sniper_flags.append("Insider book = controlled early buyers")

    for rf in risk_flags:
        if "sniper" in rf.lower() or "snipe" in rf.lower():
            sniper_score += 20
            sniper_flags.append(rf)

    sniper_score = min(100, sniper_score)
    if sniper_score >= 70 or (insider_wallets and max_wallet_pct >= 15):
        sniper_level = "critical"
    elif sniper_score >= 45 or max_wallet_pct > MAX_SNIPER_WALLET_PCT:
        sniper_level = "high"
    elif sniper_score >= 28:
        sniper_level = "medium"
    elif sniper_score >= 12:
        sniper_level = "low"
    else:
        sniper_level = "clean"

    if bundle_level == "critical" or sniper_level == "critical":
        overall = "critical"
    elif bundle_level == "high" or sniper_level == "high":
        overall = "high"
    elif bundle_level == "medium" or sniper_level == "medium":
        overall = "medium"
    elif bundle_level == "low" or sniper_level == "low":
        overall = "low"
    else:
        overall = "clean"

    # Hard reject: high/critical, ≥12% with red flags, or ≥20% hard-skip zone
    hard_reject = (
        overall in ("critical", "high")
        or estimated_bundle_pct >= BUNDLE_PCT_HARD_SKIP
        or (estimated_bundle_pct >= BUNDLE_PCT_HIGH and len(red_flags) >= 1)
        or len(red_flags) >= 2
        or (bundled and bundle_score >= 48)
    )

    # Decision framework label
    if estimated_bundle_pct < BUNDLE_PCT_NOISE and overall in ("clean", "low"):
        decision = "acceptable"
    elif estimated_bundle_pct < BUNDLE_PCT_HIGH and overall in ("low", "medium"):
        decision = "caution_small_size"
    elif estimated_bundle_pct >= BUNDLE_PCT_HARD_SKIP or overall == "critical":
        decision = "hard_skip"
    else:
        decision = "usually_skip"

    summary_parts: list[str] = []
    if overall == "clean" and estimated_bundle_pct < BUNDLE_PCT_NOISE:
        summary_parts.append(
            "Clean open: bundled <5% + organic-looking holders"
        )
    else:
        summary_parts.append(f"Bundled ~{estimated_bundle_pct:.0f}%")
        summary_parts.append(f"bundle {bundle_level}")
        if same_block_wallets:
            summary_parts.append(f"{same_block_wallets} early wallets")
        if sniper_level not in ("clean",):
            summary_parts.append(f"sniper {sniper_level}")
        if red_flags:
            summary_parts.append(f"flags {len(red_flags)}/4")
        if bundle_flags:
            summary_parts.append(bundle_flags[0][:60])

    return {
        "overall": overall,
        "hard_reject": hard_reject,
        "decision": decision,
        "summary": " · ".join(summary_parts)[:200],
        "bundled_pct": estimated_bundle_pct,
        "bundled_pct_reported": reported_bundle_pct,
        "same_block_wallets": same_block_wallets,
        "early_concentration_pct": round(early_conc, 1),
        "fresh_wallet_ratio_proxy": round(fresh_proxy, 2),
        "red_flags": red_flags,
        "red_flag_count": len(red_flags),
        "patterns": list(dict.fromkeys(patterns))[:6],
        "bundle": {
            "bundled": bundled,
            "score": bundle_score,
            "risk_level": (
                bundle_level
                if bundle_level != "clean"
                else ("critical" if bundled else "low")
            ),
            "flags": list(dict.fromkeys(bundle_flags))[:8],
            "cluster": cluster,
            "top5_pct": round(top5, 1),
            "top10_pct": round(top10, 1),
            "bundled_pct": estimated_bundle_pct,
            "same_block_wallets": same_block_wallets,
            "early_concentration_pct": round(early_conc, 1),
            "red_flags": red_flags,
            "decision": decision,
            "patterns": list(dict.fromkeys(patterns))[:5],
            "thresholds": {
                "bundled_pct": {
                    "noise_lt": BUNDLE_PCT_NOISE,
                    "medium": "5–12%",
                    "high": BUNDLE_PCT_HIGH,
                    "hard_skip": BUNDLE_PCT_HARD_SKIP,
                    "critical": BUNDLE_PCT_CRITICAL,
                },
                "same_block_wallets": {
                    "ok": SAME_BLOCK_OK,
                    "suspicious": SAME_BLOCK_SUSPICIOUS,
                    "bundle": SAME_BLOCK_BUNDLE,
                    "critical": SAME_BLOCK_CRITICAL,
                },
                "early_blocks_pct": {
                    "noise_lt": EARLY_CONC_NOISE,
                    "medium": EARLY_CONC_MEDIUM,
                    "high": EARLY_CONC_HIGH,
                },
            },
        },
        "snipers": {
            "risk_level": sniper_level if sniper_level != "clean" else "low",
            "score": sniper_score,
            "insider_count": len(insider_wallets),
            "insider_wallets": [
                {"pct": h["pct"], "owner": h.get("short") or h.get("owner", "")[:12]}
                for h in insider_wallets[:5]
            ],
            "max_wallet_pct": round(max_wallet_pct, 2),
            "large_early_wallets": [
                {"pct": h["pct"], "owner": h.get("short") or h.get("owner", "")[:12]}
                for h in large_early[:5]
            ],
            "insider_networks": insider_networks,
            "creator_pct": round(creator_pct, 2),
            "flags": sniper_flags[:6],
            "total_holders": total_holders,
        },
        "flags": list(dict.fromkeys(bundle_flags + sniper_flags))[:8],
    }


def to_legacy_snipers(report: dict[str, Any]) -> dict[str, Any]:
    s = report.get("snipers") or {}
    return {
        "insider_wallets": s.get("insider_wallets") or [],
        "insider_count": s.get("insider_count") or 0,
        "max_wallet_pct": s.get("max_wallet_pct") or 0,
        "large_early_wallets": s.get("large_early_wallets") or [],
        "risk_level": s.get("risk_level") or "low",
        "insider_networks": s.get("insider_networks") or 0,
        "score": s.get("score") or 0,
        "flags": s.get("flags") or [],
        "creator_pct": s.get("creator_pct") or 0,
        "total_holders": s.get("total_holders") or 0,
        "bundled_pct": report.get("bundled_pct"),
    }


def to_legacy_bundle(report: dict[str, Any]) -> dict[str, Any]:
    b = report.get("bundle") or {}
    return {
        "bundled": bool(b.get("bundled")),
        "flags": b.get("flags") or [],
        "risk_level": b.get("risk_level") or "low",
        "score": b.get("score") or 0,
        "top5_pct": b.get("top5_pct"),
        "top10_pct": b.get("top10_pct"),
        "cluster": b.get("cluster") or {},
        "bundled_pct": b.get("bundled_pct") if b.get("bundled_pct") is not None else report.get("bundled_pct"),
        "patterns": b.get("patterns") or report.get("patterns") or [],
    }
