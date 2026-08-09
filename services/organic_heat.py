"""Organic Heat — optimized mid-cap climbers (12–28k path + breakout).

Design (optimized from live winners):
  - Entry band ≥ $7k (past lottery death zone; aligned with survival floor)
  - Sweet $7–12k → path into $14–28k; breakout $12–55k still scored
  - Higher recall than Moons; HEAT grade is Telegram-quality
  - Soft packaging demotes; entry-trap / social-spoof hard-block (honest books)

Never claims "safe". Blocks rugs / hard avoids / critical bundles / deep dumps.
"""

from __future__ import annotations

from typing import Any

from services.accuracy import holders_known, merge_ath_into_token
from services.avoid_filters import BLOCKED_MINTS, is_hard_avoid
from services.bundle_sniper import analyze_bundle_and_snipers
from services.runner_radar import extract_ath_mcap, extract_mcap_usd, is_crashed_runner
from services.social_signals import analyze_social_narrative

# Optimized band: proved survival → climb / breakout
# - $7–12k: path to $14–28k (what organic heat is winning on)
# - $12–55k: breakout / pre-grad heat
HEAT_MCAP_MIN = 7_000.0
HEAT_MCAP_MAX = 55_000.0
TARGET_TP_LOW = 14_000.0
TARGET_TP_HIGH = 28_000.0
BREAKOUT_MIN = 12_000.0
# Prefer entries that already cleared survival + room to 2× into target
SWEET_ENTRY_MIN = 7_000.0
SWEET_ENTRY_MAX = 12_000.0
# HEAT grade (Telegram) should sit in quality band
HEAT_ALERT_MIN_MCAP = 7_000.0
MIN_AGE_MIN = 2.0  # past flash sniper window
MAX_AGE_MIN = 240.0  # 4h — organic runners can graduate mid-session
# Pullbacks: slightly tighter for quality heat
ATH_HARD_DUMP = 0.55  # −45% hard dump
ATH_SOFT_FLOOR = 0.70  # −30% still scorable as WARM/RISKY
# Just-graduated flash still scannable on heat for a short window
FLASH_GRAD_MAX_AGE_MIN = 90.0
FLASH_GRAD_MAX_MCAP = 150_000.0  # under $150k flash only

# Soft packaging may demote score. Entry-trap / social-spoof are HARD on heat
# (those patterns dump — do not alert as organic).
HEAT_SOFT_PACKAGING_FLAGS = frozenset(
    {
        "fake_twitter",
        "fake_website",
        "parabolic_no_community",
        "wash_buys",
        "dead_book",
        "low_holders",
        "empty_distribution",
        "suspicious_metadata",
        "zero_sellers",
        "bot_holder_cluster",
    }
)
# Always invest-blocking on heat (not soft-pass)
HEAT_HARD_PACKAGING_FLAGS = frozenset(
    {
        "entry_trap_social",
        "social_spoof_scam",
    }
)

LABEL_HEAT = "HEAT"  # best organic heat (still not "safe moon")
LABEL_WARM = "WARM"  # mid heat
LABEL_RISKY = "RISKY"  # thin book / partial enrich — size dust only
LABEL_SKIP = "SKIP"


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


def _ensure_social(token: dict[str, Any]) -> dict[str, Any]:
    social = token.get("socialSignals")
    if isinstance(social, dict) and social.get("edge_score") is not None:
        return social
    pf = token.get("pumpfun") or {}
    social = analyze_social_narrative(
        pump_coin=pf,
        name=token.get("name") or pf.get("name") or "",
        symbol=token.get("symbol") or pf.get("symbol") or "",
        description=pf.get("description") or token.get("description") or "",
    )
    token["socialSignals"] = social
    return social


def _bundled_pct(token: dict[str, Any]) -> float | None:
    bun = token.get("bundle") if isinstance(token.get("bundle"), dict) else {}
    bs = token.get("bundleSniper") if isinstance(token.get("bundleSniper"), dict) else {}
    for src in (bun, bs, (bs.get("bundle") or {}) if isinstance(bs, dict) else {}):
        if isinstance(src, dict) and src.get("bundled_pct") is not None:
            try:
                return float(src["bundled_pct"])
            except (TypeError, ValueError):
                pass
    return None


