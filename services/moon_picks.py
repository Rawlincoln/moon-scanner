"""Moon Picks v3 — capital-protection gate.

After user losses: almost never recommend. Show only when:
  1. Not dumped / not rug / not ghost
  2. Near ATH (still climbing)
  3. Real narrative edge: influencer tweet OR trending meta + community
  4. Multi-pillar score + confidence agree

Random near-ATH charts without story = REJECT (they dump ~always).
"""

from __future__ import annotations

from typing import Any

from config import (
    DUMP_HIDE_FRAC,
    GRADUATION_MCAP_USD,
    MIGRATION_MCAP_MAX_USD,
    MIGRATION_NEAR_MIN_PCT,
)
from services.avoid_filters import BLOCKED_MINTS, is_hard_avoid
from services.bundle_sniper import analyze_bundle_and_snipers
from services.runner_radar import extract_ath_mcap, extract_mcap_usd, is_crashed_runner
from services.social_signals import analyze_social_narrative
from services.tx_activity import score_tx_activity

# Stricter band — early dust rarely moons
MIN_MCAP = 4_000
MAX_MCAP = MIGRATION_MCAP_MAX_USD
# Must be very close to ATH (user: almost everything recommended dumped)
NEAR_ATH_FRAC = 0.88  # within −12% of ATH
FADE_ATH_FRAC = 0.93

LABEL_MOON = "MOON"
LABEL_WATCH = "WATCH"
LABEL_WEAK = "WEAK"
LABEL_REJECT = "REJECT"


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


