"""Moon Picks v3 — capital-protection + migration path.

After user losses + pre-$7k dump feedback: almost never recommend lottery.
Show only when:
  1. Not dumped / not rug / not ghost
  2. Past survival floor (~$7k) — charts that die before $7k are not picks
  3. Near ATH (still climbing) + structure toward migration
  4. Real narrative edge OR organic climb path (holders + two-way + bond)
  5. Multi-pillar score + confidence agree

Modes (MOON_MODE env):
  strict   — ultra-tight post-loss gates (−12% ATH, hard community bar)
  balanced — default; organic climb path for mid-curve migrators (still not Heat)

Random near-ATH sub-$7k charts without structure = REJECT.
"""

from __future__ import annotations

from typing import Any

from config import (
    DUMP_HIDE_FRAC,
    GRADUATION_MCAP_USD,
    MIGRATION_MCAP_MAX_USD,
    MIGRATION_NEAR_MIN_PCT,
    MIN_SURVIVAL_AGE_MINUTES,
    MONEY_ENTRY_MIN_USD,
    MOON_MODE,
    SURVIVAL_MCAP_USD,
)
from services.accuracy import holders_known, learning_soft_adjust, merge_ath_into_token
from services.avoid_filters import BLOCKED_MINTS, is_hard_avoid
from services.bundle_sniper import analyze_bundle_and_snipers
from services.runner_radar import extract_ath_mcap, extract_mcap_usd, is_crashed_runner
from services.social_signals import analyze_social_narrative
from services.tx_activity import score_tx_activity

# Discovery floor — still above pure dust; money labels use SURVIVAL_MCAP_USD
MIN_MCAP = max(5_500.0, float(SURVIVAL_MCAP_USD) * 0.78)
MAX_MCAP = MIGRATION_MCAP_MAX_USD
# Recommend / MOON-WATCH money path: must live at/above survival (~$7k)
RECOMMEND_MIN_MCAP = float(MONEY_ENTRY_MIN_USD or SURVIVAL_MCAP_USD or 7_000)

# ATH floors depend on mode (balanced recovers slight-dip organic climbers)
if MOON_MODE == "strict":
    NEAR_ATH_FRAC = 0.88  # within −12% of ATH
    NEAR_ATH_FRAC_STRONG = 0.85  # −15% with holders + edge
    FADE_ATH_FRAC = 0.93
else:
    # balanced: tighter under survival/climb so local $5–6k tops don't pass
    NEAR_ATH_FRAC = 0.88
    NEAR_ATH_FRAC_STRONG = 0.85
    FADE_ATH_FRAC = 0.92

LABEL_MOON = "MOON"
LABEL_WATCH = "WATCH"
LABEL_WEAK = "WEAK"
LABEL_REJECT = "REJECT"


def moon_mode() -> str:
    return MOON_MODE if MOON_MODE in ("balanced", "strict") else "balanced"


def default_rank_gates() -> dict[str, Any]:
    """Base score/conf floors before adaptive outcomes layer."""
    if moon_mode() == "strict":
        return {"min_score": 58, "min_confidence": 54, "max_bundled_pct": 8.0}
    # Tighter books for money path — lottery bundles dump pre-migration
    return {"min_score": 54, "min_confidence": 52, "max_bundled_pct": 8.0}


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


def _mint(token: dict[str, Any]) -> str:
    return str(
        token.get("tokenAddress") or token.get("mint") or token.get("address") or ""
    ).strip()


def _pf(token: dict[str, Any]) -> dict[str, Any]:
    return token.get("pumpfun") or (token.get("market") or {}).get("pumpfun") or {}


def _bond(token: dict[str, Any], mcap: float = 0.0) -> float:
    bond = _f(
        token.get("bonding_progress")
        or (token.get("migrationPath") or {}).get("bonding_pct")
        or _pf(token).get("bonding_progress")
    )
    if bond <= 0 and mcap > 0:
        bond = min(100.0, (mcap / GRADUATION_MCAP_USD) * 100)
    return bond


def _ensure_social(token: dict[str, Any]) -> dict[str, Any]:
    """Attach / refresh socialSignals on token."""
    social = token.get("socialSignals")
    if isinstance(social, dict) and social.get("edge_score") is not None:
        return social
    pf = _pf(token)
    social = analyze_social_narrative(
        pump_coin=pf,
        name=token.get("name") or pf.get("name") or "",
        symbol=token.get("symbol") or pf.get("symbol") or "",
        description=pf.get("description") or token.get("description") or "",
    )
    token["socialSignals"] = social
    return social


def stage_of(token: dict[str, Any]) -> str:
    mcap = extract_mcap_usd(token)
    bond = _bond(token, mcap)
    if bond >= MIGRATION_NEAR_MIN_PCT or mcap >= 28_000:
        return "near_migration"
    if mcap >= 10_000 or bond >= 18:
        return "climb"
    return "early"