def _dev_profile(token: dict[str, Any]) -> dict[str, Any]:
    """Creator history: sold bag, # tokens launched, # migrated, this mint status."""
    safety = token.get("safety") or {}
    pf = token.get("pumpfun") or {}
    creator = (
        safety.get("creator")
        or pf.get("creator")
        or token.get("creator")
        or ""
    )
    launched = _i(
        safety.get("creator_token_count")
        if safety.get("creator_token_count") is not None
        else token.get("creator_token_count")
    )
    rows = safety.get("creator_tokens") or token.get("creator_tokens") or []
    if not isinstance(rows, list):
        rows = []
    if launched <= 0 and rows:
        launched = len(rows)

    migrated = _i(safety.get("creator_migrated_count"))
    if migrated <= 0 and rows:
        for ct in rows:
            if not isinstance(ct, dict):
                continue
            if (
                ct.get("migrated")
                or ct.get("complete")
                or ct.get("raydiumPool")
                or ct.get("raydium")
                or ct.get("graduated")
                or str(ct.get("status") or "").lower()
                in ("migrated", "graduated", "complete")
            ):
                migrated += 1
                continue
            try:
                mc = float(ct.get("marketCap") or ct.get("usd_market_cap") or 0)
                if mc >= 50_000:
                    migrated += 1
            except (TypeError, ValueError):
                pass

    creator_sold = bool(safety.get("creator_sold"))
    creator_pct = _f(safety.get("creator_pct"))
    creator_bal = _f(safety.get("creator_balance"))
    # Infer sold if creator not in top holders and balance 0
    if not creator_sold and safety.get("top_holders") and creator:
        found = False
        for h in safety.get("top_holders") or []:
            owner = str(h.get("owner") or h.get("address") or "")
            if creator and creator in owner:
                found = True
                creator_pct = max(creator_pct, _f(h.get("pct")))
                break
        if not found and creator_bal <= 0 and holders_known(token):
            creator_sold = True

    this_complete = bool(pf.get("complete") or token.get("complete"))
    on_curve = safety.get("on_bonding_curve")
    if on_curve is None:
        on_curve = not this_complete
    bond = _f(token.get("bonding_progress") or pf.get("bonding_progress"))
    mcap = extract_mcap_usd(token)
    # Migration path for THIS token
    if this_complete or (on_curve is False and mcap >= 40_000):
        this_status = "migrated"
    elif bond >= 55 or mcap >= 40_000:
        this_status = "near_migration"
    elif bond >= 25 or mcap >= 15_000:
        this_status = "climbing"
    else:
        this_status = "early_curve"

    migrate_rate = (migrated / launched) if launched > 0 else None
    return {
        "creator": (str(creator)[:12] + "…") if creator and len(str(creator)) > 14 else str(creator or ""),
        "creator_full": str(creator or ""),
        "creator_sold": creator_sold,
        "creator_pct": round(creator_pct, 2),
        "creator_balance": creator_bal,
        "tokens_launched": launched,
        "tokens_migrated": migrated,
        "migrate_rate": round(migrate_rate, 3) if migrate_rate is not None else None,
        "this_status": this_status,
        "this_complete": this_complete,
        "on_bonding_curve": bool(on_curve),
        "bonding_pct": round(bond, 1),
    }


