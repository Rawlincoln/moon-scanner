"""Deployer / creator risk — history of rugs, serial farms, migrate quality.

Uses RugCheck ``creatorTokens`` + local blocklist. Facts first, then hard gates
for money mode (same spirit as mint/freeze fail-closed).
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
    sample_rugs: list[str] = []
    if rows:
        mig_count = 0
        for ct in rows:
            if not isinstance(ct, dict):
                continue
            if _row_is_migrated(ct):
                mig_count += 1
            if _row_is_rugged(ct):
                rug_hits += 1
                m = _row_mint(ct)
                if m and len(sample_rugs) < 5:
                    sample_rugs.append(m[:12] + "…")
            else:
                mc = _f(ct.get("marketCap") or ct.get("usd_market_cap"))
                if 0 < mc < 1_500 and not _row_is_migrated(ct):
                    dead_hits += 1
        if migrated <= 0:
            migrated = mig_count

    migrate_rate = (migrated / launched) if launched > 0 else None
    creator_sold = bool(safety.get("creator_sold"))
    creator_pct = _f(safety.get("creator_pct"))

    blocked = creator in _blocked_creators() if creator else False

    flags: list[str] = []
    reasons: list[str] = []
    risk = "unknown"  # low | medium | high | critical | unknown
    hard = False

    if blocked:
        flags.append("blocked_creator")
        reasons.append("Creator wallet on BLOCKED_CREATORS list")
        risk = "critical"
        hard = True

    if launched >= 15:
        flags.append("serial_farm")
        reasons.append(f"Serial deployer — {launched} prior tokens")
        risk = "critical"
        hard = True
    elif launched >= 8:
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

    if rug_hits >= 3:
        flags.append("prior_rugs")
        reasons.append(f"≥{rug_hits} prior tokens look rugged/dead-from-ATH")
        risk = "critical"
        hard = True
    elif rug_hits >= 1:
        flags.append("prior_rug")
        reasons.append(f"{rug_hits} prior token(s) match rug/dead pattern")
        if risk not in ("critical",):
            risk = "high"
        if rug_hits >= 2 or launched >= 5:
            hard = True

    if dead_hits >= 5 and launched >= 6:
        flags.append("dead_coin_farm")
        reasons.append(f"{dead_hits} prior mints still dust mcap — spray farm")
        risk = "critical" if risk != "critical" else risk
        hard = True

    if creator_sold and creator_pct < 0.5 and launched >= 3:
        flags.append("dev_sold_multi")
        reasons.append("Dev sold bag + multi-launch history")
        if risk in ("unknown", "low"):
            risk = "high"
        if launched >= 5:
            hard = True

    if launched >= 4 and migrated == 0 and not hard:
        flags.append("zero_migrate_history")
        reasons.append(f"{launched} launches, 0 migrations")
        risk = "high" if risk == "unknown" else risk
        # Soft-hard for money: 4+ with zero migrate is enough
        if launched >= 5:
            hard = True

    if not flags and launched <= 2:
        risk = "low"
        reasons.append(
            f"Light history ({launched} prior)" if launched else "No prior creator tokens in sample"
        )
    elif not flags and launched > 2 and migrate_rate and migrate_rate >= 0.25:
        risk = "medium"
        reasons.append(
            f"Some history: {migrated}/{launched} migrated ({migrate_rate * 100:.0f}%)"
        )

    return {
        "creator": creator,
        "creator_short": (creator[:8] + "…" + creator[-4:]) if len(creator) > 14 else creator,
        "tokens_launched": launched,
        "tokens_migrated": migrated,
        "migrate_rate": round(migrate_rate, 3) if migrate_rate is not None else None,
        "prior_rugs": rug_hits,
        "dead_farms": dead_hits,
        "sample_rug_mints": sample_rugs,
        "creator_sold": creator_sold,
        "creator_pct": round(creator_pct, 2),
        "blocked_wallet": blocked,
        "flags": flags,
        "reasons": reasons,
        "risk_level": risk,
        "hard_reject": hard,
        "summary": reasons[0] if reasons else "Dev history unknown / light",
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
    rate = dev.get("migrate_rate")
    rate_s = f"{rate * 100:.0f}%" if rate is not None else "n/a"
    risk = dev.get("risk_level") or "unknown"
    sold = "SOLD" if dev.get("creator_sold") else "holding"
    who = dev.get("creator_short") or "?"
    return (
        f"\n👷 <b>DEV</b> {who} · risk <b>{risk}</b>\n"
        f"launched {launched} · migrated {migrated} ({rate_s}) · "
        f"prior_rugs {rugs} · bag {sold}"
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
    return dev