def _two_way_flow(token: dict[str, Any]) -> tuple[bool, bool, int, int]:
    """Return (two_way, one_way_wash, buys_m5, sells_m5)."""
    mkt = token.get("market") or {}
    txns = (mkt.get("txns") or {}).get("m5") or {}
    buys = _i(txns.get("buys"))
    sells = _i(txns.get("sells"))
    if buys == 0 and sells == 0:
        tx = token.get("txActivity") or {}
        buys = _i(tx.get("buys_m5") or tx.get("buys"))
        sells = _i(tx.get("sells_m5") or tx.get("sells"))
    ratio = buys / max(sells, 1)
    one_way = buys >= 15 and sells == 0
    two_way = buys >= 5 and sells >= 2 and ratio <= 3.8
    return two_way, one_way, buys, sells


def _attach_migration_path(token: dict[str, Any]) -> dict[str, Any]:
    mp = token.get("migrationPath")
    if isinstance(mp, dict) and mp.get("score") is not None:
        return mp
    try:
        from services.migration_path import analyze_migration_path

        mcap = extract_mcap_usd(token)
        mp = analyze_migration_path(
            mcap_usd=mcap,
            bonding_progress=_bond(token, mcap),
            safety=token.get("safety") or {},
            pair=token.get("market") or {},
            pump=_pf(token),
            avoid=token.get("avoid")
            or (token.get("safetyReport") or {}).get("avoid")
            or {},
            complete=bool(_pf(token).get("complete")),
        )
        token["migrationPath"] = mp
        return mp
    except Exception:
        return {}


