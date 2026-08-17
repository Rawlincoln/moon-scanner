"""Live position manager — TP1/TP2/stop/trail + daily report.

Replaces pure cancel-only invalidation with full lifecycle management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from config import (
    MONEY_INVALIDATE_INTERVAL_SEC,
    MONEY_SYSTEM_ARMED,
    POSITION_MANAGER_INTERVAL_SEC,
    TELEGRAM_MONEY_MODE,
    TRAIL_AFTER_TP1,
    MONEY_DAILY_REPORT_UTC_HOUR,
)
from services.alert_invalidation import fetch_mcap
from services.capital import desk_snapshot
from services.money_plan import check_invalidation, classify_exit
from services.trade_journal import get_journal

logger = logging.getLogger("moon-scanner.positions")

_last: dict[str, Any] = {
    "ts": 0.0,
    "checked": 0,
    "events": [],
    "error": None,
}
_last_daily_report_day: str = ""


def status() -> dict[str, Any]:
    return {
        "money_mode": TELEGRAM_MONEY_MODE,
        "armed": MONEY_SYSTEM_ARMED,
        "interval_sec": POSITION_MANAGER_INTERVAL_SEC,
        "last": dict(_last),
        "open": len(get_journal().active_open()),
    }


def _plan_of(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row.get("plan_json") or "{}")
    except Exception:
        return {"entry_mcap": row.get("entry_mcap")}


def _mgmt(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row.get("mgmt_json") or "{}")
    except Exception:
        return {}


async def _notify(msg: str) -> None:
    try:
        from services.telegram_alerts import send_telegram

        await send_telegram(msg)
    except Exception as exc:
        logger.debug("position notify failed: %s", exc)


def _esc(s: Any) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt_usd(n: Any) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"${v / 1e3:.1f}k"
    return f"${v:.0f}"


async def manage_open_positions() -> dict[str, Any]:
    """One cycle: update peaks, fire TP/stop/invalid, close journal rows."""
    journal = get_journal()
    open_rows = journal.active_open()
    checked = 0
    events: list[dict[str, Any]] = []

    for row in open_rows[:40]:
        mint = str(row.get("mint") or "")
        tid = int(row["id"])
        if not mint:
            continue
        checked += 1
        mcap = await fetch_mcap(mint)
        plan = _plan_of(row)
        mgmt = _mgmt(row)
        entry = float(plan.get("entry_mcap") or row.get("entry_mcap") or 0)
        age_min = (time.time() - float(row.get("opened_at") or time.time())) / 60.0
        max_hold = float(plan.get("max_hold_min") or 45)
        # No print N cycles or past hold → force close (don't lock desk forever)
        if mcap is None:
            misses = int(mgmt.get("mcap_misses") or 0) + 1
            mgmt["mcap_misses"] = misses
            journal.update_management(tid, mgmt=mgmt)
            if misses >= 8 or age_min >= max_hold * 1.5:
                journal.close_trade(
                    tid,
                    exit_mcap=float(row.get("last_mcap") or entry or 0) or None,
                    notes="stale — no mcap feed (migrated / API down)",
                    force_outcome="stale_exit",
                )
                events.append({"id": tid, "event": "stale_exit", "mint": mint[:8]})
                await _notify(
                    f"⚠ <b>STALE EXIT</b> ${_esc(row.get('symbol') or '?')}\n"
                    f"No mcap for {misses} cycles — closed to free desk slot\n"
                    f"<code>{_esc(mint)}</code>"
                )
            await asyncio.sleep(0.1)
            continue
        mgmt["mcap_misses"] = 0
        peak = max(float(row.get("peak_mcap") or 0), float(mcap))
        tp1 = float(plan.get("tp1_mcap") or 0)
        tp2 = float(plan.get("tp2_mcap") or 0)
        stop_m = float(plan.get("stop_mcap") or 0)
        # After TP1, trail stop to entry (breakeven)
        if TRAIL_AFTER_TP1 and mgmt.get("tp1_hit") and entry > 0:
            stop_m = max(stop_m, entry)

        event: str | None = None
        close = False
        outcome = None
        notes = ""

        # Priority: live mcap only (peak is MFE for stats — never book TP on a wick)
        # stop → TP2 → invalid → TP1 scale
        if stop_m > 0 and mcap <= stop_m:
            event = "stop"
            close = True
            outcome = "stop" if not mgmt.get("tp1_hit") else "be_stop"
            notes = "Stop / trail hit — exit"
        elif tp2 > 0 and mcap >= tp2 and not mgmt.get("tp2_hit"):
            event = "tp2"
            close = True
            outcome = "tp2"
            notes = "TP2 hit (live mcap) — close remainder"
            mgmt["tp2_hit"] = True
            mgmt["tp1_hit"] = True
        else:
            invalid, reason = check_invalidation(
                plan, current_mcap=float(mcap), alert_age_min=age_min
            )
            # Don't invalid-cancel if already past TP1 (we're in profit management)
            if invalid and not mgmt.get("tp1_hit"):
                event = "invalid"
                close = True
                outcome = "invalid"
                notes = reason or "setup invalid"
            elif tp1 > 0 and mcap >= tp1 and not mgmt.get("tp1_hit"):
                event = "tp1"
                mgmt["tp1_hit"] = True
                mgmt["tp1_at"] = time.time()
                mgmt["tp1_mcap"] = float(mcap)
                notes = "TP1 hit (live mcap) — sell ~50%, move stop to BE"

        # Persist peak + mgmt always
        journal.update_management(
            tid,
            peak_mcap=peak,
            last_mcap=float(mcap),
            mgmt=mgmt,
        )

        if event:
            sym = row.get("symbol") or "?"
            size = row.get("size_usd")
            size_s = f" · size ${_esc(size)}" if size else ""
            if event == "tp1":
                msg = (
                    f"🎯 <b>TP1 HIT</b> ${_esc(sym)}{size_s}\n"
                    f"mcap {_fmt_usd(mcap)} ≥ TP1 {_fmt_usd(tp1)}\n"
                    f"• Sell ~50% now\n"
                    f"• Move STOP to BE (entry {_fmt_usd(entry)})\n"
                    f"• Let rest run to TP2 {_fmt_usd(tp2)}\n"
                    f"<code>{_esc(mint)}</code>"
                )
                await _notify(msg)
            elif event == "tp2":
                msg = (
                    f"🚀 <b>TP2 HIT — CLOSE</b> ${_esc(sym)}{size_s}\n"
                    f"mcap {_fmt_usd(mcap)} · entry {_fmt_usd(entry)}\n"
                    f"• Close remaining size · bank the win\n"
                    f"<code>{_esc(mint)}</code>"
                )
                await _notify(msg)
            elif event == "stop":
                msg = (
                    f"🛑 <b>STOP</b> ${_esc(sym)}{size_s}\n"
                    f"mcap {_fmt_usd(mcap)} ≤ stop {_fmt_usd(stop_m)}\n"
                    f"• Exit now — no revenge re-entry\n"
                    f"<code>{_esc(mint)}</code>"
                )
                await _notify(msg)
            elif event == "invalid":
                msg = (
                    f"❌ <b>CANCEL</b> ${_esc(sym)}{size_s}\n"
                    f"entry {_fmt_usd(entry)} → now {_fmt_usd(mcap)}\n"
                    f"• {_esc(notes)}\n"
                    f"<i>Do not hold — plan invalid</i>\n"
                    f"<code>{_esc(mint)}</code>"
                )
                await _notify(msg)

            if close:
                journal.close_trade(
                    tid,
                    exit_mcap=float(mcap),
                    notes=notes,
                    force_outcome=outcome,
                )
            events.append(
                {
                    "id": tid,
                    "symbol": sym,
                    "event": event,
                    "mcap": mcap,
                    "notes": notes,
                }
            )

        await asyncio.sleep(0.12)

    _last.update(
        {
            "ts": time.time(),
            "checked": checked,
            "events": events[:20],
            "error": None,
            "cancelled": sum(1 for e in events if e["event"] in ("invalid", "stop")),
        }
    )
    if events:
        logger.info("Position manager events: %s", events)
    return {"ok": True, "checked": checked, "events": events}


async def maybe_daily_report() -> None:
    global _last_daily_report_day
    now = datetime.now(timezone.utc)
    day_key = now.strftime("%Y-%m-%d")
    if day_key == _last_daily_report_day:
        return
    if now.hour != int(MONEY_DAILY_REPORT_UTC_HOUR) % 24:
        # Also allow first boot after hour by sending once if never sent today
        # Only send in the target hour
        return
    _last_daily_report_day = day_key
    desk = desk_snapshot(get_journal())
    sess = desk.get("session") or {}
    exp = desk.get("expectancy") or {}
    msg = (
        f"📊 <b>Daily money report</b> (UTC {day_key})\n"
        f"Bankroll ${_esc(desk.get('bankroll_usd'))} · "
        f"day R: <b>{_esc(sess.get('day_r'))}</b>\n"
        f"Today: {sess.get('wins_today', 0)}W / {sess.get('losses_today', 0)}L · "
        f"opened {sess.get('opened_today', 0)} · open now {sess.get('open_count', 0)}\n"
        f"All-time: WR {exp.get('win_rate_pct')}% · "
        f"E[R]={exp.get('expectancy_r')} · n={exp.get('sample_n')}\n"
        f"Can open: {desk.get('can_open')} — {_esc(desk.get('can_open_reason'))}\n"
        f"<i>Paper until E[R]&gt;0 over ≥20 closes</i>"
    )
    await _notify(msg)


async def background_position_manager_loop() -> None:
    await asyncio.sleep(35)
    logger.info(
        "Position manager ON every %.0fs (money_mode=%s armed=%s)",
        POSITION_MANAGER_INTERVAL_SEC,
        TELEGRAM_MONEY_MODE,
        MONEY_SYSTEM_ARMED,
    )
    while True:
        try:
            if TELEGRAM_MONEY_MODE or MONEY_SYSTEM_ARMED:
                await manage_open_positions()
                try:
                    await maybe_daily_report()
                except Exception as exc:
                    logger.debug("daily report: %s", exc)
        except Exception as exc:
            logger.warning("position manager failed: %s", exc)
            _last["error"] = str(exc)[:200]
        await asyncio.sleep(
            max(25.0, float(POSITION_MANAGER_INTERVAL_SEC or MONEY_INVALIDATE_INTERVAL_SEC))
        )
