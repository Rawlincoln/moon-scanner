"""Pro trencher gate — only pass tokens approaching $6k with real momentum.

Root causes of prior false positives (fixed here):
  1. Recommended ~$2k launches (rug window) instead of ~$6k climb
  2. Synthetic pump.fun volume/txns inflated confidence without real trades
  3. pump.fun + Padre NEW counted as 2 sources (same feed)
  4. Zero community (0 replies) still scored STRONG_INVEST
  5. Sniper/insider wallets not checked
  6. No minimum survival age — instant rugs passed through
"""

from __future__ import annotations

import time
from typing import Any

from config import (
    MAX_DEV_HOLD_PCT,
    MAX_SNIPER_WALLET_PCT,
    MCAP_INVEST_MAX_USD,
    MCAP_INVEST_MIN_USD,
    MIN_BUY_SELL_RATIO_M5,
    MIN_DEX_BUYS_M5,
    MIN_DEX_VOL_M5_USD,
    MIN_PRICE_CHANGE_M5,
    MIN_PUMPFUN_REPLIES,
    MIN_SURVIVAL_AGE_MINUTES,
    MIN_TOKEN_HOLDERS,
    SIXK_RADAR_MAX_USD,
    SIXK_RADAR_MIN_USD,
    TARGET_MCAP_USD,
)
from services.bundle_sniper import analyze_bundle_and_snipers, to_legacy_snipers
from services.pumpfun import PumpFunClient


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _is_real_dex_pair(pair: dict) -> bool:
    return not bool(pair.get("is_pumpfun_synthetic"))


def _mcap_distance_score(mcap: float) -> float:
    """100 = exactly at target, decays as we move away from $6k."""
    if mcap <= 0:
        return 0.0
    dist = abs(mcap - TARGET_MCAP_USD) / TARGET_MCAP_USD
    return max(0.0, 100.0 - dist * 100.0)


def analyze_snipers(
    safety: dict,
    pump: dict | None = None,
    pair: dict | None = None,
) -> dict[str, Any]:
    """Multi-signal sniper/bundle analysis (holders + insiders + flow + flash)."""
    report = analyze_bundle_and_snipers(safety, pump, pair or {})
    out = to_legacy_snipers(report)
    out["bundle"] = report.get("bundle")
    out["overall"] = report.get("overall")
    out["hard_reject"] = report.get("hard_reject")
    out["summary"] = report.get("summary")
    return out


def analyze_community(pump: dict | None, pair: dict) -> dict[str, Any]:
    pump = pump or {}
    txns = pair.get("txns") or {}
    m5 = txns.get("m5") or {}
    buys_m5 = int(m5.get("buys") or 0)
    sells_m5 = int(m5.get("sells") or 0)

    replies = int(pump.get("reply_count") or 0)
    has_social = bool(
        pump.get("twitter") or pump.get("telegram") or pump.get("website")
    )

    organic_score = 0
    if replies >= MIN_PUMPFUN_REPLIES:
        organic_score += 40
    if buys_m5 >= MIN_DEX_BUYS_M5 * 2:
        organic_score += 35
    elif buys_m5 >= MIN_DEX_BUYS_M5:
        organic_score += 20
    if has_social:
        organic_score += 15
    if buys_m5 > sells_m5:
        organic_score += 10

    return {
        "reply_count": replies,
        "has_social": has_social,
        "buys_m5": buys_m5,
        "sells_m5": sells_m5,
        "organic_score": min(100, organic_score),
        "active": replies >= MIN_PUMPFUN_REPLIES or buys_m5 >= MIN_DEX_BUYS_M5,
    }