def reject_reason(token: dict[str, Any]) -> str | None:
    """Hard reject — never show / recommend."""
    mint = _mint(token)
    if not mint:
        return "missing mint"
    if mint in BLOCKED_MINTS:
        return "blocklist"
    if token.get("skipped"):
        return str(token.get("skipReason") or token.get("skip_reason") or "skipped")

    merge_ath_into_token(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    peak = max(ath, _f(token.get("_peak_mcap") or token.get("peak_mcap")))
    age = _f(token.get("age_minutes"))

    if mcap > 0 and mcap < MIN_MCAP:
        return f"too small ${mcap:,.0f}"
    if mcap > MAX_MCAP:
        return f"too large ${mcap:,.0f}"

    # Survival floor: most recommended dumps never print $7k — do not recommend lottery
    if 0 < mcap < RECOMMEND_MIN_MCAP:
        return (
            f"below survival floor ${RECOMMEND_MIN_MCAP:,.0f} "
            f"(${mcap:,.0f}) — lottery dump zone"
        )

    # Age past sniper flash for sub-$15k; mid-curve can be younger if already large
    min_age = float(MIN_SURVIVAL_AGE_MINUTES or 4.0)
    if mcap < 15_000 and 0 < age < min_age:
        return f"too fresh {age:.1f}m under $15k — need {min_age:.0f}m+ survival"
    if 0 < age < 1.0 and mcap >= 15_000:
        return "flash launch — too young for mcap"

    # One-way wash under climb band = pre-dump packaging
    two_way, one_way, buys, sells = _two_way_flow(token)
    if one_way and mcap < 25_000:
        return "one-way wash buys — not a migration path"
    if mcap < 12_000 and buys >= 10 and sells == 0:
        return "zero sellers under $12k — wash risk"

    crashed, why = is_crashed_runner(
        token, mcap=mcap or None, ath=ath or None, peak=peak or None
    )
    if crashed:
        return why or "dumped"

    high = max(ath, peak)
    # Dump wall sits below near-ATH floors so balanced −15/−18% can still score
    dump_floor = max(DUMP_HIDE_FRAC, 0.80 if moon_mode() == "balanced" else 0.85)
    if high >= 2_500 and mcap > 0 and mcap < high * dump_floor:
        return f"dumped −{(1 - mcap / high) * 100:.0f}% from ${high:,.0f}"

    # Near-ATH gate — balanced allows a slightly deeper healthy pullback
    social_early = _ensure_social(token)
    strong_book = holders_known(token) and (
        social_early.get("influencer_tweet") or social_early.get("has_edge")
    )
    # Balanced: known holders alone can use the strong (slightly looser) floor
    if moon_mode() == "balanced" and holders_known(token) and not social_early.get(
        "namejack_risk"
    ):
        strong_book = True
    ath_floor = NEAR_ATH_FRAC_STRONG if strong_book else NEAR_ATH_FRAC
    if ath >= 3_500 and mcap > 0 and mcap < ath * ath_floor:
        return f"faded from ATH ${ath:,.0f} → ${mcap:,.0f}"

    mkt = token.get("market") or {}
    pc = mkt.get("priceChange") or token.get("priceChange") or {}
    # Balanced: slightly more room on short candles (still blocks violence)
    thr_map = (
        (("m5", -12), ("h1", -18), ("h6", -25), ("h24", -35))
        if moon_mode() == "strict"
        else (("m5", -15), ("h1", -22), ("h6", -28), ("h24", -38))
    )
    for key, thr in thr_map:
        ch = _f(pc.get(key))
        if ch <= thr:
            return f"price {key} {ch:.0f}%"

    hard, hard_why = is_hard_avoid(token)
    if hard:
        return hard_why or "hard avoid"

    # After enrich pipeline: enrich_ok must be True. Pre-enrich cards omit the key.
    if "enrich_ok" in token and token.get("enrich_ok") is not True:
        errs = token.get("enrich_errors") or []
        if any("narrative" in str(e).lower() for e in errs):
            return "no narrative / influencer edge — random chart"
        return "safety unknown — " + (
            ", ".join(str(e) for e in errs[:2]) if errs else "enrich incomplete"
        )

    social = _ensure_social(token)
    tq = social.get("ticker") or {}
    if not tq.get("ok", True):
        return "junk ticker: " + ", ".join(tq.get("issues") or ["bad"])

    # Name-jack ELON/TRUMP with no real tweet + no community = rug packaging
    namejack_min_replies = 10 if moon_mode() == "balanced" else 12
    if social.get("namejack_risk") and not social.get("influencer_tweet"):
        if _i(social.get("replies")) < namejack_min_replies:
            return "name-jack narrative without real influencer tweet / community"

    # Require a real edge — random green charts dump
    # Unverified influencer URL claims alone are NOT enough (spoofable).
    has_real_edge = bool(social.get("has_edge"))
    has_verified_inf = bool(social.get("influencer_tweet"))
    claim_only = bool(social.get("influencer_tweet_claim")) and not has_verified_inf
    if claim_only and not has_real_edge:
        return "unverified influencer link claim — spoof risk"
    if not has_real_edge and not has_verified_inf:
        bond = _bond(token, mcap)
        replies = _i(social.get("replies"))
        hk = holders_known(token)
        # Clean bundle helper for organic path
        bun_ok = True
        bs = token.get("bundleSniper") or {}
        if isinstance(bs, dict):
            overall = str(bs.get("overall") or "").lower()
            if bs.get("hard_reject") or overall in ("critical", "high"):
                bun_ok = False
            bun_pct = _f((bs.get("bundle") or {}).get("bundled_pct") or bs.get("bundled_pct"))
            if bun_pct > (12.0 if moon_mode() == "balanced" else 10.0):
                bun_ok = False

        two_way_f, _, _, _ = _two_way_flow(token)
        mp = _attach_migration_path(token)
        mig_score = _i(mp.get("score"))
        if moon_mode() == "strict":
            organic_ok = (
                bond >= 42
                and mcap >= 18_000
                and replies >= 20
                and social.get("real_x")
                and ath > 0
                and mcap >= ath * 0.92
            )
        else:
            # balanced organic climb path — catch migrators without influencer tags
            # (user: missing tokens that go all the way to migration)
            near_ath_ok = ath > 0 and mcap >= ath * 0.85
            organic_ok = bun_ok and near_ath_ok and (
                (
                    bond >= 35
                    and mcap >= 12_000
                    and replies >= 10
                    and social.get("real_x")
                )
                or (
                    hk
                    and mcap >= RECOMMEND_MIN_MCAP
                    and replies >= 12
                    and (social.get("real_x") or social.get("has_tiktok"))
                    and not social.get("namejack_risk")
                )
                or (
                    hk
                    and bond >= 22
                    and mcap >= 10_000
                    and replies >= 12
                    and not social.get("namejack_risk")
                    and not social.get("status_only")
                )
                # Structure path: already past survival, climbing toward migration
                or (
                    hk
                    and bun_ok
                    and mcap >= 12_000
                    and bond >= 18
                    and near_ath_ok
                    and (two_way_f or replies >= 8)
                    and not social.get("namejack_risk")
                    and not social.get("status_only")
                )
                # Strong migration_path score mid-curve
                or (
                    hk
                    and bun_ok
                    and mcap >= 14_000
                    and mig_score >= 55
                    and near_ath_ok
                    and not social.get("namejack_risk")
                )
                # Near-migration: structure alone is enough (hold book known)
                or (
                    hk
                    and bun_ok
                    and (
                        bond >= MIGRATION_NEAR_MIN_PCT
                        or mcap >= 28_000
                        or mig_score >= 65
                    )
                    and near_ath_ok
                    and not social.get("namejack_risk")
                )
            )
        if not organic_ok:
            return "no narrative / influencer edge — random chart"

    # Ghost / spoof
    if social.get("status_only") and not has_verified_inf:
        # Balanced: allow status-link only if real community replies exist
        if moon_mode() == "strict" or _i(social.get("replies")) < 20:
            return "status-link social spoof (not influencer)"
    if (
        _i(social.get("replies")) == 0
        and not social.get("real_x")
        and not has_verified_inf
    ):
        if age >= 4:
            return "ghost — no replies, no real X"

    safety = token.get("safety") or token.get("safetyReport") or {}
    if safety.get("is_honeypot") or safety.get("rugged") or safety.get("honeypot"):
        return "honeypot / rugged"

    deep = token.get("deepAnalysis") or {}
    if deep.get("dump", {}).get("is_dumped"):
        return deep.get("dump", {}).get("reason") or "deep: dumped"
    if deep.get("verdict") == "SKIP":
        return deep.get("summary") or "deep: skip"

    rr = token.get("runnerRadar") or {}
    if rr.get("crashed") or rr.get("stage") == "crashed":
        return rr.get("summary") or "runner crashed"

    tx = token.get("txActivity") or {}
    if tx.get("zone") in ("wash", "one_way") and _f(tx.get("buy_ratio_m5") or tx.get("ratio")) > 4.0:
        if _i(tx.get("total_m5") or tx.get("total")) >= 15:
            return "one-way wash flow"
    if tx.get("tilt") == "DOWN" and _i(tx.get("total_m5") or tx.get("total")) >= 10:
        return "tx tilt DOWN — sellers in control"

    # Flash fee/volume wars (snipers pay fees; not organic moons)
    try:
        from services.fee_flow import attach_fee_flow, fee_flow_gate

        ff = token.get("feeFlow")
        if not isinstance(ff, dict):
            ff = attach_fee_flow(token)
        ok_f, why_f = fee_flow_gate(ff)
        if not ok_f:
            return why_f or "flash fee/volume war"
    except Exception:
        pass

    # Bundle / sniper hard reject (RugCheck holders when present)
    bs = token.get("bundleSniper") or token.get("bundle_sniper")
    if not isinstance(bs, dict):
        safety = token.get("safety") or {}
        rep = token.get("safetyReport") or {}
        if safety.get("top_holders") or rep.get("bundle") or rep.get("snipers"):
            bs = analyze_bundle_and_snipers(
                safety if safety.get("top_holders") else {
                    "top_holders": safety.get("top_holders") or [],
                    "insider_detected": safety.get("insider_detected"),
                    "insider_networks": safety.get("insider_networks"),
                    "insider_holders": safety.get("insider_holders"),
                    "creator": safety.get("creator"),
                    "creator_pct": safety.get("creator_pct"),
                    "total_holders": safety.get("total_holders"),
                    "risks": safety.get("risks"),
                    "issues": safety.get("issues"),
                    "padre": safety.get("padre"),
                },
                _pf(token),
                token.get("market") or {},
                age_minutes=age or None,
                mcap_usd=mcap or None,
            )
            token["bundleSniper"] = bs
    if isinstance(bs, dict):
        if bs.get("hard_reject"):
            return bs.get("summary") or "bundle/sniper hard reject"
        sn = (bs.get("snipers") or {})
        bn = (bs.get("bundle") or {})
        if sn.get("risk_level") == "critical" or bn.get("risk_level") == "critical":
            return bs.get("summary") or "critical sniper/bundle risk"
        if bn.get("bundled") and _i(bn.get("score")) >= 50:
            return (bn.get("flags") or ["bundled launch"])[0]
        # Even without full holders: flash sniper from age/mcap
        if sn.get("risk_level") == "high" and _i(sn.get("score")) >= 55:
            return (sn.get("flags") or ["high sniper risk"])[0]

    # Lightweight flash path — never overwrite full RugCheck bundle analysis
    hk = holders_known(token)
    if not hk:
        lite = analyze_bundle_and_snipers(
            {},
            _pf(token),
            token.get("market") or {},
            age_minutes=age or None,
            mcap_usd=mcap or None,
        )
        # Only fill empty slot; never replace holders_known analysis
        if not isinstance(bs, dict):
            lite["holders_known"] = False
            if lite.get("overall") in ("clean", "low"):
                lite["overall"] = "unknown"
            token["bundleSniper"] = lite
            bs = lite
        if lite.get("hard_reject") or (lite.get("snipers") or {}).get("risk_level") in (
            "critical",
            "high",
        ):
            if _i((lite.get("snipers") or {}).get("score")) >= 45:
                return lite.get("summary") or "sniper/bundle pattern"

    # After enrich: incomplete holder book cannot be "clean" MOON path
    # (WATCH also demoted in moon_label when holders unknown)
    if "enrich_ok" in token and token.get("enrich_ok") is True and not hk:
        # Still allow through reject_reason for near-miss labeling; grades capped later
        pass

    return None


def is_moon_eligible(token: dict[str, Any]) -> bool:
    return reject_reason(token) is None


def _pillar_momentum(token: dict[str, Any], mcap: float, ath: float) -> tuple[int, list[str]]:
    notes: list[str] = []
    if ath <= 0 or mcap <= 0:
        return 40, ["No ATH yet — risky"]
    ratio = mcap / ath
    pct = round(ratio * 100)
    if ratio >= 0.98:
        notes.append(f"At ATH ({pct}%)")
        return 98, notes
    if ratio >= 0.95:
        notes.append(f"Near ATH {pct}%")
        return 88, notes
    if ratio >= FADE_ATH_FRAC:
        notes.append(f"Holding ATH {pct}%")
        return 72, notes
    if ratio >= NEAR_ATH_FRAC:
        notes.append(f"Soft pullback {pct}% of ATH")
        return 55, notes
    notes.append(f"Off ATH {pct}%")
    return 10, notes


def _pillar_structure(token: dict[str, Any], mcap: float, bond: float) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = 15
    stage = stage_of(token)
    mp = _attach_migration_path(token)
    mig_score = _i(mp.get("score"))
    if stage == "near_migration":
        score += 45
        notes.append(f"Near migration · {bond:.0f}% bonded")
    elif stage == "climb":
        score += 34
        notes.append(f"Climbing · {bond:.0f}% bonded")
    else:
        # Early / just-past survival — still high fail vs migration
        score += 4
        notes.append(f"Just past survival · {bond:.0f}% bonded — prove climb")

    if bond >= 55:
        score += 16
    elif bond >= 40:
        score += 12
    elif bond >= 25:
        score += 8
    elif bond >= 15:
        score += 4

    # Prefer mcap bands that actually migrate (not lottery)
    if 18_000 <= mcap <= 55_000:
        score += 18
        notes.append("Migration mcap band")
    elif 12_000 <= mcap < 18_000:
        score += 14
        notes.append("Runner mcap band")
    elif RECOMMEND_MIN_MCAP <= mcap < 12_000:
        score += 6
        notes.append("Survival band — need structure")

    if mig_score >= 65:
        score += 12
        notes.append(mp.get("summary") or f"Migration path {mig_score}")
    elif mig_score >= 48:
        score += 6
        notes.append(f"Migration path {mig_score}")

    two_way, one_way, _, _ = _two_way_flow(token)
    if two_way:
        score += 8
        notes.append("Two-way flow")
    elif one_way:
        score -= 20
        notes.append("One-way wash")

    return max(0, min(100, score)), notes


def _pillar_narrative(token: dict[str, Any]) -> tuple[int, list[str]]:
    """0–100: influencer + trending narrative edge (most important for moons)."""
    social = _ensure_social(token)
    notes: list[str] = list(social.get("edge_reasons") or [])[:3]
    edge = _i(social.get("edge_score"))
    if social.get("influencer_tweet"):
        edge = max(edge, 80)
        if social.get("tweet_by"):
            notes.insert(0, f"Influencer tweet: {social['tweet_by']}")
    if social.get("namejack_risk"):
        edge = min(edge, 35)
        notes.append("Name-jack risk")
    if not social.get("has_edge") and edge < 40:
        notes.append("Weak narrative edge")
    return max(0, min(100, edge)), notes or ["No narrative edge"]


def _pillar_interest(token: dict[str, Any]) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = 10
    social = _ensure_social(token)
    if social.get("real_x"):
        score += 16
        notes.append("Own X")
    if social.get("has_tiktok"):
        score += 12
        notes.append("TikTok")
    replies = _i(social.get("replies"))
    if replies >= 40:
        score += 22
        notes.append(f"{replies} replies")
    elif replies >= 15:
        score += 14
        notes.append(f"{replies} replies")
    elif replies >= 5:
        score += 6
    else:
        score -= 10
        notes.append("Low replies")

    tx = token.get("txActivity")
    if not tx and (token.get("market") or token.get("priceChange")):
        tx = score_tx_activity(
            pair=token.get("market") or {"priceChange": token.get("priceChange")}
        )
        token["txActivity"] = tx
    if tx:
        if tx.get("in_sweet_spot") or tx.get("tilt") == "UP":
            score += 16
            notes.append(tx.get("summary") or "Tx healthy")
        elif tx.get("tilt") == "DOWN":
            score -= 20
            notes.append("Tx DOWN")
        if tx.get("zone") in ("wash", "one_way", "dead"):
            score -= 18
    return max(0, min(100, score)), notes


def _pillar_safety(token: dict[str, Any]) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = 65
    avoid = (
        (token.get("safetyReport") or {}).get("avoid")
        or (token.get("safety") or {}).get("avoid")
        or token.get("avoid")
        or {}
    )
    flags = set(avoid.get("flags") or [])
    # Must match flag names emitted by avoid_filters (P0 audit fix)
    soft = {
        "fake_twitter",
        "fake_website",
        "suspicious_metadata",
        "ghost_book",  # legacy alias
        "dead_book",
        "low_holders",
        "empty_distribution",
        "sell_pressure",
        "entry_trap",  # legacy alias
        "entry_trap_social",
        "social_spoof_scam",
        "wash_buys",
        "zero_sellers",
        "bot_holder_cluster",
        "parabolic_no_community",
    }
    soft_hit = flags & soft
    if soft_hit:
        score -= 15 * len(soft_hit)
        notes.append("Risk: " + ", ".join(sorted(soft_hit))[:50])
    social = _ensure_social(token)
    if social.get("namejack_risk"):
        score -= 20
        notes.append("Name-jack risk")
    if not soft_hit and not avoid.get("avoid") and not social.get("namejack_risk"):
        score += 12
        notes.append("Clean packaging")
    # Proven deployer track record (migrations / prior moons)
    try:
        from services.dev_risk import attach_dev_risk

        dev = token.get("devRisk") if isinstance(token.get("devRisk"), dict) else None
        if not dev:
            dev = attach_dev_risk(token)
        if dev.get("proven_dev") and not dev.get("hard_reject"):
            score = min(100, score + 12)
            notes.append(dev.get("summary") or "Proven migrator/moon dev")
        elif int(dev.get("prior_moons") or 0) >= 1 and not dev.get("hard_reject"):
            score = min(100, score + 8)
            notes.append(f"{dev.get('prior_moons')} prior moon-class run(s)")
    except Exception:
        pass
    # Unique ticker = mild positive; reused copycat = demote
    try:
        from services.ticker_registry import attach_ticker_uniqueness

        tu = token.get("tickerUniqueness")
        if not isinstance(tu, dict):
            tu = attach_ticker_uniqueness(token, record=False)
        if tu.get("unique"):
            score = min(100, score + 8)
            notes.append(tu.get("summary") or "Unique ticker")
        elif tu.get("status") in ("reused", "heavily_reused", "reused_hot"):
            score = max(0, score - 10)
            notes.append(tu.get("summary") or "Reused ticker")
        elif tu.get("is_hot_meta") and int(tu.get("prior_mints") or 0) >= 1:
            score = max(0, score - 8)
            notes.append(tu.get("summary") or "Hot ticker reuse")
    except Exception:
        pass
    return max(0, min(100, score)), notes


def moon_score(token: dict[str, Any]) -> int:
    if reject_reason(token) is not None:
        return 0
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    bond = _bond(token, mcap)
    m, _ = _pillar_momentum(token, mcap, ath)
    s, _ = _pillar_structure(token, mcap, bond)
    n, _ = _pillar_narrative(token)
    i, _ = _pillar_interest(token)
    safe, _ = _pillar_safety(token)
    # Momentum + structure + narrative — structure matters for migration survival
    composite = 0.26 * m + 0.24 * s + 0.26 * n + 0.12 * i + 0.12 * safe
    if m < 55:
        composite = min(composite, 48)
    # Allow climb-path tokens with weaker influencer narrative if structure is strong
    if n < 40 and s < 50:
        composite = min(composite, 50)
    elif n < 35 and s >= 55:
        composite = min(composite, 62)
    social = _ensure_social(token)
    if social.get("influencer_tweet"):
        composite = min(100, composite + 8)
    # Proven migrators / prior moons — soft boost (look for good dev records)
    try:
        from services.dev_risk import dev_score_boost

        db = dev_score_boost(token)
        if db:
            composite = min(100, composite + db)
    except Exception:
        pass
    # Unique brand ticker mild boost; reused demote
    try:
        from services.ticker_registry import ticker_score_boost

        tb = ticker_score_boost(token)
        if tb:
            composite = max(0, min(100, composite + tb))
    except Exception:
        pass
    # Organic fee/volume trail boost; flash/wash demote
    try:
        from services.fee_flow import fee_score_boost

        fb = fee_score_boost(token)
        if fb:
            composite = max(0, min(100, composite + fb))
    except Exception:
        pass
    # Migration path readiness (tokens that can actually graduate)
    try:
        mp = _attach_migration_path(token)
        ms = _i(mp.get("score"))
        if ms >= 65:
            composite = min(100, composite + 8)
        elif ms >= 50:
            composite = min(100, composite + 4)
        if str(mp.get("lane") or "") == "early_lottery":
            composite = min(composite, 52)
    except Exception:
        pass
    # Prefer climb/near-migration stages over just-survived charts
    st = stage_of(token)
    if st == "near_migration":
        composite = min(100, composite + 6)
    elif st == "climb":
        composite = min(100, composite + 3)
    return max(0, min(100, int(round(composite))))


def moon_label(
    score: int,
    *,
    momentum: int = 100,
    narrative: int = 100,
    social: dict | None = None,
    holders_known: bool = True,
    mcap: float = 0.0,
    bond: float = 0.0,
    structure: int = 0,
    stage: str = "",
) -> str:
    social = social or {}
    bal = moon_mode() == "balanced"
    mcap = mcap or 0.0
    # Never MOON/WATCH below survival floor
    if 0 < mcap < RECOMMEND_MIN_MCAP:
        return LABEL_WEAK
    # MOON only with real edge + near ATH + known holder book
    moon_score_floor = 72 if bal else 75
    moon_mom = 78 if bal else 80
    moon_narr = 50 if bal else 55
    if (
        score >= moon_score_floor
        and momentum >= moon_mom
        and narrative >= moon_narr
        and holders_known
        and (social.get("influencer_tweet") or social.get("has_edge"))
    ):
        return LABEL_MOON
    # Climb / near-migration structure MOON without influencer (real migrators)
    if (
        bal
        and holders_known
        and score >= 70
        and momentum >= 76
        and structure >= 55
        and mcap >= 12_000
        and (bond >= 18 or stage in ("climb", "near_migration") or mcap >= 18_000)
        and not social.get("namejack_risk")
        and not social.get("status_only")
    ):
        return LABEL_MOON
    # WATCH also needs holder book truth — unknown book is not a recommendation grade
    watch_score = 55 if bal else 58
    watch_mom = 66 if bal else 70
    watch_narr = 34 if bal else 40
    if holders_known and score >= watch_score and momentum >= watch_mom and narrative >= watch_narr:
        return LABEL_WATCH
    if holders_known and score >= 50 and social.get("influencer_tweet") and momentum >= 72:
        return LABEL_WATCH
    # Balanced: organic community climb can be WATCH without influencer tag
    if (
        bal
        and holders_known
        and score >= 54
        and momentum >= 68
        and (
            narrative >= 30
            or structure >= 50
            or stage in ("climb", "near_migration")
            or mcap >= 14_000
        )
        and (
            social.get("real_x")
            or _i(social.get("replies")) >= 10
            or structure >= 55
            or stage == "near_migration"
        )
        and not social.get("namejack_risk")
    ):
        return LABEL_WATCH
    return LABEL_WEAK


def evaluate(token: dict[str, Any]) -> dict[str, Any]:
    social = _ensure_social(token)
    reason = reject_reason(token)
    if reason:
        return {
            "eligible": False,
            "reject": reason,
            "moon_score": 0,
            "label": LABEL_REJECT,
            "confidence": 0,
            "stage": stage_of(token),
            "why": [reason],
            "pillars": {},
            "ath_retention_pct": None,
            "narrative": social.get("summary") or "",
            "badges": social.get("badges") or [],
            "influencer_tweet": social.get("influencer_tweet"),
            "tweet_by": social.get("tweet_by"),
        }

    merge_ath_into_token(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    bond = _bond(token, mcap)
    m_sc, m_why = _pillar_momentum(token, mcap, ath)
    s_sc, s_why = _pillar_structure(token, mcap, bond)
    n_sc, n_why = _pillar_narrative(token)
    i_sc, i_why = _pillar_interest(token)
    safe_sc, safe_why = _pillar_safety(token)

    hk = holders_known(token)
    st = stage_of(token)
    score = moon_score(token)
    label = moon_label(
        score,
        momentum=m_sc,
        narrative=n_sc,
        social=social,
        holders_known=hk,
        mcap=mcap,
        bond=bond,
        structure=s_sc,
        stage=st,
    )
    conf = int(
        round(0.26 * m_sc + 0.22 * s_sc + 0.26 * n_sc + 0.14 * i_sc + 0.12 * safe_sc)
    )
    if social.get("influencer_tweet"):
        conf = min(99, conf + 10)
    if st in ("climb", "near_migration"):
        conf = min(99, conf + 4)
    if not hk:
        conf = min(conf, 48)
        safe_sc = min(safe_sc, 50)
    if label == LABEL_MOON:
        conf = max(conf, 75)
    if label == LABEL_WEAK:
        conf = min(conf, 45)
    conf = max(0, min(99, conf))

    # Soft blend learned P(good)/P(bad) into score + conf
    score, conf, learn_meta = learning_soft_adjust(token, score, conf)
    if learn_meta.get("applied") and learn_meta.get("delta_score", 0) < 0:
        # Re-label after demote
        label = moon_label(
            score,
            momentum=m_sc,
            narrative=n_sc,
            social=social,
            holders_known=hk,
            mcap=mcap,
            bond=bond,
            structure=s_sc,
            stage=st,
        )
        if label == LABEL_WEAK:
            conf = min(conf, 45)

    ath_ret = round(100 * mcap / ath, 1) if ath > 0 and mcap > 0 else None
    mp = _attach_migration_path(token)
    why: list[str] = []
    why.extend(n_why[:2])
    why.extend(m_why[:1])
    why.extend(s_why[:1])
    why.extend(i_why[:1])
    if mp.get("summary") and st in ("climb", "near_migration"):
        why.append(str(mp["summary"])[:90])
    if not hk:
        why.append("Holder book incomplete — not MOON/WATCH grade")
    if learn_meta.get("applied"):
        why.append(
            f"Learn P(good)≈{100 * float(learn_meta.get('p_good') or 0):.0f}%"
            f" ({learn_meta.get('delta_score', 0):+d} score)"
        )
    if not why:
        why.append("Passed capital-protection filters")

    return {
        "eligible": True,
        "reject": None,
        "moon_score": score,
        "label": label,
        "confidence": conf,
        "stage": st,
        "ath_retention_pct": ath_ret,
        "holders_known": hk,
        "learning_soft": learn_meta if learn_meta.get("applied") else None,
        "why": why[:8],
        "pillars": {
            "momentum": m_sc,
            "structure": s_sc,
            "narrative": n_sc,
            "interest": i_sc,
            "safety": safe_sc,
        },
        "migration_path": {
            "score": mp.get("score"),
            "lane": mp.get("lane"),
            "summary": mp.get("summary"),
            "recommend": mp.get("recommend"),
        }
        if mp
        else None,
        "narrative": social.get("summary") or "",
        "narratives": social.get("narratives") or [],
        "badges": social.get("badges") or [],
        "influencer_tweet": bool(social.get("influencer_tweet")),
        "tweet_by": social.get("tweet_by"),
        "tweet_url": social.get("tweet_url"),
        "edge_score": social.get("edge_score"),
        "bundle": (token.get("bundleSniper") or {}).get("bundle")
        or token.get("bundle"),
        "snipers": (token.get("bundleSniper") or {}).get("snipers")
        or token.get("snipers"),
    }


def filter_and_rank(
    tokens: list[dict[str, Any]],
    *,
    min_score: int = 55,
    min_confidence: int = 52,
    max_bundled_pct: float | None = 12.0,
    require_influencer: bool = False,
    require_holders: bool = True,
) -> list[dict[str, Any]]:
    """Only high-confidence WATCH/MOON with narrative edge.

    Optional adaptive gates (Phase 3): max_bundled_pct, require_influencer.
    require_holders: never show MOON/WATCH without RugCheck holder book.
    """
    out: list[dict[str, Any]] = []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        ev = evaluate(t)
        if not ev["eligible"]:
            continue
        if ev["label"] == LABEL_WEAK:
            continue  # never recommend weak
        if require_holders and not holders_known(t) and not ev.get("holders_known"):
            continue
        if int(ev["moon_score"]) < min_score:
            continue
        if int(ev.get("confidence") or 0) < min_confidence:
            continue
        # Soft learning floor: high P(bad) already demoted score; extra skip if SKIP
        ls = ev.get("learning_soft") or t.get("learning_soft") or {}
        if ls.get("applied") and str(ls.get("action") or "").upper() == "SKIP":
            if float(ls.get("p_good") or 1) < 0.28:
                continue
        if require_influencer and not (
            ev.get("influencer_tweet")
            or (t.get("socialSignals") or {}).get("influencer_tweet")
        ):
            continue
        if max_bundled_pct is not None:
            bun = (
                (t.get("bundle") or {}).get("bundled_pct")
                if isinstance(t.get("bundle"), dict)
                else None
            )
            if bun is None and isinstance(t.get("bundleSniper"), dict):
                bun = (t.get("bundleSniper") or {}).get("bundled_pct")
                if bun is None:
                    bun = ((t.get("bundleSniper") or {}).get("bundle") or {}).get(
                        "bundled_pct"
                    )
            try:
                if bun is not None and float(bun) > float(max_bundled_pct):
                    continue
            except (TypeError, ValueError):
                pass
        row = dict(t)
        row["moon"] = ev
        row["moon_score"] = ev["moon_score"]
        row["moon_label"] = ev["label"]
        row["confidence"] = ev["confidence"]
        row["stage"] = ev["stage"]
        row["ath_retention_pct"] = ev.get("ath_retention_pct")
        row["socialSignals"] = t.get("socialSignals")
        out.append(row)

    def _stage_rank(row: dict[str, Any]) -> int:
        st = str(row.get("stage") or (row.get("moon") or {}).get("stage") or "")
        if st == "near_migration":
            return 0
        if st == "climb":
            return 1
        return 2

    out.sort(
        key=lambda x: (
            0 if x.get("moon_label") == LABEL_MOON else 1,
            _stage_rank(x),  # prefer migration path over lottery survivors
            0 if x.get("moon", {}).get("influencer_tweet") else 1,
            -int((x.get("moon") or {}).get("pillars", {}).get("structure") or 0),
            -int(x.get("confidence") or 0),
            -int(x.get("moon_score") or 0),
            -float(x.get("mcap_usd") or 0),
        )
    )
    return out

