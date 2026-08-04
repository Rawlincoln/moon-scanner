"""Telegram push alerts for Moon / Snipe / Heat picks.

Works even when the browser is closed — background loop + post-scan hooks.
Configure TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env (see TELEGRAM_ALERTS.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from config import (
    PADRE_TRADE_URL,
    TELEGRAM_ALERT_DEDUPE_SEC,
    TELEGRAM_ALERT_FEEDS,
    TELEGRAM_ALERT_GRAD_LABELS,
    TELEGRAM_ALERT_HEAT_LABELS,
    TELEGRAM_ALERT_INTERVAL_SEC,
    TELEGRAM_ALERT_MAX_PER_CYCLE,
    TELEGRAM_ALERT_MOON_LABELS,
    TELEGRAM_ALERT_SNIPE_LABELS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_ALERTS_ENABLED,
)
from services.http_client import get_client

logger = logging.getLogger("moon-scanner.telegram")

_SEEN_PATH = Path(__file__).resolve().parent.parent / "data" / "telegram_alert_seen.json"
_lock = asyncio.Lock()
_last_cycle: dict[str, Any] = {
    "ts": 0.0,
    "sent": 0,
    "error": None,
    "feeds": {},
}


def configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and TELEGRAM_ALERTS_ENABLED)


def status() -> dict[str, Any]:
    return {
        "enabled": TELEGRAM_ALERTS_ENABLED,
        "configured": configured(),
        "bot_set": bool(TELEGRAM_BOT_TOKEN),
        "chat_set": bool(TELEGRAM_CHAT_ID),
        "interval_sec": TELEGRAM_ALERT_INTERVAL_SEC,
        "feeds": list(TELEGRAM_ALERT_FEEDS),
        "labels": {
            "moon": list(TELEGRAM_ALERT_MOON_LABELS),
            "snipe": list(TELEGRAM_ALERT_SNIPE_LABELS),
            "heat": list(TELEGRAM_ALERT_HEAT_LABELS),
            "grad": list(TELEGRAM_ALERT_GRAD_LABELS),
        },
        "dedupe_sec": TELEGRAM_ALERT_DEDUPE_SEC,
        "last_cycle": dict(_last_cycle),
    }


def _load_seen() -> dict[str, float]:
    try:
        if not _SEEN_PATH.is_file():
            return {}
        raw = json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
        now = time.time()
        out: dict[str, float] = {}
        for k, ts in (raw or {}).items():
            try:
                tsf = float(ts)
            except (TypeError, ValueError):
                continue
            if now - tsf < TELEGRAM_ALERT_DEDUPE_SEC * 2:
                out[str(k)] = tsf
        return out
    except Exception:
        return {}


def _save_seen(seen: dict[str, float]) -> None:
    try:
        _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        # prune
        now = time.time()
        pruned = {
            k: v
            for k, v in seen.items()
            if now - float(v) < TELEGRAM_ALERT_DEDUPE_SEC * 2
        }
        _SEEN_PATH.write_text(json.dumps(pruned), encoding="utf-8")
    except Exception as exc:
        logger.debug("telegram seen save failed: %s", exc)


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


def _mint_of(t: dict[str, Any]) -> str:
    return str(t.get("tokenAddress") or t.get("mint") or "").strip()


def _label_of(kind: str, t: dict[str, Any]) -> str:
    if kind == "moon":
        return str(t.get("moon_label") or (t.get("moon") or {}).get("label") or "").upper()
    if kind == "snipe":
        return str(
            t.get("snipe_label") or (t.get("snipe") or {}).get("label") or ""
        ).upper()
    if kind == "heat":
        return str(t.get("heat_label") or (t.get("heat") or {}).get("label") or "").upper()
    if kind in ("grad", "graduated"):
        return str(t.get("grad_label") or (t.get("grad") or {}).get("label") or "").upper()
    return ""


def _allowed_labels(kind: str) -> set[str]:
    if kind == "moon":
        return set(TELEGRAM_ALERT_MOON_LABELS)
    if kind == "snipe":
        return set(TELEGRAM_ALERT_SNIPE_LABELS)
    if kind == "heat":
        return set(TELEGRAM_ALERT_HEAT_LABELS)
    if kind in ("grad", "graduated"):
        return set(TELEGRAM_ALERT_GRAD_LABELS)
    return set()


def _emoji(kind: str, label: str) -> str:
    if kind == "moon":
        return "🌕" if label == "MOON" else "◈"
    if kind == "snipe":
        return "⚡" if label == "SNIPE" else "🟡"
    if kind == "heat":
        return "🔥" if label == "HEAT" else "🟠"
    if kind in ("grad", "graduated"):
        return "◆" if label == "RUNNER" else "📉" if label == "DIP" else "◆"
    return "•"


def format_pick_message(kind: str, t: dict[str, Any]) -> str:
    mint = _mint_of(t)
    label = _label_of(kind, t) or "PICK"
    sym = t.get("symbol") or "?"
    name = t.get("name") or ""
    mcap = _fmt_usd(t.get("mcap_usd") or t.get("mcap"))
    age = t.get("age_minutes")
    age_s = f"{float(age):.0f}m" if age is not None else "—"
    padre = f"{PADRE_TRADE_URL}/trade/solana/{mint}" if mint else ""
    pump = f"https://pump.fun/coin/{mint}" if mint else ""

    why = []
    if kind == "moon":
        why = (t.get("moon") or {}).get("why") or []
        score = t.get("moon_score") or (t.get("moon") or {}).get("moon_score")
    elif kind == "snipe":
        why = (t.get("snipe") or {}).get("why") or []
        score = t.get("snipe_score") or (t.get("snipe") or {}).get("snipe_score")
        tp = t.get("target_2x_usd") or (t.get("snipe") or {}).get("target_2x_usd")
        if tp:
            why = list(why) + [f"2× TP {_fmt_usd(tp)}"]
    elif kind in ("grad", "graduated"):
        why = (t.get("grad") or {}).get("why") or []
        score = t.get("grad_score") or (t.get("grad") or {}).get("grad_score")
        ath_ret = t.get("ath_retention_pct") or (t.get("grad") or {}).get(
            "ath_retention_pct"
        )
        if ath_ret is not None:
            why = list(why) + [f"ATH retention {ath_ret}%"]
    else:
        why = (t.get("heat") or {}).get("why") or []
        score = t.get("heat_score") or (t.get("heat") or {}).get("heat_score")
        dev = t.get("dev") or (t.get("heat") or {}).get("dev") or {}
        if dev:
            why = list(why) + [
                f"dev {dev.get('tokens_launched', '?')} launched · "
                f"{dev.get('tokens_migrated', '?')} migrated"
                + (" · SOLD" if dev.get("creator_sold") else "")
            ]

    why_line = ""
    if why:
        why_line = "\n• " + "\n• ".join(str(w)[:80] for w in why[:3])

    title = f"{_emoji(kind, label)} <b>{label}</b> ${sym}"
    if name:
        title += f" <i>({_esc(name)[:40]})</i>"

    body = (
        f"{title}\n"
        f"{kind.upper()} · {mcap} · age {age_s}"
        + (f" · score {score}" if score is not None else "")
        + why_line
        + (f"\n<a href=\"{padre}\">Padre</a> · <a href=\"{pump}\">Pump</a>" if mint else "")
        + (f"\n<code>{mint}</code>" if mint else "")
    )
    return body


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def send_telegram(text: str, *, chat_id: str | None = None) -> dict[str, Any]:
    """Send one HTML message. Returns {ok, error?}."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
    cid = (chat_id or TELEGRAM_CHAT_ID or "").strip()
    if not cid:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID not set"}
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        client = get_client()
        resp = await client.post(
            url,
            json={
                "chat_id": cid,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=12.0,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or not data.get("ok"):
            err = data.get("description") or f"HTTP {resp.status_code}"
            logger.warning("telegram send failed: %s", err)
            return {"ok": False, "error": str(err)[:200]}
        return {"ok": True}
    except Exception as exc:
        logger.warning("telegram send error: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


async def notify_new_picks(
    kind: str,
    tokens: list[dict[str, Any]],
    *,
    force: bool = False,
) -> int:
    """Alert on new tokens for a feed. Returns number of messages sent."""
    if not configured() and not force:
        return 0
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return 0
    if kind not in TELEGRAM_ALERT_FEEDS and not force:
        return 0

    allowed = _allowed_labels(kind)
    async with _lock:
        seen = _load_seen()
        now = time.time()
        sent = 0
        # Rank: higher labels first
        pri = {
            "MOON": 0,
            "SNIPE": 0,
            "HEAT": 0,
            "RUNNER": 0,
            "WATCH": 1,
            "SETUP": 1,
            "WARM": 1,
            "DIP": 1,
            "RISKY": 2,
        }
        ranked: list[dict[str, Any]] = []
        for t in tokens or []:
            if not isinstance(t, dict):
                continue
            mint = _mint_of(t)
            if not mint:
                continue
            lab = _label_of(kind, t)
            if allowed and lab not in allowed:
                continue
            key = f"{kind}:{mint}"
            if key in seen and now - seen[key] < TELEGRAM_ALERT_DEDUPE_SEC:
                continue
            ranked.append(t)
        ranked.sort(key=lambda x: pri.get(_label_of(kind, x), 9))

        for t in ranked:
            if sent >= TELEGRAM_ALERT_MAX_PER_CYCLE:
                break
            mint = _mint_of(t)
            key = f"{kind}:{mint}"
            msg = format_pick_message(kind, t)
            result = await send_telegram(msg)
            if result.get("ok"):
                seen[key] = now
                sent += 1
                await asyncio.sleep(0.35)  # soft rate limit
            else:
                _last_cycle["error"] = result.get("error")
                break
        if sent:
            _save_seen(seen)
        return sent


async def run_alert_cycle(*, force: bool = False) -> dict[str, Any]:
    """Scan feeds and push new picks to Telegram."""
    if not configured() and not force:
        return {"ok": False, "error": "not configured", "sent": 0}

    from services.scan_moon import scan_moon_tokens
    from services.scan_snipes import scan_safe_snipes
    from services.scan_heat import scan_organic_heat

    total = 0
    feeds: dict[str, Any] = {}
    err = None

    try:
        if "moon" in TELEGRAM_ALERT_FEEDS:
            data = await scan_moon_tokens(limit=12, max_age_minutes=120, force=True)
            n = await notify_new_picks("moon", data.get("tokens") or [], force=force)
            feeds["moon"] = {"shown": len(data.get("tokens") or []), "sent": n}
            total += n
        if "snipe" in TELEGRAM_ALERT_FEEDS or "snipes" in TELEGRAM_ALERT_FEEDS:
            data = await scan_safe_snipes(limit=10, max_age_minutes=60, force=True)
            n = await notify_new_picks("snipe", data.get("tokens") or [], force=force)
            feeds["snipe"] = {"shown": len(data.get("tokens") or []), "sent": n}
            total += n
        if "heat" in TELEGRAM_ALERT_FEEDS:
            data = await scan_organic_heat(limit=12, max_age_minutes=120, force=True)
            n = await notify_new_picks("heat", data.get("tokens") or [], force=force)
            feeds["heat"] = {"shown": len(data.get("tokens") or []), "sent": n}
            total += n
        if "grad" in TELEGRAM_ALERT_FEEDS or "graduated" in TELEGRAM_ALERT_FEEDS:
            from services.scan_graduated import scan_graduated_runners

            data = await scan_graduated_runners(
                limit=12, max_age_minutes=7 * 24 * 60, force=True
            )
            n = await notify_new_picks("grad", data.get("tokens") or [], force=force)
            feeds["grad"] = {"shown": len(data.get("tokens") or []), "sent": n}
            total += n
    except Exception as exc:
        err = str(exc)[:200]
        logger.warning("telegram alert cycle failed: %s", exc)

    _last_cycle.update(
        {"ts": time.time(), "sent": total, "error": err, "feeds": feeds}
    )
    if total:
        logger.info("Telegram alerts sent %s picks %s", total, feeds)
    return {"ok": err is None, "sent": total, "feeds": feeds, "error": err}


async def background_telegram_alert_loop() -> None:
    """Periodic scan + Telegram push. First cycle seeds dedupe (no flood)."""
    await asyncio.sleep(25)
    if not configured():
        logger.info(
            "Telegram alerts off — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env"
        )
        # Still sleep-loop so env can be hot-fixed? No — config is process-start.
        # Keep sleeping lightly in case re-enabled via restart.
        while True:
            await asyncio.sleep(300)
            if configured():
                break
            continue

    # Seed: run one silent cycle marking current picks as seen
    try:
        await _seed_seen_from_scans()
        await send_telegram(
            "✅ <b>Moon Scanner</b> Telegram alerts ON\n"
            f"Feeds: {', '.join(TELEGRAM_ALERT_FEEDS)}\n"
            f"Every ~{TELEGRAM_ALERT_INTERVAL_SEC:.0f}s · keep start.bat running"
        )
    except Exception as exc:
        logger.warning("telegram seed failed: %s", exc)

    while True:
        try:
            if configured():
                await run_alert_cycle()
        except Exception as exc:
            logger.warning("telegram loop error: %s", exc)
            _last_cycle["error"] = str(exc)[:200]
        await asyncio.sleep(max(30.0, float(TELEGRAM_ALERT_INTERVAL_SEC)))


async def _seed_seen_from_scans() -> None:
    """Mark currently shown tokens as already alerted (avoid dump on enable)."""
    from services.scan_moon import scan_moon_tokens
    from services.scan_snipes import scan_safe_snipes
    from services.scan_heat import scan_organic_heat

    async with _lock:
        seen = _load_seen()
        now = time.time()
        for kind, coro in (
            ("moon", scan_moon_tokens(limit=12, max_age_minutes=120, force=False)),
            ("snipe", scan_safe_snipes(limit=10, max_age_minutes=60, force=False)),
            ("heat", scan_organic_heat(limit=12, max_age_minutes=120, force=False)),
        ):
            if kind == "snipe" and "snipe" not in TELEGRAM_ALERT_FEEDS and "snipes" not in TELEGRAM_ALERT_FEEDS:
                continue
            if kind != "snipe" and kind not in TELEGRAM_ALERT_FEEDS:
                continue
            try:
                data = await coro
            except Exception:
                continue
            for t in data.get("tokens") or []:
                mint = _mint_of(t) if isinstance(t, dict) else ""
                if not mint:
                    continue
                lab = _label_of(kind, t)
                if _allowed_labels(kind) and lab not in _allowed_labels(kind):
                    continue
                seen[f"{kind}:{mint}"] = now
        _save_seen(seen)
        logger.info("Telegram alert dedupe seeded (%s keys)", len(seen))


async def send_test_message() -> dict[str, Any]:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {
            "ok": False,
            "error": "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env",
        }
    return await send_telegram(
        "🧪 <b>Moon Scanner test</b>\n"
        "If you see this, Telegram alerts are wired.\n"
        f"Feeds: {', '.join(TELEGRAM_ALERT_FEEDS) or 'none'}"
    )


