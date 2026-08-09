"""Telegram push alerts for Moon / Snipe / Heat picks.

Money-mode (default): MOON + SNIPE only, with entry/stop/TP/invalid rules.
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
    DATA_DIR,
    MONEY_AUTO_LAB,
    MONEY_REQUIRE_CONTROL_SURFACE,
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
    TELEGRAM_MONEY_MODE,
)
from services.http_client import get_client
from services.capital import can_open_trade, enrich_plan_with_size
from services.cockpit import (
    control_surface_gate,
    extract_cockpit,
    format_cockpit_telegram,
    token_to_cockpit_input,
)
from services.dev_risk import (
    attach_dev_risk,
    dev_risk_gate,
    format_dev_telegram,
)
from services.money_plan import build_money_plan

logger = logging.getLogger("moon-scanner.telegram")

_SEEN_PATH = Path(DATA_DIR) / "telegram_alert_seen.json"
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
        "money_mode": TELEGRAM_MONEY_MODE,
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
        "hint": (
            "MONEY MODE: MOON+SNIPE+HEAT · entry/stop/TP · auto-CANCEL if setup breaks"
            if TELEGRAM_MONEY_MODE
            else "Full multi-feed alerts (set TELEGRAM_MONEY_MODE=1 for capital mode)"
        ),
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
    mcap_raw = t.get("mcap_usd") or t.get("mcap")
    mcap = _fmt_usd(mcap_raw)
    age = t.get("age_minutes")
    age_s = f"{float(age):.0f}m" if age is not None else "—"
    padre = f"{PADRE_TRADE_URL}/trade/solana/{mint}" if mint else ""
    pump = f"https://pump.fun/coin/{mint}" if mint else ""

    why = []
    score = None
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

    title = f"{_emoji(kind, label)} <b>{_esc(label)}</b> ${_esc(sym)}"
    if name:
        title += f" <i>({_esc(name)[:40]})</i>"

    if why:
        why_line = "\n• " + "\n• ".join(_esc(str(w)[:80]) for w in why[:3])
    else:
        why_line = ""

    # Money plan + size (complete system)
    plan = t.get("_money_plan") if isinstance(t.get("_money_plan"), dict) else None
    if not plan:
        plan = (
            enrich_plan_with_size(kind, t)
            if TELEGRAM_MONEY_MODE or kind in ("moon", "snipe", "heat")
            else build_money_plan(kind, t)
        )
    plan_lines = ""
    if TELEGRAM_MONEY_MODE or kind in ("moon", "snipe", "heat"):
        sizing = plan.get("sizing") or {}
        size_line = ""
        if sizing.get("size_usd"):
            size_line = (
                f"\n💰 SIZE ${sizing.get('size_usd')} "
                f"(risk ${sizing.get('risk_usd')} · "
                + (
                    f"~{sizing.get('size_sol')} SOL · "
                    if sizing.get("size_sol")
                    else ""
                )
                + f"bankroll ${sizing.get('bankroll_usd')})"
            )
        plan_lines = (
            f"\n\n<b>PLAN</b> (mcap ref · risk-sized)"
            f"{size_line}\n"
            f"Entry ≈ {_fmt_usd(plan.get('entry_mcap'))}\n"
            f"🛑 STOP −{plan.get('stop_pct')}% → {_fmt_usd(plan.get('stop_mcap'))}\n"
            f"🎯 TP1 +{plan.get('tp1_pct')}% → {_fmt_usd(plan.get('tp1_mcap'))} (sell 50%)\n"
            f"🚀 TP2 +{plan.get('tp2_pct')}% → {_fmt_usd(plan.get('tp2_mcap'))} (close rest)\n"
            f"❌ INVALID if &lt; {_fmt_usd(plan.get('invalid_if_below_mcap'))} "
            f"or no +{plan.get('need_move_pct')}% in {plan.get('max_hold_min'):.0f}m\n"
            f"<i>Skip if late (past TP1) · never average down · obey daily R stop</i>"
        )

    # Auto-lab cockpit (Germanus-style facts on money alerts)
    lab_lines = ""
    cockpit = t.get("_cockpit") if isinstance(t.get("_cockpit"), dict) else None
    if cockpit is None and (MONEY_AUTO_LAB or TELEGRAM_MONEY_MODE):
        try:
            cockpit = extract_cockpit(token_to_cockpit_input(t))
        except Exception:
            cockpit = None
    if cockpit and (
        MONEY_AUTO_LAB or TELEGRAM_MONEY_MODE or kind in ("moon", "snipe", "heat")
    ):
        lab_lines = format_cockpit_telegram(cockpit)

    dev_lines = ""
    dev = t.get("devRisk") or t.get("_dev_risk")
    if not isinstance(dev, dict) and (
        TELEGRAM_MONEY_MODE or kind in ("moon", "snipe", "heat")
    ):
        try:
            dev = attach_dev_risk(t)
        except Exception:
            dev = None
    if isinstance(dev, dict) and (
        TELEGRAM_MONEY_MODE or kind in ("moon", "snipe", "heat")
    ):
        dev_lines = format_dev_telegram(dev)

    ticker_lines = ""
    if TELEGRAM_MONEY_MODE or kind in ("moon", "snipe", "heat"):
        try:
            from services.ticker_registry import attach_ticker_uniqueness

            tu = t.get("tickerUniqueness")
            if not isinstance(tu, dict):
                tu = attach_ticker_uniqueness(t, record=True)
            st = tu.get("status") or ""
            tsym = _esc(tu.get("symbol") or sym)
            if tu.get("unique"):
                ticker_lines = (
                    f"\n🏷 <b>TICKER</b> unique ${tsym} — fresh brand signal"
                )
            elif st in ("reused", "heavily_reused") or (
                tu.get("is_hot_meta") and int(tu.get("prior_mints") or 0) >= 1
            ):
                ticker_lines = (
                    f"\n🏷 <b>TICKER</b> reused ${tsym} "
                    f"×{int(tu.get('prior_mints') or 0)}+ mints — copycat risk"
                )
            elif tu.get("is_hot_meta"):
                ticker_lines = (
                    f"\n🏷 <b>TICKER</b> hot meta ${tsym} "
                    "— not unique (need real edge)"
                )
        except Exception:
            ticker_lines = ""

    flow_lines = ""
    if TELEGRAM_MONEY_MODE or kind in ("moon", "snipe", "heat"):
        try:
            from services.fee_flow import attach_fee_flow, format_fee_telegram

            ff = t.get("feeFlow")
            if not isinstance(ff, dict):
                ff = attach_fee_flow(t)
            flow_lines = format_fee_telegram(ff)
        except Exception:
            flow_lines = ""

    body = (
        f"{title}\n"
        f"{_esc(kind.upper())} · {mcap} · age {age_s}"
        + (f" · score {score}" if score is not None else "")
        + why_line
        + plan_lines
        + lab_lines
        + dev_lines
        + ticker_lines
        + flow_lines
        + (f"\n<a href=\"{padre}\">Padre</a> · <a href=\"{pump}\">Pump</a>" if mint else "")
        + (
            f" · <a href=\"https://moon-scanner-9tlz.onrender.com/lab\">Lab</a>"
            if mint
            else ""
        )
        + (f"\n<code>{_esc(mint)}</code>" if mint else "")
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
    # Money mode: moon + snipe + heat (organic edge). Still block grad spam.
    kind_l = kind.lower().strip()
    if TELEGRAM_MONEY_MODE and kind_l not in ("moon", "snipe", "snipes", "heat"):
        return 0
    if kind not in TELEGRAM_ALERT_FEEDS and not force:
        # allow snipes alias
        if not (kind_l in ("snipe", "snipes") and (
            "snipe" in TELEGRAM_ALERT_FEEDS or "snipes" in TELEGRAM_ALERT_FEEDS
        )):
            return 0

    allowed = _allowed_labels(kind if kind_l != "snipes" else "snipe")
    feed_key = "snipe" if kind_l in ("snipe", "snipes") else kind_l

    # Session risk gate (daily loss / max open / disarmed)
    try:
        from services.trade_journal import get_journal

        ok_gate, gate_why = can_open_trade(get_journal(), kind=feed_key)
        if not ok_gate:
            logger.info("money gate blocked %s alerts: %s", feed_key, gate_why)
            _last_cycle["gate"] = gate_why
            return 0
    except Exception as exc:
        logger.debug("gate check failed: %s", exc)

    async with _lock:
        seen = _load_seen()
        now = time.time()
        sent = 0
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
            lab = _label_of(feed_key, t)
            if allowed and lab not in allowed:
                continue
            # Money mode: WATCH only for climb / near-migration (not lottery survivors)
            if (
                TELEGRAM_MONEY_MODE
                and feed_key == "moon"
                and lab == "WATCH"
            ):
                st = str(
                    t.get("stage")
                    or (t.get("moon") or {}).get("stage")
                    or ""
                ).lower()
                mcap_w = 0.0
                try:
                    mcap_w = float(
                        t.get("mcap_usd")
                        or (t.get("moon") or {}).get("mcap_usd")
                        or 0
                    )
                except (TypeError, ValueError):
                    mcap_w = 0.0
                if st not in ("climb", "near_migration") and mcap_w < 12_000:
                    continue
            key = f"{feed_key}:{mint}"
            if key in seen and now - seen[key] < TELEGRAM_ALERT_DEDUPE_SEC:
                continue
            ranked.append(t)
        def _rank_key(x: dict[str, Any]) -> tuple:
            lab_p = pri.get(_label_of(feed_key, x), 9)
            # Prefer proven migrator / prior-moon devs first
            try:
                from services.dev_risk import attach_dev_risk

                d = x.get("devRisk")
                if not isinstance(d, dict):
                    d = attach_dev_risk(x)
                proven = 0 if d.get("proven_dev") else 1
                moons = -int(d.get("prior_moons") or 0)
                mig = -int(d.get("tokens_migrated") or 0)
            except Exception:
                proven, moons, mig = 1, 0, 0
            try:
                from services.ticker_registry import attach_ticker_uniqueness

                tu = x.get("tickerUniqueness")
                if not isinstance(tu, dict):
                    tu = attach_ticker_uniqueness(x, record=False)
                uniq = 0 if tu.get("unique") else 1
                reused_n = int(tu.get("prior_mints") or 0)
            except Exception:
                uniq, reused_n = 1, 0
            score = -int(
                x.get("moon_score")
                or x.get("snipe_score")
                or (x.get("moon") or {}).get("moon_score")
                or 0
            )
            return (lab_p, proven, moons, mig, uniq, reused_n, score)

        ranked.sort(key=_rank_key)

        for t in ranked:
            if sent >= TELEGRAM_ALERT_MAX_PER_CYCLE:
                break
            # Re-check gate each send (open count may fill)
            try:
                from services.trade_journal import get_journal

                ok_gate, gate_why = can_open_trade(get_journal(), kind=feed_key)
                if not ok_gate:
                    _last_cycle["gate"] = gate_why
                    break
            except Exception:
                pass
            mint = _mint_of(t)
            key = f"{feed_key}:{mint}"
            plan = enrich_plan_with_size(feed_key, t)
            t = dict(t)
            t["_money_plan"] = plan

            # Auto-lab: cockpit facts + fail-closed control surface
            cockpit = None
            try:
                cockpit = extract_cockpit(token_to_cockpit_input(t))
                t["_cockpit"] = cockpit
            except Exception as exc:
                logger.debug("cockpit extract failed: %s", exc)
                cockpit = None

            # Control surface: required for moon/snipe; HEAT soft-fail (higher recall)
            if TELEGRAM_MONEY_MODE and MONEY_REQUIRE_CONTROL_SURFACE:
                if feed_key in ("moon", "snipe"):
                    if cockpit is None:
                        logger.info("skip %s — no cockpit/control surface", mint[:8])
                        continue
                    ok_ctrl, why_ctrl = control_surface_gate(cockpit)
                    if not ok_ctrl:
                        logger.info(
                            "skip %s control surface: %s", mint[:8], why_ctrl
                        )
                        _last_cycle["control_skip"] = why_ctrl
                        continue
                elif feed_key == "heat" and cockpit is not None:
                    ok_ctrl, why_ctrl = control_surface_gate(cockpit)
                    if not ok_ctrl:
                        logger.info(
                            "skip heat %s control surface: %s", mint[:8], why_ctrl
                        )
                        _last_cycle["control_skip"] = why_ctrl
                        continue

            # Dev / serial rugger gate
            try:
                dev = attach_dev_risk(t)
                t["_dev_risk"] = dev
                t["devRisk"] = dev
                if TELEGRAM_MONEY_MODE:
                    ok_dev, why_dev = dev_risk_gate(dev)
                    if not ok_dev:
                        logger.info("skip %s dev risk: %s", mint[:8], why_dev)
                        _last_cycle["dev_skip"] = why_dev
                        continue
            except Exception as exc:
                logger.debug("dev risk failed: %s", exc)

            # Flash fee / wash volume gate
            try:
                from services.fee_flow import attach_fee_flow, fee_flow_gate

                ff = attach_fee_flow(t)
                t["feeFlow"] = ff
                if TELEGRAM_MONEY_MODE:
                    ok_f, why_f = fee_flow_gate(ff)
                    if not ok_f:
                        logger.info("skip %s fee flow: %s", mint[:8], why_f)
                        _last_cycle["fee_skip"] = why_f
                        continue
            except Exception as exc:
                logger.debug("fee flow failed: %s", exc)

            # Heat: only alert quality band (HEAT always; WARM if score high enough)
            if feed_key == "heat":
                lab_h = _label_of("heat", t)
                hs = int(
                    t.get("heat_score")
                    or (t.get("heat") or {}).get("heat_score")
                    or 0
                )
                if lab_h == "WARM" and hs < 58:
                    continue
                if lab_h == "RISKY":
                    continue

            # Archive snapshot into Lab (non-blocking best-effort)
            if MONEY_AUTO_LAB and cockpit:
                try:
                    from services.scan_archive import get_archive

                    get_archive().store(token_to_cockpit_input(t), store_raw=False)
                except Exception as exc:
                    logger.debug("auto-lab archive failed: %s", exc)

            msg = format_pick_message(feed_key, t)
            result = await send_telegram(msg)
            if result.get("ok"):
                seen[key] = now
                sent += 1
                try:
                    from services.trade_journal import get_journal

                    get_journal().open_from_alert(
                        feed_key, t, alert_sent=True, plan=plan
                    )
                except Exception as exc:
                    logger.debug("journal open failed: %s", exc)
                await asyncio.sleep(0.35)
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
        # Organic heat — enabled in money mode (live edge); still no grad spam
        if "heat" in TELEGRAM_ALERT_FEEDS:
            from services.scan_heat import scan_organic_heat

            data = await scan_organic_heat(limit=14, max_age_minutes=150, force=True)
            n = await notify_new_picks("heat", data.get("tokens") or [], force=force)
            feeds["heat"] = {"shown": len(data.get("tokens") or []), "sent": n}
            total += n
        if not TELEGRAM_MONEY_MODE and (
            "grad" in TELEGRAM_ALERT_FEEDS or "graduated" in TELEGRAM_ALERT_FEEDS
        ):
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
        {
            "ts": time.time(),
            "sent": total,
            "error": err,
            "feeds": feeds,
            "money_mode": TELEGRAM_MONEY_MODE,
        }
    )
    if total:
        logger.info("Telegram alerts sent %s picks %s", total, feeds)
    return {
        "ok": err is None,
        "sent": total,
        "feeds": feeds,
        "error": err,
        "money_mode": TELEGRAM_MONEY_MODE,
    }


async def background_telegram_alert_loop() -> None:
    """Periodic scan + Telegram push. First cycle seeds dedupe (no flood)."""
    await asyncio.sleep(25)
    if not configured():
        logger.info(
            "Telegram alerts off — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env"
        )
        while True:
            await asyncio.sleep(300)
            if configured():
                break
            continue

    try:
        await _seed_seen_from_scans()
        from services.capital import desk_snapshot
        from services.trade_journal import get_journal

        desk = desk_snapshot(get_journal())
        mode_line = (
            "💰 <b>COMPLETE MONEY SYSTEM</b>\n"
            "MOON + SNIPE + 🔥 HEAT · risk-sized · TP1/TP2/STOP managed\n"
            f"Bankroll ${desk.get('bankroll_usd')} · "
            f"risk {desk.get('risk_per_trade_pct')}%/trade "
            f"(${desk.get('risk_per_trade_usd')})\n"
            f"Max open {desk.get('max_open_trades')} · "
            f"day stop −{desk.get('max_daily_loss_r')}R\n"
            if TELEGRAM_MONEY_MODE
            else "Multi-feed alerts (heat/grad enabled)\n"
        )
        await send_telegram(
            "✅ <b>Moon Scanner</b> Telegram alerts ON\n"
            f"{mode_line}"
            f"Feeds: {', '.join(TELEGRAM_ALERT_FEEDS)}\n"
            f"Armed: {desk.get('armed')} · can open: {desk.get('can_open')}\n"
            f"Every ~{TELEGRAM_ALERT_INTERVAL_SEC:.0f}s · desk /money"
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

    async with _lock:
        seen = _load_seen()
        now = time.time()
        jobs: list[tuple[str, Any]] = []
        if "moon" in TELEGRAM_ALERT_FEEDS:
            jobs.append(
                ("moon", scan_moon_tokens(limit=12, max_age_minutes=120, force=False))
            )
        if "snipe" in TELEGRAM_ALERT_FEEDS or "snipes" in TELEGRAM_ALERT_FEEDS:
            jobs.append(
                ("snipe", scan_safe_snipes(limit=10, max_age_minutes=60, force=False))
            )
        if "heat" in TELEGRAM_ALERT_FEEDS:
            from services.scan_heat import scan_organic_heat

            jobs.append(
                ("heat", scan_organic_heat(limit=14, max_age_minutes=150, force=False))
            )
        for kind, coro in jobs:
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
    plan_demo = build_money_plan(
        "moon",
        {"mcap_usd": 15_000, "symbol": "DEMO"},
    )
    return await send_telegram(
        "🧪 <b>Moon Scanner test</b>\n"
        f"Money mode: <b>{'ON' if TELEGRAM_MONEY_MODE else 'OFF'}</b>\n"
        f"Feeds: {', '.join(TELEGRAM_ALERT_FEEDS) or 'none'}\n"
        f"Demo plan entry {_fmt_usd(plan_demo.get('entry_mcap'))} · "
        f"stop {_fmt_usd(plan_demo.get('stop_mcap'))} · "
        f"TP1 {_fmt_usd(plan_demo.get('tp1_mcap'))}"
    )