def run_trench_gate(
    safety: dict,
    pair: dict,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Returns passed=True only for pro-grade ~$6k climbing setups."""
    pump = pair.get("pumpfun") or {}
    sources = sources or []

    mcap = _safe_float(pump.get("usd_market_cap") or pair.get("marketCap"))
    age_min = None
    created = pair.get("pairCreatedAt") or pump.get("created_timestamp")
    if created:
        age_min = (time.time() * 1000 - created) / 60_000

    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    m5_txns = txns.get("m5") or {}

    vol_m5 = _safe_float(volume.get("m5"))
    pc_m5 = _safe_float(changes.get("m5"))
    buys_m5 = int(m5_txns.get("buys") or 0)
    sells_m5 = int(m5_txns.get("sells") or 0)
    buy_ratio_m5 = buys_m5 / max(sells_m5, 1)

    snipers = analyze_snipers(safety, pump)
    community = analyze_community(pump, pair)

    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    def add(name: str, passed: bool, detail: str, critical: bool = True):
        checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed and critical:
            failures.append(f"{name}: {detail}")

    # --- Hard gates ---
    add(
        "real_dex_data",
        _is_real_dex_pair(pair),
        "Need DexScreener trade data — synthetic pump.fun stats ignored",
    )

    add(
        "mcap_approaching_6k",
        MCAP_INVEST_MIN_USD <= mcap <= MCAP_INVEST_MAX_USD,
        f"MCap ${mcap:,.0f} — want ${MCAP_INVEST_MIN_USD:,.0f}–${MCAP_INVEST_MAX_USD:,.0f} "
        f"(approaching ${TARGET_MCAP_USD:,.0f})",
    )

    add(
        "mcap_going_up",
        pc_m5 >= MIN_PRICE_CHANGE_M5,
        f"5m price {pc_m5:+.1f}% — must be ≥ {MIN_PRICE_CHANGE_M5:+.0f}% (climbing)",
    )

    if age_min is not None:
        add(
            "survived_rug_window",
            age_min >= MIN_SURVIVAL_AGE_MINUTES,
            f"Age {age_min:.1f}m — need ≥ {MIN_SURVIVAL_AGE_MINUTES:.0f}m to survive snipers",
        )

    add(
        "real_volume",
        vol_m5 >= MIN_DEX_VOL_M5_USD,
        f"5m vol ${vol_m5:,.0f} — need ≥ ${MIN_DEX_VOL_M5_USD:,.0f}",
    )

    add(
        "buyers_active",
        buys_m5 >= MIN_DEX_BUYS_M5,
        f"{buys_m5} buys in 5m — need ≥ {MIN_DEX_BUYS_M5}",
    )

    add(
        "buy_pressure",
        buy_ratio_m5 >= MIN_BUY_SELL_RATIO_M5,
        f"Buy/sell ratio {buy_ratio_m5:.2f}x — need ≥ {MIN_BUY_SELL_RATIO_M5:.2f}x",
    )

    holders = int(safety.get("total_holders") or 0)
    add(
        "holder_distribution",
        holders >= MIN_TOKEN_HOLDERS,
        f"{holders} holders — need ≥ {MIN_TOKEN_HOLDERS} (not just snipers)",
    )

    add(
        "no_insider_snipers",
        snipers["risk_level"] not in ("critical", "high")
        and snipers["insider_count"] == 0
        and not snipers.get("hard_reject"),
        snipers.get("summary")
        or (
            "Insider/sniper wallets detected"
            if snipers["insider_count"] or snipers["risk_level"] != "low"
            else "Clean sniper book"
        ),
    )

    add(
        "no_whale_sniper",
        snipers["max_wallet_pct"] <= MAX_SNIPER_WALLET_PCT,
        f"Largest wallet {snipers['max_wallet_pct']:.1f}% — max {MAX_SNIPER_WALLET_PCT:.0f}%",
    )

    bundle = snipers.get("bundle") or {}
    add(
        "not_bundled",
        not bundle.get("bundled") and bundle.get("risk_level") not in ("critical", "high"),
        (
            (bundle.get("flags") or ["Bundled multi-wallet launch"])[0]
            if bundle.get("bundled")
            else "No bundle cluster"
        ),
    )

    dev_pct = _safe_float(safety.get("creator_pct"))
    add(
        "dev_not_bagging",
        dev_pct <= MAX_DEV_HOLD_PCT,
        f"Dev holds {dev_pct:.1f}% — max {MAX_DEV_HOLD_PCT:.0f}%",
    )

    add(
        "no_mint_authority",
        not safety.get("mint_authority"),
        "Mint authority active — dev can mint more",
    )

    add(
        "community_or_organic",
        community["active"],
        f"Need ≥{MIN_PUMPFUN_REPLIES} replies OR ≥{MIN_DEX_BUYS_M5} buys in 5m "
        f"(has {community['reply_count']} replies, {buys_m5} buys)",
    )

    add(
        "safety_pass",
        safety.get("passed", False),
        "RugCheck / Padre safety failed" if not safety.get("passed") else "Passed",
    )

    if safety.get("rugged"):
        add("not_rugged", False, "Token flagged as rugged on RugCheck")

    if pump.get("complete"):
        add("on_bonding_curve", False, "Already graduated — too late for 6k entry")

    # Real multi-source (not duplicate pump feeds)
    real_sources = set(sources)
    real_sources.discard("padre_trenches_new")  # same as pump.fun latest
    has_real_overlap = len(real_sources) >= 2 or "padre_trending" in sources
    add(
        "multi_source",
        has_real_overlap,
        "Confirmed on trending/new-pairs feed (not just raw launch)",
        critical=False,
    )

    passed = len(failures) == 0
    score = sum(1 for c in checks if c["passed"])
    total = len(checks)

    mcap_score = _mcap_distance_score(mcap)
    trench_score = round(
        (score / max(total, 1)) * 50
        + mcap_score * 0.25
        + community["organic_score"] * 0.15
        + (20 if pc_m5 > 15 else 10 if pc_m5 > 5 else 0)
        + (0 if snipers["risk_level"] == "low" else -20),
        1,
    )

    return {
        "passed": passed,
        "trench_score": trench_score,
        "mcap_usd": round(mcap, 2),
        "mcap_target": TARGET_MCAP_USD,
        "mcap_distance_pct": round(
            abs(mcap - TARGET_MCAP_USD) / TARGET_MCAP_USD * 100, 1
        ),
        "age_minutes": round(age_min, 1) if age_min is not None else None,
        "price_change_m5": round(pc_m5, 2),
        "volume_m5": round(vol_m5, 2),
        "checks": checks,
        "failures": failures,
        "snipers": snipers,
        "community": community,
        "has_real_dex": _is_real_dex_pair(pair),
        "verdict": (
            "APPROVED — approaching $6k with real buyers"
            if passed
            else "REJECTED — " + (failures[0] if failures else "failed trench gate")
        ),
    }


def is_approaching_6k_candidate(pump: dict | None) -> bool:
    """Fast pre-filter — wide $2k–$9k radar so we catch before $6k is gone."""
    if not pump or pump.get("complete") or pump.get("is_banned"):
        return False
    mcap = _safe_float(pump.get("usd_market_cap"))
    if mcap < SIXK_RADAR_MIN_USD or mcap > SIXK_RADAR_MAX_USD:
        return False
    age = PumpFunClient.coin_age_minutes(pump)
    return age >= MIN_SURVIVAL_AGE_MINUTES