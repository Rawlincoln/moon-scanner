"""Deployer / creator risk AND quality — rugs vs proven migrators/moons.

Uses RugCheck ``creatorTokens`` + local blocklist.
  - Hard gates: serial farms, prior rugs (money mode)
  - Soft boost: high migrate rate + prior moon-sized ATHs
"""

from __future__ import annotations

from typing import Any

# Known serial rug deployers (extend via BLOCKED_CREATORS env or code).
# These are wallet addresses, not mints.
DEFAULT_BLOCKED_CREATORS: frozenset[str] = frozenset(
    {
        # Add confirmed farm wallets here as you catch them
    }
)


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _i(x: Any, d: int = 0) -> int:
    try:
        return int(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _blocked_creators() -> set[str]:
    import os

    out = set(DEFAULT_BLOCKED_CREATORS)
    raw = (os.getenv("BLOCKED_CREATORS") or "").strip()
    for part in raw.split(","):
        p = part.strip()
        if p:
            out.add(p)
    return out


def _row_mint(ct: dict[str, Any]) -> str:
    return str(
        ct.get("mint")
        or ct.get("token")
        or ct.get("address")
        or ct.get("tokenAddress")
        or ""
    ).strip()


def _row_is_migrated(ct: dict[str, Any]) -> bool:
    if (
        ct.get("migrated")
        or ct.get("complete")
        or ct.get("raydiumPool")
        or ct.get("raydium")
        or ct.get("graduated")
        or str(ct.get("status") or "").lower()
        in ("migrated", "graduated", "complete")
    ):
        return True
    mc = _f(ct.get("marketCap") or ct.get("usd_market_cap") or ct.get("ath_market_cap"))
    return mc >= 50_000


def _row_peak_mcap(ct: dict[str, Any]) -> float:
    """Best available peak mcap for a prior creator mint."""
    return max(
        _f(ct.get("ath_market_cap") or ct.get("athMarketCap") or ct.get("peak")),
        _f(ct.get("marketCap") or ct.get("usd_market_cap") or ct.get("mcap")),
    )


def _row_is_moon(ct: dict[str, Any], *, min_ath: float = 100_000.0) -> bool:
    """Prior token ran to a meaningful moon band (migrated or high ATH)."""
    if _row_is_rugged(ct):
        return False
    peak = _row_peak_mcap(ct)
    if peak >= min_ath:
        return True
    if _row_is_migrated(ct) and peak >= 60_000:
        return True
    return False


def _row_is_rugged(ct: dict[str, Any]) -> bool:
    """Heuristic: prior mint looks like a rug / dead farm coin."""
    if ct.get("rugged") or ct.get("is_rugged") or ct.get("honeypot"):
        return True
    status = str(ct.get("status") or ct.get("risk") or "").lower()
    if any(k in status for k in ("rug", "scam", "honeypot", "danger")):
        return True
    risks = ct.get("risks") or ct.get("flags") or []
    if isinstance(risks, list):
        joined = " ".join(str(r).lower() for r in risks)
        if "rug" in joined or "honeypot" in joined or "scam" in joined:
            return True
    # Dead low mcap after having some activity = likely dump farm
    mc = _f(ct.get("marketCap") or ct.get("usd_market_cap"))
    ath = _f(ct.get("ath_market_cap") or ct.get("athMarketCap") or ct.get("peak"))
    holders = _i(ct.get("holders") or ct.get("totalHolders") or ct.get("holder"))
    if ath >= 8_000 and mc > 0 and mc < ath * 0.15 and not _row_is_migrated(ct):
        return True
    if ath >= 15_000 and mc < 2_000 and not _row_is_migrated(ct):
        return True
    if holders and holders < 30 and ath >= 5_000 and mc < ath * 0.2:
        return True
    return False


def analyze_creator_history(
    safety: dict[str, Any] | None,
    *,
    pump: dict[str, Any] | None = None,
    creator_override: str | None = None,
) -> dict[str, Any]:
    """Score deployer history for serial farms and prior rugs."""
    safety = safety or {}
    pump = pump or {}
    creator = str(
        creator_override
        or safety.get("creator")
        or pump.get("creator")
        or ""
    ).strip()

    rows = safety.get("creator_tokens") or []
    if not isinstance(rows, list):
        rows = []
    launched = _i(safety.get("creator_token_count"))
    if launched <= 0:
        launched = len(rows)

    migrated = _i(safety.get("creator_migrated_count"))
    rug_hits = 0
    dead_hits = 0
    moon_hits = 0  # ATH/peak ≥ $100k clean runs
    mega_hits = 0  # ≥ $500k
    sample_rugs: list[str] = []
    sample_moons: list[str] = []
    best_prior_ath = 0.0
    if rows:
        mig_count = 0
        for ct in rows:
            if not isinstance(ct, dict):
                continue
            if _row_is_migrated(ct):
                mig_count += 1
            peak = _row_peak_mcap(ct)
            best_prior_ath = max(best_prior_ath, peak)
            if _row_is_rugged(ct):
                rug_hits += 1
                m = _row_mint(ct)
                if m and len(sample_rugs) < 5:
                    sample_rugs.append(m[:12] + "…")
            else:
                mc = _f(ct.get("marketCap") or ct.get("usd_market_cap"))
                if 0 < mc < 1_500 and not _row_is_migrated(ct):
                    dead_hits += 1
                if _row_is_moon(ct, min_ath=100_000):
                    moon_hits += 1
                    m = _row_mint(ct)
                    if m and len(sample_moons) < 5:
                        sample_moons.append(m[:12] + "…")
                if peak >= 500_000 and not _row_is_rugged(ct):
                    mega_hits += 1
        if migrated <= 0:
            migrated = mig_count

    migrate_rate = (migrated / launched) if launched > 0 else None
    creator_sold = bool(safety.get("creator_sold"))
    creator_pct = _f(safety.get("creator_pct"))

    blocked = creator in _blocked_creators() if creator else False

    flags: list[str] = []
    reasons: list[str] = []
    quality_flags: list[str] = []
    quality_reasons: list[str] = []
    risk = "unknown"  # low | medium | high | critical | unknown
    hard = False
    quality_score = 0  # 0–100 positive track record
    score_boost = 0  # applied to moon ranking
    priority_boost = 0.0  # discovery priority

    if blocked:
        flags.append("blocked_creator")
        reasons.append("Creator wallet on BLOCKED_CREATORS list")
        risk = "critical"
        hard = True

    # Proven good track record (look for this) — only if not a farm/rugger
    # Needs enough sample to avoid "1 lucky coin" bias
    proven = False
    if not hard and rug_hits == 0:
        if migrated >= 2 and migrate_rate is not None and migrate_rate >= 0.35:
            proven = True
            quality_flags.append("proven_migrator")
            quality_reasons.append(
                f"Proven migrator: {migrated}/{launched} migrated ({migrate_rate * 100:.0f}%)"
            )
            quality_score += 35
            score_boost += 6
            priority_boost += 14
        if moon_hits >= 1:
            quality_flags.append("prior_moons")
            quality_reasons.append(
                f"{moon_hits} prior moon-class run(s) (ATH ≥$100k)"
            )
            quality_score += 20 + min(20, moon_hits * 8)
            score_boost += 5 + min(8, moon_hits * 3)
            priority_boost += 10 + min(12, moon_hits * 4)
            proven = True
        if mega_hits >= 1:
            quality_flags.append("prior_mega")
            quality_reasons.append(f"{mega_hits} prior mega (ATH ≥$500k)")
            quality_score += 20
            score_boost += 6
            priority_boost += 12
            proven = True
        if (
            migrated >= 3
            and migrate_rate is not None
            and migrate_rate >= 0.5
            and moon_hits >= 1
        ):
            quality_flags.append("elite_dev")
            quality_reasons.append(
                f"Elite track: {migrate_rate * 100:.0f}% migrate + {moon_hits} moons"
            )
            quality_score += 15
            score_boost += 5
            priority_boost += 10
        if proven and launched >= 2 and migrate_rate and migrate_rate >= 0.25:
            quality_score = min(100, quality_score + 5)

    # Cap boosts
    score_boost = min(18, score_boost)
    priority_boost = min(40.0, priority_boost)
    quality_score = min(100, quality_score)

    if launched >= 15 and not proven:
        flags.append("serial_farm")
        reasons.append(f"Serial deployer — {launched} prior tokens")
        risk = "critical"
        hard = True
    elif launched >= 8 and not proven:
        flags.append("serial_creator")
        reasons.append(f"Creator launched {launched} tokens")
        if migrated == 0:
            flags.append("serial_zero_migrate")
            reasons.append(f"{launched} launches with 0 migrations — farm pattern")
            risk = "critical"
            hard = True
        else:
            risk = "high" if risk not in ("critical",) else risk
            if migrate_rate is not None and migrate_rate < 0.1:
                hard = True
                flags.append("low_migrate_rate")
                reasons.append(
                    f"Migrate rate {migrate_rate * 100:.0f}% ({migrated}/{launched}) — mostly fails"
                )
    elif launched >= 15 and proven:
        # High volume but proven — watch, don't auto-critical
        flags.append("high_volume_dev")
        reasons.append(f"High volume deployer ({launched}) but has proven runs")
        if risk not in ("critical", "high"):
            risk = "medium"

    if rug_hits >= 3:
        flags.append("prior_rugs")
        reasons.append(f"≥{rug_hits} prior tokens look rugged/dead-from-ATH")
        risk = "critical"
        hard = True
        proven = False
        score_boost = 0
        priority_boost = 0
        quality_score = 0
    elif rug_hits >= 1:
        flags.append("prior_rug")
        reasons.append(f"{rug_hits} prior token(s) match rug/dead pattern")
        if risk not in ("critical",):
            risk = "high"
        if rug_hits >= 2 or launched >= 5:
            hard = True
        # Don't boost if any prior rug
        score_boost = 0
        priority_boost = min(priority_boost, 0)
        proven = False
        quality_score = min(quality_score, 15)

    if dead_hits >= 5 and launched >= 6 and not proven:
        flags.append("dead_coin_farm")
        reasons.append(f"{dead_hits} prior mints still dust mcap — spray farm")
        risk = "critical" if risk != "critical" else risk
        hard = True

    if creator_sold and creator_pct < 0.5 and launched >= 3 and not proven:
        flags.append("dev_sold_multi")
        reasons.append("Dev sold bag + multi-launch history")
        if risk in ("unknown", "low"):
            risk = "high"
        if launched >= 5:
            hard = True

    if launched >= 4 and migrated == 0 and not hard and not proven:
        flags.append("zero_migrate_history")
        reasons.append(f"{launched} launches, 0 migrations")
        risk = "high" if risk == "unknown" else risk
        if launched >= 5:
            hard = True

    if not flags and not quality_flags and launched <= 2:
        risk = "low"
        reasons.append(
            f"Light history ({launched} prior)" if launched else "No prior creator tokens in sample"
        )
    elif not flags and launched > 2 and migrate_rate and migrate_rate >= 0.25:
        if risk == "unknown":
            risk = "medium" if not proven else "low"
        if not quality_reasons:
            reasons.append(
                f"Some history: {migrated}/{launched} migrated ({migrate_rate * 100:.0f}%)"
            )

    if proven and risk in ("unknown", "medium"):
        risk = "low"

    summary = (
        (quality_reasons[0] if quality_reasons else None)
        or (reasons[0] if reasons else None)
        or "Dev history unknown / light"
    )

    return {
        "creator": creator,
        "creator_short": (creator[:8] + "…" + creator[-4:]) if len(creator) > 14 else creator,
        "tokens_launched": launched,
        "tokens_migrated": migrated,
        "migrate_rate": round(migrate_rate, 3) if migrate_rate is not None else None,
        "prior_rugs": rug_hits,
        "prior_moons": moon_hits,
        "prior_megas": mega_hits,
        "best_prior_ath": round(best_prior_ath, 0) if best_prior_ath else 0,
        "dead_farms": dead_hits,
        "sample_rug_mints": sample_rugs,
        "sample_moon_mints": sample_moons,
        "creator_sold": creator_sold,
        "creator_pct": round(creator_pct, 2),
        "blocked_wallet": blocked,
        "flags": flags,
        "quality_flags": quality_flags,
        "reasons": reasons,
        "quality_reasons": quality_reasons,
        "risk_level": risk,
        "hard_reject": hard,
        "proven_dev": proven and not hard,
        "quality_score": quality_score,
        "score_boost": score_boost if not hard else 0,
        "priority_boost": priority_boost if not hard else 0.0,
        "summary": summary,
    }


def dev_risk_gate(dev: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Money-mode fail-closed on high-risk deployers."""
    if not isinstance(dev, dict):
        return True, None  # unknown — don't block solely on missing (enrich may lag)
    if dev.get("hard_reject") or dev.get("risk_level") == "critical":
        return False, dev.get("summary") or "dev risk critical"
    if dev.get("blocked_wallet"):
        return False, "blocked creator wallet"
    if int(dev.get("prior_rugs") or 0) >= 2:
        return False, f"prior rugs x{dev.get('prior_rugs')}"
    if int(dev.get("tokens_launched") or 0) >= 8 and int(dev.get("tokens_migrated") or 0) == 0:
        return False, "serial deployer 0 migrations"
    return True, None


def format_dev_telegram(dev: dict[str, Any] | None) -> str:
    if not isinstance(dev, dict):
        return ""
    launched = dev.get("tokens_launched") or 0
    migrated = dev.get("tokens_migrated") or 0
    rugs = dev.get("prior_rugs") or 0
    moons = dev.get("prior_moons") or 0
    rate = dev.get("migrate_rate")
    rate_s = f"{rate * 100:.0f}%" if rate is not None else "n/a"
    risk = dev.get("risk_level") or "unknown"
    sold = "SOLD" if dev.get("creator_sold") else "holding"
    who = dev.get("creator_short") or "?"
    badge = ""
    if dev.get("proven_dev"):
        badge = " · ⭐ <b>PROVEN</b>"
        if "elite_dev" in (dev.get("quality_flags") or []):
            badge = " · ⭐ <b>ELITE</b>"
    moon_bit = f" · prior_moons {moons}" if moons else ""
    return (
        f"\n👷 <b>DEV</b> {who} · risk <b>{risk}</b>{badge}\n"
        f"launched {launched} · migrated {migrated} ({rate_s})"
        f"{moon_bit} · prior_rugs {rugs} · bag {sold}"
    )


def attach_dev_risk(token: dict[str, Any]) -> dict[str, Any]:
    """Compute and attach ``devRisk`` on a feed card / analyze result."""
    safety = token.get("safety") or {}
    pf = token.get("pumpfun") or {}
    dev = analyze_creator_history(safety, pump=pf)
    token["devRisk"] = dev
    # Mirror onto safety for avoid_filters consumers
    if isinstance(token.get("safety"), dict):
        token["safety"]["dev_risk"] = dev
        token["safety"]["creator_prior_rugs"] = dev.get("prior_rugs")
        token["safety"]["creator_prior_moons"] = dev.get("prior_moons")
        token["safety"]["proven_dev"] = dev.get("proven_dev")
    return dev


def dev_score_boost(token: dict[str, Any]) -> int:
    """Soft points for moon composite (0 if hard risk)."""
    dev = token.get("devRisk")
    if not isinstance(dev, dict):
        try:
            dev = attach_dev_risk(token)
        except Exception:
            return 0
    if dev.get("hard_reject"):
        return 0
    return int(dev.get("score_boost") or 0)


def dev_priority_boost(token: dict[str, Any]) -> float:
    dev = token.get("devRisk")
    if not isinstance(dev, dict):
        return 0.0
    if dev.get("hard_reject"):
        return 0.0
    try:
        return float(dev.get("priority_boost") or 0)
    except (TypeError, ValueError):
        return 0.0