def heat_reject_reason(token: dict[str, Any]) -> str | None:
    """Hard rejects only — intentionally looser than moon/snipe."""
    mint = (token.get("tokenAddress") or token.get("mint") or "").strip()
    if not mint:
        return "missing mint"
    if mint in BLOCKED_MINTS:
        return "blocklist"
    if token.get("skipped"):
        return token.get("skipReason") or "skipped"

    merge_ath_into_token(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    age = _f(token.get("age_minutes"))

    pf = token.get("pumpfun") or {}
    complete = bool(pf.get("complete") or token.get("complete"))
    # Just-graduated flash: allow complete coins briefly (catch post-curve early)
    if complete:
        if age > FLASH_GRAD_MAX_AGE_MIN:
            return "graduated too long ago — use Graduated lane"
        if mcap > FLASH_GRAD_MAX_MCAP:
            return f"flash grad too large ${mcap:,.0f} — use Graduated lane"
    if mcap > 0 and mcap < HEAT_MCAP_MIN:
        return f"below $7k heat band (${mcap:,.0f}) — lottery zone"
    if mcap > HEAT_MCAP_MAX and not (
        complete and age <= FLASH_GRAD_MAX_AGE_MIN and mcap <= FLASH_GRAD_MAX_MCAP
    ):
        return f"above heat/breakout band ${mcap:,.0f}"
    if age < MIN_AGE_MIN:
        return f"too fresh {age:.1f}m — need {MIN_AGE_MIN:.0f}m+ for organic heat"
    if age > MAX_AGE_MIN and not (complete and age <= FLASH_GRAD_MAX_AGE_MIN):
        return f"too old {age:.0f}m"

    # Flash holders: 245 @ 2m-class books are not organic heat
    try:
        from services.avoid_filters import flash_holders_reason

        holders = _i(
            (token.get("safety") or {}).get("total_holders")
            or token.get("total_holders")
        )
        fh = flash_holders_reason(holders, age)
        if fh:
            return fh
    except Exception:
        pass

    # Flash fee/volume wars — snipers paying, not organic heat
    try:
        from services.fee_flow import attach_fee_flow, fee_flow_gate

        ff = attach_fee_flow(token)
        ok_f, why_f = fee_flow_gate(ff)
        if not ok_f:
            return why_f or "flash fee/volume war"
    except Exception:
        pass

    crashed, why = is_crashed_runner(token)
    if crashed:
        return why or "crashed"

    # Deep dump only (moons use −12%; heat allows pullbacks)
    if ath >= 3_000 and mcap > 0 and mcap < ath * ATH_HARD_DUMP:
        return f"hard dump −{(1 - mcap / ath) * 100:.0f}% from ATH"

    # P0: full is_hard_avoid parity with Moons/Snipes except pure soft-packaging sets
    avoid = (
        token.get("avoid")
        or (token.get("safetyReport") or {}).get("avoid")
        or (token.get("safety") or {}).get("avoid")
        or {}
    )
    flags = set(avoid.get("flags") or []) if isinstance(avoid, dict) else set()
    # Entry-trap / social-spoof always kill heat (not organic)
    if flags & HEAT_HARD_PACKAGING_FLAGS:
        return "social packaging trap — not organic heat"

    hard, hard_why = is_hard_avoid(token)
    if hard:
        # Only allow through if ALL flags are soft packaging (status-link style)
        if flags and flags <= HEAT_SOFT_PACKAGING_FLAGS:
            pass  # demote in scoring later
        else:
            return hard_why or "hard avoid"

    # One-way wash under $20k is not organic heat
    mkt = token.get("market") or {}
    txns = (mkt.get("txns") or {}).get("m5") or {}
    buys = _i(txns.get("buys"))
    sells = _i(txns.get("sells"))
    if buys >= 15 and sells == 0 and mcap < 20_000:
        return "one-way wash buys — not organic heat"
    if buys >= 10 and sells >= 1 and buys / max(sells, 1) > 10 and mcap < 15_000:
        return "extreme buy skew — wash risk"

    safety = token.get("safety") or {}
    if safety.get("is_honeypot") or safety.get("rugged") or safety.get("honeypot"):
        return "honeypot / rugged"
    if safety.get("error") and token.get("enrich_ok") is True:
        # enrich claimed ok but safety error — treat as incomplete
        pass

    # --- Dev / serial deployer / sold bag / prior rugs ---
    dev = _dev_profile(token)
    launched = int(dev.get("tokens_launched") or 0)
    try:
        from services.dev_risk import attach_dev_risk, dev_risk_gate

        dr = attach_dev_risk(token)
        ok_d, why_d = dev_risk_gate(dr)
        if not ok_d:
            return why_d or "dev risk — prior rugs / serial farm"
        if int(dr.get("prior_rugs") or 0) >= 1 and launched >= 3:
            # Soft block on heat for single prior rug + multi launch
            if int(dr.get("prior_rugs") or 0) >= 2 or launched >= 6:
                return dr.get("summary") or "prior rug history"
    except Exception:
        pass
    if launched >= 15:
        return f"serial deployer — {launched} tokens launched"
    if launched >= 8 and int(dev.get("tokens_migrated") or 0) == 0:
        return f"serial deploys ({launched}) with 0 migrations — farm risk"
    if (
        dev.get("creator_sold")
        and _f(dev.get("creator_pct")) < 0.5
        and launched >= 3
    ):
        return "dev sold bag + multi-launch history"
    if "creator_dumped" in flags or "dev_out_green_chart" in flags:
        return "dev out / creator dumped"
    if "serial_creator" in flags:
        return f"serial creator ({launched or 'many'} tokens)"
    # Bundle / sniper capital flags always fatal on heat
    if flags & {"bundled", "snipers", "insiders", "ai_pitch_no_socials"}:
        return hard_why or f"risk: {', '.join(sorted(flags & {'bundled', 'snipers', 'insiders', 'ai_pitch_no_socials'}))}"

    # After enrich pipeline: allow incomplete as RISKY later, but reject pure fail
    if "enrich_ok" in token and token.get("enrich_ok") is not True:
        errs = token.get("enrich_errors") or []
        # Pre-enrich capital rejects already set; incomplete enrich → soft path
        # Only hard-block if honeypot-ish errors
        joined = " ".join(str(e) for e in errs).lower()
        if "honeypot" in joined or "rugged" in joined:
            return "safety fail"
        # keep going without enrich — scored as RISKY

    pc = token.get("priceChange") or (token.get("market") or {}).get("priceChange") or {}
    if _f(pc.get("m5")) <= -35 or _f(pc.get("h1")) <= -45:
        return "sharp dump candle"

    # Bundle critical only
    bs = token.get("bundleSniper")
    if not isinstance(bs, dict) and (safety.get("top_holders") or token.get("enrich_ok")):
        try:
            bs = analyze_bundle_and_snipers(
                safety,
                token.get("pumpfun") or {},
                token.get("market") or {},
                age_minutes=age or None,
                mcap_usd=mcap or None,
            )
            token["bundleSniper"] = bs
        except Exception:
            bs = {}
    if isinstance(bs, dict):
        if bs.get("hard_reject") and (
            str(bs.get("overall") or "").lower() in ("critical", "high")
            or _f((bs.get("bundle") or {}).get("bundled_pct")) >= 25
        ):
            return bs.get("summary") or "bundle/sniper hard reject"
        sn = (bs.get("snipers") or {}) if isinstance(bs.get("snipers"), dict) else {}
        if sn.get("risk_level") == "critical":
            return "sniper critical"

    bun = _bundled_pct(token)
    if bun is not None and bun >= 28:
        return f"bundled {bun:.0f}% — farm"

    return None


def _heat_signals(token: dict[str, Any]) -> tuple[int, list[str], dict[str, Any]]:
    """Score organic heat 0–100 from replies, structure, flow, mild narrative."""
    why: list[str] = []
    score = 28
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    bond = _f(token.get("bonding_progress"))
    age = _f(token.get("age_minutes"))
    social = _ensure_social(token)
    replies = _i(social.get("replies") or (token.get("pumpfun") or {}).get("reply_count"))
    pc = token.get("priceChange") or (token.get("market") or {}).get("priceChange") or {}
    m5 = _f(pc.get("m5"))
    h1 = _f(pc.get("h1"))
    tx = token.get("txActivity") or {}
    ath_ret = (100 * mcap / ath) if ath > 0 and mcap > 0 else None
    hk = holders_known(token)
    bun = _bundled_pct(token)
    meta: dict[str, Any] = {
        "replies": replies,
        "ath_retention_pct": round(ath_ret, 1) if ath_ret is not None else None,
        "holders_known": hk,
        "bundle_pct": bun,
        "m5": m5,
    }

    # --- Community heat ---
    if replies >= 40:
        score += 22
        why.append(f"{replies} replies — hot chat")
    elif replies >= 18:
        score += 14
        why.append(f"{replies} replies")
    elif replies >= 8:
        score += 8
        why.append(f"{replies} replies")
    elif replies >= 3:
        score += 3
    else:
        score -= 4
        why.append("Thin replies")

    # --- Structure / 12–21k path OR breakout $12–55k ---
    tp2x = mcap * 2.0 if mcap > 0 else 0.0
    meta["target_2x_usd"] = round(tp2x, 0) if tp2x else None
    meta["target_zone"] = [TARGET_TP_LOW, TARGET_TP_HIGH]
    meta["upside_to_21k"] = (
        round(TARGET_TP_HIGH / mcap, 2) if mcap > 0 and mcap < TARGET_TP_HIGH else None
    )
    meta["breakout"] = mcap >= BREAKOUT_MIN
    pf = token.get("pumpfun") or {}
    if pf.get("complete") or token.get("complete"):
        score += 6
        why.append("Just graduated — early post-curve window")

    if SWEET_ENTRY_MIN <= mcap <= SWEET_ENTRY_MAX:
        score += 20
        why.append(
            f"Sweet $7–12k entry → 2× ~${tp2x:,.0f} (14–28k zone)"
        )
    elif HEAT_MCAP_MIN <= mcap < BREAKOUT_MIN:
        score += 12
        why.append(
            f"Entry ${mcap:,.0f} · path to ${TARGET_TP_LOW:,.0f}–${TARGET_TP_HIGH:,.0f}"
        )
    elif BREAKOUT_MIN <= mcap <= HEAT_MCAP_MAX:
        score += 16
        why.append(
            f"Breakout ${mcap:,.0f} — climb / near-grad heat"
        )
        if mcap < 40_000:
            score += 6
            why.append("Room toward graduation ~$69k")
        elif mcap < 55_000:
            score += 3
    else:
        score += 4  # flash grad oversized still scorable

    # Bonus if 2× lands inside target zone (early band)
    if mcap < BREAKOUT_MIN:
        if TARGET_TP_LOW <= tp2x <= TARGET_TP_HIGH:
            score += 10
            why.append(f"2× lands in target zone (${tp2x:,.0f})")
        elif tp2x < TARGET_TP_LOW:
            score += 3
        elif mcap < TARGET_TP_LOW:
            score += 5
            why.append(f"Room to ${TARGET_TP_HIGH:,.0f}")

    if bond >= 40:
        score += 10
        why.append(f"Bonding {bond:.0f}% — migration structure")
    elif bond >= 22:
        score += 7
        why.append(f"Bonding {bond:.0f}%")
    elif bond >= 12:
        score += 3
    elif bond >= 8:
        score += 1

    if 4.0 <= age <= 90:
        score += 8
        why.append(f"Age {age:.0f}m — past flash window")
    elif 2.0 <= age < 4.0:
        score += 4
    elif age < 2.0:
        score -= 2
        why.append("Very fresh — sniper risk")

    # --- Momentum (allow mild red if still heated) ---
    if 3 <= m5 <= 80:
        score += 12
        why.append(f"m5 +{m5:.0f}%")
    elif 0 <= m5 < 3:
        score += 4
    elif -12 <= m5 < 0:
        score += 1
        why.append(f"m5 pullback {m5:.0f}%")
    elif m5 > 80:
        score -= 4
        why.append("Parabolic m5 — late")
    elif m5 < -12:
        score -= 10
        why.append(f"m5 {m5:.0f}% weak")

    if h1 >= 10:
        score += 6
    elif h1 <= -25:
        score -= 8

    # ATH retention: heat allows pullbacks
    if ath_ret is not None:
        if ath_ret >= 92:
            score += 10
            why.append(f"Near ATH ({ath_ret:.0f}%)")
        elif ath_ret >= 80:
            score += 7
        elif ath_ret >= ATH_SOFT_FLOOR * 100:
            score += 3
            why.append(f"Pullback {ath_ret:.0f}% ATH — organic watch")
        else:
            score -= 12
            why.append(f"Deep pullback {ath_ret:.0f}% ATH")

    # --- Tx / flow (two-way = organic; one-way = wash) ---
    if tx.get("tilt") == "UP" or tx.get("in_sweet_spot"):
        score += 12
        why.append(tx.get("summary") or "Tx up")
    elif tx.get("tilt") == "DOWN":
        score -= 14
        why.append("Tx tilt DOWN")

    mkt = token.get("market") or {}
    txns = (mkt.get("txns") or {}).get("m5") or {}
    buys = _i(txns.get("buys") or tx.get("buys_m5"))
    sells = _i(txns.get("sells") or tx.get("sells_m5"))
    if buys >= 6 and sells >= 2 and buys / max(sells, 1) <= 4.0:
        score += 10
        why.append(f"Two-way flow {buys}/{sells}")
    elif buys >= 12 and sells == 0:
        score -= 14
        why.append("One-way buys only")

    # --- Mild narrative bonus (not required for heat) ---
    if social.get("influencer_tweet"):
        score += 12
        why.append(social.get("summary") or "Influencer edge")
    elif social.get("has_edge"):
        score += 8
        why.append(social.get("summary") or "Narrative edge")
    elif social.get("narratives"):
        score += 4
        why.append(str((social.get("narratives") or ["meta"])[0]))

    if social.get("real_x"):
        score += 5
    if social.get("namejack_risk") and not social.get("influencer_tweet"):
        score -= 10
        why.append("Name-jack packaging")

    avoid = (
        token.get("avoid")
        or (token.get("safetyReport") or {}).get("avoid")
        or {}
    )
    flags = set(avoid.get("flags") or []) if isinstance(avoid, dict) else set()
    if "fake_twitter" in flags:
        score -= 12
        why.append("Status-link social — packaging risk")
    if "entry_trap_social" in flags or "social_spoof_scam" in flags:
        score -= 20
        why.append("Social trap packaging")

    # --- Book ---
    if hk:
        score += 10
        if bun is not None and bun <= 5:
            score += 8
            why.append(f"Clean book · bundled {bun:.0f}%")
        elif bun is not None and bun <= 10:
            score += 3
        elif bun is not None and bun > 15:
            score -= 12
            why.append(f"Bundled {bun:.0f}%")
    else:
        score -= 6
        why.append("Holders unknown — RISKY")

    # Migration readiness soft boost (what heat is winning on)
    try:
        from services.migration_path import analyze_migration_path

        mp = token.get("migrationPath")
        if not isinstance(mp, dict):
            mp = analyze_migration_path(
                mcap_usd=mcap,
                bonding_progress=bond,
                safety=token.get("safety") or {},
                pair=token.get("market") or {},
                pump=token.get("pumpfun") or {},
                avoid=avoid if isinstance(avoid, dict) else {},
            )
            token["migrationPath"] = mp
        ms = _i(mp.get("score"))
        if ms >= 60:
            score += 10
            why.append(mp.get("summary") or f"Migration path {ms}")
        elif ms >= 45:
            score += 5
    except Exception:
        pass

    if token.get("realtime"):
        score += 5
        why.append("Realtime hit")

    if token.get("enrich_ok") is not True:
        score -= 5
        why.append("Incomplete safety enrich")

    # --- Dev history (launched / migrated / sold) ---
    dev = _dev_profile(token)
    launched = int(dev.get("tokens_launched") or 0)
    migrated = int(dev.get("tokens_migrated") or 0)
    meta["dev"] = dev
    if launched <= 0:
        why.append("Dev history unknown")
    elif launched == 1:
        score += 6
        why.append("First token from this dev")
    elif launched == 2:
        score += 3
        why.append("Dev: 2 launches")
    elif launched <= 5:
        score -= 4
        why.append(f"Dev launched {launched} tokens")
    elif launched < 8:
        score -= 10
        why.append(f"Dev launched {launched} tokens — caution")
    else:
        score -= 18
        why.append(f"Serial deployer: {launched} tokens")

    if migrated >= 2 and launched > 0 and migrated / max(launched, 1) >= 0.25:
        score += 10
        why.append(f"Dev migrated {migrated}/{launched} prior — better track")
    elif migrated == 1 and launched <= 4:
        score += 5
        why.append(f"Dev has {migrated} prior migration")
    elif launched >= 4 and migrated == 0:
        score -= 10
        why.append(f"0/{launched} prior migrations — farm pattern")

    if dev.get("creator_sold"):
        score -= 16
        why.append("Dev sold / not in holders")
    elif _f(dev.get("creator_pct")) >= 3:
        score += 3
        why.append(f"Dev still holds ~{_f(dev.get('creator_pct')):.1f}%")

    if dev.get("this_status") == "migrated":
        score += 4
        why.append("This token already migrated")
    elif dev.get("this_status") == "near_migration":
        score += 8
        why.append(f"Near migration · bond {_f(dev.get('bonding_pct')):.0f}%")
    elif dev.get("this_status") == "climbing":
        score += 4

    score = max(0, min(99, score))
    return score, why, meta


def evaluate_heat(token: dict[str, Any]) -> dict[str, Any]:
    reason = heat_reject_reason(token)
    mcap = extract_mcap_usd(token)
    ath = extract_ath_mcap(token)
    ath_ret = round(100 * mcap / ath, 1) if ath > 0 and mcap > 0 else None
    social = _ensure_social(token)

    if reason:
        return {
            "eligible": False,
            "reject": reason,
            "heat_score": 0,
            "label": LABEL_SKIP,
            "confidence": 0,
            "risk_level": "skip",
            "why": [reason],
            "ath_retention_pct": ath_ret,
            "plan": None,
        }

    score, why, meta = _heat_signals(token)
    hk = holders_known(token)
    bun = _bundled_pct(token)
    enrich_ok = token.get("enrich_ok") is True
    replies = _i(meta.get("replies"))
    ath_ok = ath_ret is None or ath_ret >= ATH_SOFT_FLOOR * 100
    dev = meta.get("dev") or _dev_profile(token)

    # Labels: HEAT = Telegram quality; WARM = watch; RISKY = dust only
    conf = score
    serial_ish = int(dev.get("tokens_launched") or 0) >= 6 and int(
        dev.get("tokens_migrated") or 0
    ) == 0
    in_sweet = SWEET_ENTRY_MIN <= mcap <= SWEET_ENTRY_MAX
    ath_heat_ok = ath_ret is None or ath_ret >= 78
    if (
        score >= 68
        and replies >= 10
        and ath_heat_ok
        and hk
        and enrich_ok
        and (bun is None or bun <= 12)
        and not dev.get("creator_sold")
        and not serial_ish
        and mcap >= HEAT_ALERT_MIN_MCAP
    ):
        label = LABEL_HEAT
        conf = max(conf, 64)
        risk = "elevated"
    elif score >= 54 and ath_ok and mcap >= HEAT_MCAP_MIN and (
        replies >= 6
        or token.get("realtime")
        or social.get("has_edge")
        or in_sweet
        or (hk and _f(token.get("bonding_progress")) >= 18)
    ):
        label = LABEL_WARM
        conf = min(conf, 60)
        risk = "high"
        if dev.get("creator_sold") or serial_ish:
            label = LABEL_RISKY
            conf = min(conf, 48)
            risk = "very_high"
    elif score >= 46 and ath_ok and mcap >= HEAT_MCAP_MIN:
        label = LABEL_RISKY
        conf = min(conf, 48)
        risk = "very_high"
        why = why + ["Smaller size — path to 14–28k uncertain"]
    else:
        return {
            "eligible": False,
            "reject": "not enough heat for $7k→14–28k path",
            "heat_score": score,
            "label": LABEL_SKIP,
            "confidence": min(conf, 40),
            "risk_level": "skip",
            "why": why[:5] or ["Weak heat / wrong mcap band"],
            "ath_retention_pct": ath_ret,
            "dev": dev,
            "plan": None,
        }

    if not hk:
        label = LABEL_RISKY if label == LABEL_HEAT else label
        if label == LABEL_WARM and score < 62:
            label = LABEL_RISKY
        conf = min(conf, 50)
        risk = "very_high"

    if not enrich_ok:
        label = LABEL_RISKY if label != LABEL_SKIP else label
        conf = min(conf, 45)
        risk = "very_high"

    tp2x = round(mcap * 2.0, 0) if mcap else None
    # Prefer target-zone TP when 2× overshoots zone for breakouts
    zone_tp = TARGET_TP_HIGH if mcap and mcap < TARGET_TP_HIGH else tp2x
    plan = {
        "entry_usd": round(mcap, 0) if mcap else None,
        "take_profit_2x_usd": tp2x,
        "target_zone_usd": [TARGET_TP_LOW, TARGET_TP_HIGH],
        "target_tp_usd": round(zone_tp, 0) if zone_tp else None,
        "invalidation_usd": round(mcap * 0.75, 0) if mcap else None,
        "size_advice": (
            f"Entry ≥$7k · aim for ${TARGET_TP_LOW:,.0f}–${TARGET_TP_HIGH:,.0f}. "
            f"Many still dump — size small. Cut on −25%."
        ),
        "rule": (
            "Organic Heat $7k→14–28k path (optimized). Not capital-safe moons. "
            "Book partials into the target zone."
        ),
    }

    return {
        "eligible": True,
        "reject": None,
        "heat_score": score,
        "label": label,
        "confidence": max(0, min(99, int(conf))),
        "risk_level": risk,
        "why": why[:8],
        "ath_retention_pct": ath_ret,
        "holders_known": hk,
        "bundle_pct": bun,
        "replies": replies,
        "target_2x_usd": tp2x,
        "target_zone_usd": [TARGET_TP_LOW, TARGET_TP_HIGH],
        "dev": dev,
        "narrative": social.get("summary") or "",
        "badges": social.get("badges") or [],
        "plan": plan,
        "meta": meta,
    }


def filter_and_rank_heat(
    tokens: list[dict[str, Any]],
    *,
    min_score: int = 44,
    limit: int = 16,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tokens:
        if not isinstance(t, dict):
            continue
        ev = evaluate_heat(t)
        if not ev.get("eligible"):
            continue
        if int(ev.get("heat_score") or 0) < min_score:
            continue
        row = dict(t)
        row["heat"] = ev
        row["heat_score"] = ev["heat_score"]
        row["heat_label"] = ev["label"]
        row["confidence"] = ev["confidence"]
        row["risk_level"] = ev.get("risk_level")
        row["ath_retention_pct"] = ev.get("ath_retention_pct")
        row["dev"] = ev.get("dev")
        out.append(row)

    rank = {LABEL_HEAT: 0, LABEL_WARM: 1, LABEL_RISKY: 2}

    def _heat_rank_key(x: dict[str, Any]) -> tuple:
        m = extract_mcap_usd(x)
        bond = _f(x.get("bonding_progress"))
        # Prefer HEAT, then climb structure, then score
        stage = 0 if bond >= 30 or m >= 18_000 else (1 if m >= 10_000 else 2)
        return (
            rank.get(x.get("heat_label") or "", 9),
            stage,
            -(x.get("heat_score") or 0),
            -(x.get("confidence") or 0),
            -_i((x.get("heat") or {}).get("replies")),
        )

    out.sort(key=_heat_rank_key)
    return out[:limit]


def heat_card_from_coin(coin: dict, *, source: str = "pump.fun") -> dict[str, Any] | None:
    """Pre-enrich card — wider band than moon/snipe."""
    from config import PADRE_TRADE_URL
    from services.avoid_filters import analyze_avoid_flags
    from services.pumpfun import PumpFunClient

    mint = (coin.get("mint") or "").strip()
    if not mint or mint in BLOCKED_MINTS:
        return None
    if coin.get("is_banned"):
        return None
    mcap = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or 0)
    bond = PumpFunClient.bonding_progress(coin)
    age = PumpFunClient.coin_age_minutes(coin)
    complete = bool(coin.get("complete"))
    # Allow just-graduated flash on heat (early mega catch)
    if complete:
        if age > FLASH_GRAD_MAX_AGE_MIN or mcap > FLASH_GRAD_MAX_MCAP:
            return None
        if mcap < HEAT_MCAP_MIN * 0.95:
            return None
    else:
        if mcap < HEAT_MCAP_MIN * 0.95 or mcap > HEAT_MCAP_MAX * 1.05:
            return None
        if age < MIN_AGE_MIN * 0.7 or age > MAX_AGE_MIN + 20:
            return None
    # Hard dump prefilter
    if ath >= 3_000 and mcap > 0 and mcap < ath * ATH_HARD_DUMP:
        return None

    avoid = analyze_avoid_flags(
        safety={
            "mint": mint,
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
            "description": coin.get("description"),
        },
        pump=coin,
        mint=mint,
    )
    # Pre-card: drop capital hard_avoid; soft packaging alone may continue
    flags = set(avoid.get("flags") or [])
    hard, _ = is_hard_avoid({"avoid": avoid})
    if mint in BLOCKED_MINTS:
        return None
    if hard and (not flags or not flags <= HEAT_SOFT_PACKAGING_FLAGS):
        return None

    social = analyze_social_narrative(
        pump_coin=coin,
        name=coin.get("name") or "",
        symbol=coin.get("symbol") or "",
        description=coin.get("description") or "",
    )
    return {
        "tokenAddress": mint,
        "chainId": "solana",
        "name": coin.get("name") or "Unknown",
        "symbol": coin.get("symbol") or "?",
        "icon": coin.get("image_uri"),
        "mcap_usd": mcap,
        "ath_mcap": ath or None,
        "bonding_progress": round(bond, 1),
        "age_minutes": round(age, 1),
        "pumpfun": {
            "usd_market_cap": mcap,
            "ath_market_cap": ath or None,
            "bonding_progress": bond,
            "twitter": coin.get("twitter"),
            "telegram": coin.get("telegram"),
            "website": coin.get("website"),
            "description": coin.get("description"),
            "reply_count": coin.get("reply_count", 0),
            "creator": coin.get("creator"),
            "image_uri": coin.get("image_uri"),
        },
        "safetyReport": {"avoid": avoid},
        "avoid": avoid,
        "socialSignals": social,
        "pump_url": f"https://pump.fun/coin/{mint}",
        "padre_url": f"{PADRE_TRADE_URL}/trade/solana/{mint}",
        "source": source,
        "mode": "organic_heat",
    }
