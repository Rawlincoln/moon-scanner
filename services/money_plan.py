"""Money-mode trade plan: entry / stop / TP / invalidation rules.

Used by Telegram alerts + trade journal. Pure functions (no I/O).
"""

from __future__ import annotations

from typing import Any

from config import (
    MONEY_INVALID_DROP_PCT,
    MONEY_INVALID_NO_MOVE_PCT,
    MONEY_MAX_HOLD_MIN,
    MONEY_RISK_PCT_HINT,
    MONEY_STOP_PCT,
    MONEY_TP1_PCT,
    MONEY_TP2_PCT,
    TELEGRAM_MONEY_MODE,
)


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def entry_mcap(token: dict[str, Any]) -> float:
    return _f(token.get("mcap_usd") or token.get("mcap") or token.get("entry_mcap"))


def build_money_plan(
    kind: str,
    token: dict[str, Any],
    *,
    entry: float | None = None,
) -> dict[str, Any]:
    """Build a capital plan for an alerted pick.

    All levels are **mcap USD** (same unit as scanner), not per-token price.
    User should translate risk as % of bankroll, not SOL size blindly.
    """
    e = float(entry) if entry is not None and entry > 0 else entry_mcap(token)
    kind_l = (kind or "").lower().strip()
    stop_pct = MONEY_STOP_PCT
    tp1_pct = MONEY_TP1_PCT
    tp2_pct = MONEY_TP2_PCT
    # Snipes already aim for ~2× — keep TP2 at 2×, slightly tighter stop
    if kind_l in ("snipe", "snipes"):
        stop_pct = min(stop_pct, 0.16)
        snipe_tp = _f(
            token.get("target_2x_usd")
            or (token.get("snipe") or {}).get("target_2x_usd")
        )
        if snipe_tp > e > 0:
            tp2_pct = (snipe_tp / e) - 1.0
            tp1_pct = min(tp1_pct, tp2_pct * 0.5)

    stop_mcap = e * (1.0 - stop_pct) if e > 0 else 0.0
    tp1_mcap = e * (1.0 + tp1_pct) if e > 0 else 0.0
    tp2_mcap = e * (1.0 + tp2_pct) if e > 0 else 0.0
    invalid_mcap = e * (1.0 - MONEY_INVALID_DROP_PCT) if e > 0 else 0.0
    no_move_mcap = e * (1.0 + MONEY_INVALID_NO_MOVE_PCT) if e > 0 else 0.0

    r_to_stop = stop_pct  # risk fraction
    r_reward_tp1 = tp1_pct / stop_pct if stop_pct > 0 else 0.0

    return {
        "money_mode": TELEGRAM_MONEY_MODE,
        "kind": kind_l,
        "entry_mcap": round(e, 2) if e else None,
        "stop_mcap": round(stop_mcap, 2) if stop_mcap else None,
        "stop_pct": round(stop_pct * 100, 1),
        "tp1_mcap": round(tp1_mcap, 2) if tp1_mcap else None,
        "tp1_pct": round(tp1_pct * 100, 1),
        "tp2_mcap": round(tp2_mcap, 2) if tp2_mcap else None,
        "tp2_pct": round(tp2_pct * 100, 1),
        "invalid_if_below_mcap": round(invalid_mcap, 2) if invalid_mcap else None,
        "invalid_drop_pct": round(MONEY_INVALID_DROP_PCT * 100, 1),
        "need_move_by_mcap": round(no_move_mcap, 2) if no_move_mcap else None,
        "need_move_pct": round(MONEY_INVALID_NO_MOVE_PCT * 100, 1),
        "max_hold_min": MONEY_MAX_HOLD_MIN,
        "risk_pct_hint": MONEY_RISK_PCT_HINT,
        "r_multiple_tp1": round(r_reward_tp1, 2),
        "rules": [
            f"Entry ≈ mcap ${_fmt(e)} (scan reference — your fill may differ)",
            f"STOP −{stop_pct * 100:.0f}% → ${_fmt(stop_mcap)} (hard)",
            f"TP1 +{tp1_pct * 100:.0f}% → ${_fmt(tp1_mcap)} (sell ~50%)",
            f"TP2 +{tp2_pct * 100:.0f}% → ${_fmt(tp2_mcap)} (trail rest)",
            f"INVALID if mcap < ${_fmt(invalid_mcap)} (−{MONEY_INVALID_DROP_PCT * 100:.0f}% from alert)",
            f"TIME-STOP: if not +{MONEY_INVALID_NO_MOVE_PCT * 100:.0f}% within {MONEY_MAX_HOLD_MIN:.0f}m → exit",
            f"Size ≤ {MONEY_RISK_PCT_HINT:.1f}% bankroll risk to stop",
        ],
    }


