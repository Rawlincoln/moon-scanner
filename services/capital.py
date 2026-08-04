"""Bankroll, position sizing, and session risk gates.

Complete money system layer — no alerts without size + room under risk limits.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from config import (
    BANKROLL_USD,
    MAX_DAILY_LOSS_R,
    MAX_DAILY_PROFIT_R,
    MAX_OPEN_TRADES,
    MAX_TRADES_PER_DAY,
    MONEY_SYSTEM_ARMED,
    RISK_PER_TRADE_PCT,
    SOL_USD,
    TELEGRAM_MONEY_MODE,
)
from services.money_plan import build_money_plan


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _day_start_ts() -> float:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return start.timestamp()


def size_position(
    *,
    entry_mcap: float,
    stop_pct: float | None = None,
    bankroll: float | None = None,
    risk_pct: float | None = None,
    sol_usd: float | None = None,
) -> dict[str, Any]:
    """Risk-based size: lose risk_usd if stop hits.

    For memecoins we size in **USD notional** (what you'd spend), not token count.
    stop_pct is fraction e.g. 0.18.
    """
    br = _f(bankroll, BANKROLL_USD)
    rp = _f(risk_pct, RISK_PER_TRADE_PCT)
    sp = _f(stop_pct, 0.18)
    sol = _f(sol_usd, SOL_USD)
    if sp <= 0:
        sp = 0.18
    if br <= 0:
        br = BANKROLL_USD
    risk_usd = br * (rp / 100.0)
    # If stop is −18%, position notional ≈ risk / 0.18
    size_usd = risk_usd / sp if sp > 0 else 0.0
    # Cap single position at 25% bankroll (never yolo full bag)
    size_usd = min(size_usd, br * 0.25)
    size_sol = (size_usd / sol) if sol > 0 else None
    return {
        "bankroll_usd": round(br, 2),
        "risk_pct": round(rp, 2),
        "risk_usd": round(risk_usd, 2),
        "stop_pct": round(sp * 100, 1),
        "size_usd": round(size_usd, 2),
        "size_sol": round(size_sol, 4) if size_sol is not None else None,
        "sol_usd": sol,
        "entry_mcap": round(entry_mcap, 2) if entry_mcap else None,
        "max_loss_if_stopped_usd": round(risk_usd, 2),
        "rule": f"Risk ${risk_usd:.2f} ({rp}% of ${br:.0f}) to −{sp * 100:.0f}% stop → size ${size_usd:.2f}",
    }


def enrich_plan_with_size(kind: str, token: dict[str, Any]) -> dict[str, Any]:
    plan = build_money_plan(kind, token)
    stop_pct = _f(plan.get("stop_pct"), 18.0) / 100.0
    entry = _f(plan.get("entry_mcap"))
    sizing = size_position(entry_mcap=entry, stop_pct=stop_pct)
    plan["sizing"] = sizing
    plan["size_usd"] = sizing["size_usd"]
    plan["risk_usd"] = sizing["risk_usd"]
    plan["size_sol"] = sizing["size_sol"]
    plan["rules"] = list(plan.get("rules") or []) + [
        sizing["rule"],
        f"Buy ≈ ${sizing['size_usd']:.2f}"
        + (f" (~{sizing['size_sol']:.3f} SOL)" if sizing.get("size_sol") else ""),
    ]
    return plan


def session_stats(journal: Any) -> dict[str, Any]:
    """Today's open/closed R stats from journal."""
    day0 = _day_start_ts()
    open_rows = journal.active_open() if journal else []
    all_rows = journal.list_trades(limit=200) if journal else []
    today_opened = [r for r in all_rows if _f(r.get("opened_at")) >= day0]
    today_closed = [
        r
        for r in all_rows
        if r.get("status") in ("closed", "invalid")
        and _f(r.get("closed_at") or r.get("opened_at")) >= day0
    ]
    r_sum = 0.0
    r_n = 0
    for r in today_closed:
        if r.get("r_multiple") is not None:
            r_sum += _f(r.get("r_multiple"))
            r_n += 1
        elif r.get("multiple") is not None and r.get("plan_json"):
            # approximate from multiple if needed
            try:
                import json

                plan = json.loads(r["plan_json"] or "{}")
                sp = _f(plan.get("stop_pct"), 18) / 100.0 or 0.18
                mult = _f(r.get("multiple"))
                r_sum += (mult - 1.0) / sp
                r_n += 1
            except Exception:
                pass
    wins = sum(
        1
        for r in today_closed
        if str(r.get("outcome") or "") in ("tp1", "tp2", "win_small")
        or _f(r.get("multiple")) >= 1.15
    )
    losses = sum(
        1
        for r in today_closed
        if str(r.get("outcome") or "") in ("stop", "loss", "invalid")
        or (_f(r.get("multiple")) and _f(r.get("multiple")) < 0.95)
    )
    return {
        "day_start_utc": day0,
        "open_count": len(open_rows),
        "opened_today": len(today_opened),
        "closed_today": len(today_closed),
        "wins_today": wins,
        "losses_today": losses,
        "day_r": round(r_sum, 2) if r_n else 0.0,
        "day_r_n": r_n,
        "open_trades": [
            {
                "id": r.get("id"),
                "symbol": r.get("symbol"),
                "feed": r.get("feed"),
                "label": r.get("label"),
                "entry_mcap": r.get("entry_mcap"),
                "peak_mcap": r.get("peak_mcap"),
                "size_usd": r.get("size_usd"),
                "opened_at": r.get("opened_at"),
            }
            for r in open_rows
        ],
    }


def can_open_trade(journal: Any, *, kind: str = "moon") -> tuple[bool, str]:
    """Gate new money alerts / journal opens."""
    if not MONEY_SYSTEM_ARMED:
        return False, "system disarmed (MONEY_SYSTEM_ARMED=0) — scan only"
    if not TELEGRAM_MONEY_MODE and kind not in ("moon", "snipe"):
        # still allow non-money if mode off
        pass
    stats = session_stats(journal)
    if stats["open_count"] >= MAX_OPEN_TRADES:
        return (
            False,
            f"max open trades {stats['open_count']}/{MAX_OPEN_TRADES} — manage or close first",
        )
    if stats["opened_today"] >= MAX_TRADES_PER_DAY:
        return (
            False,
            f"max trades today {stats['opened_today']}/{MAX_TRADES_PER_DAY} — session cap",
        )
    if stats["day_r"] <= -abs(MAX_DAILY_LOSS_R):
        return (
            False,
            f"daily loss limit hit ({stats['day_r']}R ≤ −{MAX_DAILY_LOSS_R}R) — STOP trading today",
        )
    if stats["day_r"] >= MAX_DAILY_PROFIT_R and kind not in ("snipe",):
        return (
            False,
            f"daily profit lock {stats['day_r']}R ≥ +{MAX_DAILY_PROFIT_R}R — only SNIPE or stop",
        )
    return True, "ok"


def desk_snapshot(journal: Any) -> dict[str, Any]:
    """Full money desk state for UI / Telegram."""
    stats = session_stats(journal)
    ok, reason = can_open_trade(journal, kind="moon")
    jsum = journal.summary() if journal else {}
    risk_usd = BANKROLL_USD * (RISK_PER_TRADE_PCT / 100.0)
    return {
        "armed": MONEY_SYSTEM_ARMED,
        "money_mode": TELEGRAM_MONEY_MODE,
        "bankroll_usd": BANKROLL_USD,
        "risk_per_trade_pct": RISK_PER_TRADE_PCT,
        "risk_per_trade_usd": round(risk_usd, 2),
        "max_daily_loss_r": MAX_DAILY_LOSS_R,
        "max_daily_profit_r": MAX_DAILY_PROFIT_R,
        "max_open_trades": MAX_OPEN_TRADES,
        "max_trades_per_day": MAX_TRADES_PER_DAY,
        "sol_usd": SOL_USD,
        "can_open": ok,
        "can_open_reason": reason,
        "session": stats,
        "expectancy": {
            "sample_n": jsum.get("sample_n"),
            "win_rate_pct": jsum.get("win_rate_pct"),
            "expectancy_r": jsum.get("expectancy_r"),
            "avg_multiple": jsum.get("avg_multiple"),
            "wins": jsum.get("wins"),
            "losses": jsum.get("losses"),
        },
        "playbook": [
            "Only MOON + SNIPE alerts (money mode)",
            f"Risk {RISK_PER_TRADE_PCT}% bankroll (${risk_usd:.2f}) per trade to stop",
            f"Max {MAX_OPEN_TRADES} open · {MAX_TRADES_PER_DAY}/day",
            f"Daily stop −{MAX_DAILY_LOSS_R}R · profit lock +{MAX_DAILY_PROFIT_R}R",
            "TP1 +50% scale 50% · move stop to BE · TP2 full or trail",
            "Never average down · never hold past invalid/CANCEL",
            "Paper until expectancy_r > 0 over ≥20 closed trades",
        ],
        "ts": time.time(),
    }