def reject_reason(token: dict[str, Any]) -> str | None:
    """Hard reject — never show / recommend."""
    mint = _mint(token)
    if not mint:
        return "missing mint"
    if mint in BLOCKED_MINTS:
        return "blocklist"
    if token.get("skipped"):
        return str(token.get("skipReason") or token.get("skip_reason") or "skipped")

    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    peak = max(ath, _f(token.get("_peak_mcap") or token.get("peak_mcap")))
    age = _f(token.get("age_minutes"))

    if mcap > 0 and mcap < MIN_MCAP:
        return f"too small ${mcap:,.0f}"
    if mcap > MAX_MCAP:
        return f"too large ${mcap:,.0f}"

    if 0 < age < 1.0 and mcap >= 15_000:
        return "flash launch — too young for mcap"

    crashed, why = is_crashed_runner(
        token, mcap=mcap or None, ath=ath or None, peak=peak or None
    )
    if crashed:
        return why or "dumped"

    high = max(ath, peak)
    if high >= 2_500 and mcap > 0 and mcap < high * max(DUMP_HIDE_FRAC, 0.85):
        return f"dumped −{(1 - mcap / high) * 100:.0f}% from ${high:,.0f}"

    # Stricter: must hold near ATH
    if ath >= 3_500 and mcap > 0 and mcap < ath * NEAR_ATH_FRAC:
        return f"faded from ATH ${ath:,.0f} → ${mcap:,.0f}"

    mkt = token.get("market") or {}
    pc = mkt.get("priceChange") or token.get("priceChange") or {}
    for key, thr in (("m5", -12), ("h1", -18), ("h6", -25), ("h24", -35)):
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
    if social.get("namejack_risk") and not social.get("influencer_tweet"):
        if _i(social.get("replies")) < 12:
            return "name-jack narrative without real influencer tweet / community"

    # Require a real edge — random green charts dump
    # Unverified influencer URL claims alone are NOT enough (spoofable).
    has_real_edge = bool(social.get("has_edge"))
    has_verified_inf = bool(social.get("influencer_tweet"))
    claim_only = bool(social.get("influencer_tweet_claim")) and not has_verified_inf
    if claim_only and not has_real_edge:
        return "unverified influencer link claim — spoof risk"
    if not has_real_edge and not has_verified_inf:
        # Allow pure organic near-mig only if strong community
        bond = _bond(token, mcap)
        replies = _i(social.get("replies"))
        organic_ok = (
            bond >= 42
            and mcap >= 18_000
            and replies >= 20
            and social.get("real_x")
            and ath > 0
            and mcap >= ath * 0.92
        )
        if not organic_ok:
            return "no narrative / influencer edge — random chart"

    # Ghost / spoof
    if social.get("status_only") and not has_verified_inf:
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
    holders_known = bool(
        (isinstance(bs, dict) and bs.get("holders_known"))
        or (token.get("safety") or {}).get("top_holders")
    )
    if not holders_known:
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
    if stage == "near_migration":
        score += 42
        notes.append(f"Near migration · {bond:.0f}% bonded")
    elif stage == "climb":
        score += 30
        notes.append(f"Climbing · {bond:.0f}% bonded")
    else:
        score += 8
        notes.append(f"Early · {bond:.0f}% bonded — high fail rate")

    if bond >= 55:
        score += 16
    elif bond >= 40:
        score += 12
    elif bond >= 25:
        score += 6

    if 12_000 <= mcap <= 55_000:
        score += 14
        notes.append("Runner mcap band")
    elif 6_000 <= mcap < 12_000:
        score += 6
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
    soft = {
        "fake_twitter",
        "fake_website",
        "suspicious_metadata",
        "ghost_book",
        "low_holders",
        "sell_pressure",
        "entry_trap",
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
    # Narrative + momentum dominate (what actually moons)
    composite = 0.28 * m + 0.18 * s + 0.30 * n + 0.14 * i + 0.10 * safe
    if m < 55:
        composite = min(composite, 48)
    if n < 40:
        composite = min(composite, 50)
    social = _ensure_social(token)
    if social.get("influencer_tweet"):
        composite = min(100, composite + 8)
    return max(0, min(100, int(round(composite))))


def moon_label(
    score: int,
    *,
    momentum: int = 100,
    narrative: int = 100,
    social: dict | None = None,
    holders_known: bool = True,
) -> str:
    social = social or {}
    # MOON only with real edge + near ATH + known holder book
    if (
        score >= 75
        and momentum >= 80
        and narrative >= 55
        and holders_known
        and (social.get("influencer_tweet") or social.get("has_edge"))
    ):
        return LABEL_MOON
    if score >= 58 and momentum >= 70 and narrative >= 40:
        return LABEL_WATCH
    if score >= 50 and social.get("influencer_tweet") and momentum >= 75:
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

    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    bond = _bond(token, mcap)
    m_sc, m_why = _pillar_momentum(token, mcap, ath)
    s_sc, s_why = _pillar_structure(token, mcap, bond)
    n_sc, n_why = _pillar_narrative(token)
    i_sc, i_why = _pillar_interest(token)
    safe_sc, safe_why = _pillar_safety(token)

    holders_known = bool(
        (token.get("bundleSniper") or {}).get("holders_known")
        or (token.get("safety") or {}).get("top_holders")
    )
    score = moon_score(token)
    label = moon_label(
        score,
        momentum=m_sc,
        narrative=n_sc,
        social=social,
        holders_known=holders_known,
    )
    conf = int(
        round(0.30 * m_sc + 0.15 * s_sc + 0.30 * n_sc + 0.15 * i_sc + 0.10 * safe_sc)
    )
    if social.get("influencer_tweet"):
        conf = min(99, conf + 10)
    if not holders_known:
        conf = min(conf, 60)
    if label == LABEL_MOON:
        conf = max(conf, 75)
    if label == LABEL_WEAK:
        conf = min(conf, 45)
    conf = max(0, min(99, conf))

    ath_ret = round(100 * mcap / ath, 1) if ath > 0 and mcap > 0 else None
    why: list[str] = []
    why.extend(n_why[:2])
    why.extend(m_why[:1])
    why.extend(s_why[:1])
    why.extend(i_why[:1])
    if not why:
        why.append("Passed capital-protection filters")

    return {
        "eligible": True,
        "reject": None,
        "moon_score": score,
        "label": label,
        "confidence": conf,
        "stage": stage_of(token),
        "ath_retention_pct": ath_ret,
        "why": why[:6],
        "pillars": {
            "momentum": m_sc,
            "structure": s_sc,
            "narrative": n_sc,
            "interest": i_sc,
            "safety": safe_sc,
        },
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
) -> list[dict[str, Any]]:
    """Only high-confidence WATCH/MOON with narrative edge.

    Optional adaptive gates (Phase 3): max_bundled_pct, require_influencer.
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
        if int(ev["moon_score"]) < min_score:
            continue
        if int(ev.get("confidence") or 0) < min_confidence:
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

    out.sort(
        key=lambda x: (
            0 if x.get("moon", {}).get("influencer_tweet") else 1,
            0 if x.get("moon_label") == LABEL_MOON else 1,
            -int(x.get("confidence") or 0),
            -int(x.get("moon_score") or 0),
            -int((x.get("moon") or {}).get("edge_score") or 0),
        )
    )
    return out