def check_invalidation(
    plan: dict[str, Any],
    *,
    current_mcap: float,
    alert_age_min: float,
) -> tuple[bool, str | None]:
    """Return (invalid, reason) for an open alerted plan."""
    e = _f(plan.get("entry_mcap"))
    cur = _f(current_mcap)
    if e <= 0 or cur <= 0:
        return False, None

    inv_below = _f(plan.get("invalid_if_below_mcap"))
    if inv_below <= 0:
        inv_below = e * (1.0 - MONEY_INVALID_DROP_PCT)
    if cur < inv_below:
        drop = (1.0 - cur / e) * 100
        return True, f"mcap dropped −{drop:.0f}% to ${_fmt(cur)} (cancel threshold ${_fmt(inv_below)})"

    stop_m = _f(plan.get("stop_mcap"))
    if stop_m > 0 and cur <= stop_m:
        return True, f"hit stop zone ${_fmt(cur)} ≤ stop ${_fmt(stop_m)}"

    max_hold = _f(plan.get("max_hold_min"), MONEY_MAX_HOLD_MIN)
    need = _f(plan.get("need_move_by_mcap"))
    if need <= 0:
        need = e * (1.0 + MONEY_INVALID_NO_MOVE_PCT)
    if alert_age_min >= max_hold and cur < need:
        return (
            True,
            f"time-stop {alert_age_min:.0f}m without +{MONEY_INVALID_NO_MOVE_PCT * 100:.0f}% "
            f"(now ${_fmt(cur)}, needed ${_fmt(need)})",
        )
    return False, None


def classify_exit(
    plan: dict[str, Any],
    *,
    exit_mcap: float,
    peak_mcap: float | None = None,
) -> dict[str, Any]:
    """Classify a closed trade vs plan levels."""
    e = _f(plan.get("entry_mcap"))
    x = _f(exit_mcap)
    peak = max(_f(peak_mcap), x, e)
    if e <= 0 or x <= 0:
        return {"outcome": "unknown", "multiple": 0.0, "r_multiple": None}

    mult = x / e
    peak_mult = peak / e if e else 0
    stop_m = _f(plan.get("stop_mcap"))
    tp1 = _f(plan.get("tp1_mcap"))
    tp2 = _f(plan.get("tp2_mcap"))
    stop_pct = _f(plan.get("stop_pct"), MONEY_STOP_PCT * 100) / 100.0 or MONEY_STOP_PCT

    if peak_mult >= 1.0 + MONEY_TP2_PCT or (tp2 > 0 and peak >= tp2):
        outcome = "tp2"
    elif peak_mult >= 1.0 + MONEY_TP1_PCT or (tp1 > 0 and peak >= tp1):
        outcome = "tp1"
    elif stop_m > 0 and x <= stop_m * 1.02:
        outcome = "stop"
    elif mult < 0.85:
        outcome = "loss"
    elif mult >= 1.08:
        outcome = "win_small"
    else:
        outcome = "scratch"

    # R: risk unit = stop distance
    r = (x - e) / (e * stop_pct) if e * stop_pct > 0 else None
    return {
        "outcome": outcome,
        "multiple": round(mult, 3),
        "peak_multiple": round(peak_mult, 3),
        "r_multiple": round(r, 2) if r is not None else None,
    }


def _fmt(n: float) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v >= 1e6:
        return f"{v / 1e6:.2f}M"
    if v >= 1e3:
        return f"{v / 1e3:.1f}k"
    return f"{v:.0f}"
