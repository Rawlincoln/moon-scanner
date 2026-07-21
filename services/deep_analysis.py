"""Elaborate per-token verdict — dump risk, tx interest, migration quality, buy gate.

After user losses: default is SKIP/WATCH. BUY only when multiple gates pass.
"""

from __future__ import annotations

from typing import Any

from services.runner_radar import extract_ath_mcap, is_crashed_runner
from services.tx_activity import score_tx_activity


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def build_deep_analysis(
    *,
    mcap: float,
    safety: dict | None = None,
    pair: dict | None = None,
    pump: dict | None = None,
    alpha: dict | None = None,
    migration: dict | None = None,
    trade_plan: dict | None = None,
    avoid: dict | None = None,
    smart_money: dict | None = None,
    social: dict | None = None,
) -> dict[str, Any]:
    safety = safety or {}
    pair = pair or {}
    pump = pump or pair.get("pumpfun") or {}
    alpha = alpha or {}
    migration = migration or {}
    trade_plan = trade_plan or {}
    avoid = avoid or safety.get("avoid") or {}
    smart_money = smart_money or {}
    social = social or {}

    ath = extract_ath_mcap({"ath_mcap": pump.get("ath_market_cap"), "pumpfun": pump})
    peak = max(ath, mcap)
    dumped, dump_why = is_crashed_runner(
        {
            "mcap_usd": mcap,
            "ath_mcap": ath,
            "peak_mcap": ath,
            "priceChange": pair.get("priceChange") or {},
            "safetyReport": {"avoid": avoid},
            "pumpfun": pump,
        }
    )
    # Near-ATH retention (must be within 25% of ATH to consider buy)
    near_ath = ath <= 0 or (mcap > 0 and mcap >= ath * 0.75)
    dump_pct = round((1 - mcap / ath) * 100, 1) if ath > 0 and mcap > 0 else 0.0

    tx = alpha.get("txActivity") or score_tx_activity(pair=pair, pump=pump)
    bond = _f(migration.get("bonding_pct") or pump.get("bonding_progress"))
    mig_score = int(migration.get("score") or 0)
    alpha_score = int(alpha.get("score") or 0)
    alpha_tier = str(alpha.get("tier") or "")
    fp = alpha.get("megaFingerprint") or {}
    fp_score = int(fp.get("score") or 0)

    checklist: list[dict[str, Any]] = []
    risks: list[str] = []
    why: list[str] = []

    def gate(ok: bool, label: str, detail: str = "") -> None:
        checklist.append({"ok": ok, "label": label, "detail": detail})
        if ok:
            why.append(f"✓ {label}" + (f" — {detail}" if detail else ""))
        else:
            risks.append(f"✗ {label}" + (f" — {detail}" if detail else ""))

    gate(not dumped, "Not dumped", dump_why or f"−{dump_pct:.0f}% from ATH" if ath else "no ATH yet")
    gate(near_ath, "Near ATH", f"${mcap:,.0f} vs ATH ${ath:,.0f}" if ath else "n/a")
    gate(not avoid.get("hard_avoid"), "Not hard-avoid", avoid.get("summary") or "")
    gate(not avoid.get("avoid") or not avoid.get("hard_avoid"), "Avoid filters clean", avoid.get("summary") or "ok")
    gate(tx.get("in_sweet_spot") or tx.get("tilt") == "UP", "Tx interest", tx.get("summary") or "")
    gate(int(tx.get("sells_m5") or 0) >= 4, "Two-way sells present", f"{tx.get('buys_m5')}B/{tx.get('sells_m5')}S")
    gate(mig_score >= 65 or bond >= 45, "Migration quality", f"score {mig_score} · bond {bond:.0f}%")
    gate(alpha_score >= 68 or alpha_tier in ("MEGA_MOON", "MOON_SETUP"), "Structure score", f"{alpha_tier} {alpha_score}")
    gate(mcap >= 15_000, "Min mcap $15k+", f"${mcap:,.0f}")
    gate(bond >= 40, "Near-migration bond ≥40%", f"{bond:.0f}%")
    gate(not safety.get("is_honeypot") and not safety.get("rugged"), "Not honeypot/rugged")
    gate(fp_score >= 55 or social.get("highlight") or alpha_tier in ("MEGA_MOON", "MOON_SETUP"), "Narrative/structure edge", f"FP {fp_score}")

    gates_ok = sum(1 for c in checklist if c["ok"])
    gates_total = len(checklist)

    # BUY only if almost all gates pass (capital protection after user losses)
    buy_ready = (
        not dumped
        and near_ath
        and not avoid.get("hard_avoid")
        and not avoid.get("avoid")
        and (tx.get("in_sweet_spot") or (tx.get("tilt") == "UP" and int(tx.get("score") or 0) >= 65))
        and int(tx.get("sells_m5") or 0) >= 4
        and mcap >= 15_000
        and bond >= 42
        and mig_score >= 65
        and alpha_score >= 70
        and alpha_tier in ("MEGA_MOON", "MOON_SETUP", "ALPHA")
        and not safety.get("is_honeypot")
        and not safety.get("rugged")
        and gates_ok >= 10
    )

    if dumped or avoid.get("hard_avoid") or safety.get("is_honeypot"):
        verdict = "SKIP"
        conf = 90
        pos = "Do not buy. Already dumped or hard-avoid. Capital preservation."
    elif buy_ready:
        verdict = "BUY"
        conf = min(88, 50 + gates_ok * 3 + int(tx.get("score") or 0) // 5)
        pos = (
            "Small size only (e.g. ≤0.05–0.1 SOL). Scale out into strength. "
            "Hard stop if −25% from entry or fresh dump from local high."
        )
    elif gates_ok >= 7 and not dumped and near_ath and mcap >= 12_000:
        verdict = "WATCH"
        conf = 40 + gates_ok * 2
        pos = "Do not FOMO. Wait for missing gates (tx sweet + near ATH + structure)."
    else:
        verdict = "SKIP"
        conf = 55
        pos = "Skip or dust only. Conditions do not support a buy after prior dump losses."

    # Force trade plan alignment note
    if trade_plan.get("action") == "ENTER" and verdict != "BUY":
        why.append("Learned ENTER overridden — multi-gate buy not satisfied")

    return {
        "verdict": verdict,
        "confidence": conf,
        "gates_passed": gates_ok,
        "gates_total": gates_total,
        "buy_ready": buy_ready,
        "position_advice": pos,
        "dump": {
            "is_dumped": dumped,
            "reason": dump_why,
            "ath_mcap": round(ath) if ath else None,
            "mcap": round(mcap),
            "dump_pct_from_ath": dump_pct,
            "near_ath": near_ath,
            "retain_frac": round(mcap / ath, 3) if ath > 0 else None,
        },
        "tx_interest": {
            "score": tx.get("score"),
            "zone": tx.get("zone"),
            "tilt": tx.get("tilt"),
            "total_m5": tx.get("total_m5"),
            "buys_m5": tx.get("buys_m5"),
            "sells_m5": tx.get("sells_m5"),
            "ratio": tx.get("buy_ratio_m5"),
            "in_sweet_spot": tx.get("in_sweet_spot"),
            "sweet_band": tx.get("sweet_band"),
            "summary": tx.get("summary"),
        },
        "migration": {
            "lane": migration.get("lane"),
            "score": mig_score,
            "bonding_pct": bond,
            "to_graduation_usd": migration.get("to_graduation_usd"),
            "recommend": migration.get("recommend"),
            "summary": migration.get("summary"),
        },
        "structure": {
            "alpha_tier": alpha_tier,
            "alpha_score": alpha_score,
            "fingerprint": fp_score,
            "fingerprint_tier": fp.get("tier"),
            "mega_stack": alpha.get("mega_stack"),
        },
        "checklist": checklist,
        "why": why[:12],
        "risks": risks[:12],
        "summary": (
            f"{verdict} ({conf}%) · {gates_ok}/{gates_total} gates · "
            f"tx {tx.get('zone')} {tx.get('total_m5')} · bond {bond:.0f}% · "
            f"{'DUMPED' if dumped else 'near ATH' if near_ath else f'−{dump_pct:.0f}% ATH'}"
        ),
    }
