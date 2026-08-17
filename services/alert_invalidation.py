"""Auto-cancel open money-mode alerts when setup breaks.

Polls pump.fun mcap for open journal trades; sends Telegram CANCEL + closes row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from config import MONEY_INVALIDATE_INTERVAL_SEC, TELEGRAM_MONEY_MODE
from services.http_client import get as http_get
from config import PUMPFUN_API_URL, REQUEST_TIMEOUT
from services.trade_journal import get_journal

logger = logging.getLogger("moon-scanner.invalidation")

_last: dict[str, Any] = {"ts": 0.0, "checked": 0, "cancelled": 0, "error": None}


def status() -> dict[str, Any]:
    return {
        "money_mode": TELEGRAM_MONEY_MODE,
        "interval_sec": MONEY_INVALIDATE_INTERVAL_SEC,
        "last": dict(_last),
        "open": len(get_journal().active_open()),
    }


async def fetch_mcap(mint: str) -> float | None:
    """Best mcap: pump.fun first, DexScreener fallback (post-migrate)."""
    mint = (mint or "").strip()
    if not mint:
        return None
    try:
        resp = await http_get(
            f"{PUMPFUN_API_URL}/coins/{mint}",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://pump.fun",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            m = float(data.get("usd_market_cap") or data.get("market_cap") or 0)
            if m > 0:
                return m
    except Exception:
        pass
    # Graduated / pump miss → Dex pairs
    try:
        resp = await http_get(
            f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            pairs = resp.json() or []
            best = 0.0
            for p in pairs if isinstance(pairs, list) else []:
                try:
                    m = float(p.get("marketCap") or p.get("fdv") or 0)
                except (TypeError, ValueError):
                    m = 0.0
                if m > best:
                    best = m
            if best > 0:
                return best
    except Exception:
        pass
    return None


async def run_invalidation_cycle() -> dict[str, Any]:
    """Check all open journal trades; cancel + notify if broken."""
    journal = get_journal()
    open_rows = journal.active_open()
    checked = 0
    cancelled = 0
    details: list[dict[str, Any]] = []

    for row in open_rows[:40]:
        mint = str(row.get("mint") or "")
        if not mint:
            continue
        checked += 1
        mcap = await fetch_mcap(mint)
        if mcap is None:
            await asyncio.sleep(0.12)
            continue
        updated = journal.apply_mcap(int(row["id"]), mcap)
        await asyncio.sleep(0.12)
        if not updated or updated.get("status") != "invalid":
            continue
        cancelled += 1
        reason = updated.get("invalid_reason") or "setup invalid"
        details.append(
            {
                "id": updated.get("id"),
                "mint": mint,
                "symbol": updated.get("symbol"),
                "reason": reason,
            }
        )
        # Notify Telegram
        try:
            from services.telegram_alerts import send_telegram, _fmt_usd, _esc

            plan = {}
            try:
                plan = json.loads(updated.get("plan_json") or "{}")
            except Exception:
                pass
            entry = plan.get("entry_mcap") or updated.get("entry_mcap")
            msg = (
                f"❌ <b>CANCEL</b> ${_esc(updated.get('symbol') or '?')}\n"
                f"{_esc(str(updated.get('feed') or '').upper())} · "
                f"entry {_fmt_usd(entry)} → now {_fmt_usd(mcap)}\n"
                f"• {_esc(reason)}\n"
                f"<i>Do not hold — plan invalid</i>\n"
                f"<code>{_esc(mint)}</code>"
            )
            await send_telegram(msg)
        except Exception as exc:
            logger.debug("cancel notify failed: %s", exc)

    _last.update(
        {
            "ts": time.time(),
            "checked": checked,
            "cancelled": cancelled,
            "error": None,
            "details": details[:10],
        }
    )
    if cancelled:
        logger.info("Invalidated %s open alerts (%s checked)", cancelled, checked)
    return {"ok": True, "checked": checked, "cancelled": cancelled, "details": details}


async def background_invalidation_loop() -> None:
    await asyncio.sleep(40)
    if not TELEGRAM_MONEY_MODE:
        logger.info("Money-mode off — alert invalidation loop idle")
        while True:
            await asyncio.sleep(300)
            if TELEGRAM_MONEY_MODE:
                break
    logger.info(
        "Money-mode invalidation ON every %.0fs", MONEY_INVALIDATE_INTERVAL_SEC
    )
    while True:
        try:
            if TELEGRAM_MONEY_MODE:
                await run_invalidation_cycle()
        except Exception as exc:
            logger.warning("invalidation cycle failed: %s", exc)
            _last["error"] = str(exc)[:200]
        await asyncio.sleep(max(30.0, float(MONEY_INVALIDATE_INTERVAL_SEC)))
