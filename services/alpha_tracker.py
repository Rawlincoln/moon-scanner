"""Padre Alpha Tracker desk — group-mentioned tokens → pro analysis → BUY alerts.

Flow:
  1. Pull tokens mentioned in alpha groups (Padre WS if PADRE_AUTH_TOKEN set;
     else DexScreener boosts + social profiles as group-heat proxy).
  2. Analyze like a pro: avoid flags, book health, social honesty, mcap band.
  3. Emit Telegram BUY when gates pass (ALPHA_TRACKER_TELEGRAM=1).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from config import (
    ALPHA_TRACKER_ENABLED,
    ALPHA_TRACKER_MAX_AGE_MIN,
    ALPHA_TRACKER_MAX_PER_CYCLE,
    ALPHA_TRACKER_MCAP_MAX,
    ALPHA_TRACKER_MCAP_MIN,
    ALPHA_TRACKER_MIN_GROUPS,
    ALPHA_TRACKER_MIN_SCORE,
    ALPHA_TRACKER_POLL_SEC,
    ALPHA_TRACKER_TELEGRAM,
    DATA_DIR,
    PADRE_AUTH_TOKEN,
    PADRE_TRADE_URL,
    TELEGRAM_ALPHA_CHAT_ID,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from services.avoid_filters import BLOCKED_MINTS, analyze_avoid_flags
from services.dexscreener import DexScreenerClient
from services.http_client import get_client
from services.padre import PadreClient
from services.padre_alpha_ws import recent_mentions as padre_recent_mentions
from services.padre_alpha_ws import start_background as start_padre_ws
from services.padre_alpha_ws import status as padre_ws_status
from services.pumpfun import PumpFunClient
from services.social_signals import analyze_social_narrative
from services.snipe_social import analyze_snipe_social

logger = logging.getLogger("moon-scanner.alpha-tracker")

_dex = DexScreenerClient()
_pump = PumpFunClient()
_padre = PadreClient()

_SEEN_PATH = Path(DATA_DIR) / "alpha_tracker_seen.json"
_last: dict[str, Any] = {
    "ts": 0.0,
    "candidates": 0,
    "analyzed": 0,
    "buys": 0,
    "sent": 0,
    "errors": [],
    "source": None,
}
_cache: dict[str, Any] = {"buys": [], "ts": 0.0}


def status() -> dict[str, Any]:
    buys = list(_cache.get("buys") or [])
    watch = list(_cache.get("watch") or [])
    return {
        "ok": True,
        "enabled": ALPHA_TRACKER_ENABLED,
        "telegram": ALPHA_TRACKER_TELEGRAM,
        "poll_sec": ALPHA_TRACKER_POLL_SEC,
        "min_groups": ALPHA_TRACKER_MIN_GROUPS,
        "min_score": ALPHA_TRACKER_MIN_SCORE,
        "mcap": [ALPHA_TRACKER_MCAP_MIN, ALPHA_TRACKER_MCAP_MAX],
        "padre_token_set": bool(PADRE_AUTH_TOKEN),
        "padre_ws": padre_ws_status(),
        "last": dict(_last),
        "cached_buys": len(buys),
        "buys": buys[:20],
        "watch": watch[:20],
        "cache_ts": float(_cache.get("ts") or 0),
        "hint": (
            "Set PADRE_AUTH_TOKEN from trade.padre.gg session for live Alpha Tracker. "
            "Without it, Dex boosts + TG socials act as group-heat proxy."
        ),
    }


def _load_seen() -> dict[str, float]:
    try:
        if not _SEEN_PATH.is_file():
            return {}
        import json

        raw = json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
        now = time.time()
        return {
            str(k): float(v)
            for k, v in (raw or {}).items()
            if now - float(v) < 6 * 3600
        }
    except Exception:
        return {}


def _save_seen(seen: dict[str, float]) -> None:
    try:
        import json

        _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        pruned = {k: v for k, v in seen.items() if now - float(v) < 6 * 3600}
        _SEEN_PATH.write_text(json.dumps(pruned), encoding="utf-8")
    except Exception:
        pass


def _esc(s: str) -> str:
    return (
        str(s)
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


async def discover_group_mentions(*, limit: int = 40) -> list[dict[str, Any]]:
    """Merge Padre Alpha Tracker buffer + public group-heat proxies."""
    merged: dict[str, dict[str, Any]] = {}

    # 1) Live Padre Alpha Tracker (if connected)
    for row in padre_recent_mentions(limit=limit):
        mint = str(row.get("tokenAddress") or "").strip()
        if not mint or mint in BLOCKED_MINTS:
            continue
        if str(row.get("chainId") or "solana").lower() not in ("solana", "sol"):
            continue
        cur = merged.get(mint) or {
            "tokenAddress": mint,
            "chainId": "solana",
            "sources": [],
            "group_count": 0,
            "groups": [],
            "boost_amount": 0,
            "has_telegram": False,
            "has_twitter": False,
        }
        cur["sources"] = list(set((cur.get("sources") or []) + ["padre_alpha_tracker"]))
        cur["group_count"] = max(
            int(cur.get("group_count") or 0), int(row.get("group_count") or 1)
        )
        for g in row.get("groups") or []:
            if g and g not in cur["groups"]:
                cur["groups"].append(g)
        if row.get("symbol"):
            cur["symbol"] = row["symbol"]
        if row.get("name"):
            cur["name"] = row["name"]
        merged[mint] = cur

    # 2) Public proxy — Dex boosts (communities paying = group heat)
    try:
        boosts, top, profiles = await asyncio.gather(
            _dex.get_latest_boosts(),
            _dex.get_top_boosts(),
            _dex.get_latest_profiles(),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.debug("dex discover: %s", exc)
        boosts, top, profiles = [], [], []

    for batch, tag in (
        (boosts if isinstance(boosts, list) else [], "dex_boost"),
        (top if isinstance(top, list) else [], "dex_top_boost"),
        (profiles if isinstance(profiles, list) else [], "dex_profile"),
    ):
        for item in batch:
            if not isinstance(item, dict):
                continue
            if str(item.get("chainId") or "").lower() != "solana":
                continue
            mint = str(item.get("tokenAddress") or "").strip()
            if not mint or mint in BLOCKED_MINTS:
                continue
            cur = merged.get(mint) or {
                "tokenAddress": mint,
                "chainId": "solana",
                "sources": [],
                "group_count": 0,
                "groups": [],
                "boost_amount": 0,
                "has_telegram": False,
                "has_twitter": False,
            }
            cur["sources"] = list(set((cur.get("sources") or []) + [tag]))
            amt = 0
            try:
                amt = int(item.get("totalAmount") or item.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0
            cur["boost_amount"] = max(int(cur.get("boost_amount") or 0), amt)
            # Boosts ≈ group shilling intensity
            if tag.startswith("dex_"):
                heat = 1 + (1 if amt >= 50 else 0) + (1 if amt >= 200 else 0)
                cur["group_count"] = max(int(cur.get("group_count") or 0), heat)
                if tag not in (cur.get("groups") or []):
                    cur.setdefault("groups", []).append(tag)
            # Social links
            links = item.get("links") or []
            if isinstance(links, list):
                for lk in links:
                    if not isinstance(lk, dict):
                        continue
                    url = str(lk.get("url") or "").lower()
                    typ = str(lk.get("type") or lk.get("label") or "").lower()
                    if "t.me" in url or "telegram" in typ or "telegram" in url:
                        cur["has_telegram"] = True
                    if "x.com" in url or "twitter" in url or typ == "twitter":
                        cur["has_twitter"] = True
            desc = str(item.get("description") or "")
            if desc:
                cur["description"] = desc[:280]
            if item.get("icon"):
                cur["icon"] = item["icon"]
            if item.get("url"):
                cur["dex_url"] = item["url"]
            merged[mint] = cur

    # 3) Fresh pump climbers with real TG/X — live community heat (not only boost spam)
    try:
        from services.padre_feed import PadreFeedClient

        feed = PadreFeedClient()
        coins = await feed._fetch_pump_sorted("last_trade_timestamp", 60)
    except Exception:
        coins = []
    for coin in coins or []:
        if not isinstance(coin, dict):
            continue
        mint = str(coin.get("mint") or "").strip()
        if not mint or mint in BLOCKED_MINTS or coin.get("complete"):
            continue
        tw = str(coin.get("twitter") or "")
        tg = str(coin.get("telegram") or "")
        replies = int(coin.get("reply_count") or 0)
        # Community signal: TG and/or own X + replies
        has_tg = bool(tg)
        own_x = "status/" not in tw.lower() and (
            "x.com/" in tw.lower() or "twitter.com/" in tw.lower()
        )
        if not (has_tg or (own_x and replies >= 5)):
            continue
        try:
            mcap = float(coin.get("usd_market_cap") or 0)
        except (TypeError, ValueError):
            mcap = 0.0
        if mcap and (
            mcap < ALPHA_TRACKER_MCAP_MIN * 0.6 or mcap > ALPHA_TRACKER_MCAP_MAX * 1.4
        ):
            continue
        cur = merged.get(mint) or {
            "tokenAddress": mint,
            "chainId": "solana",
            "sources": [],
            "group_count": 0,
            "groups": [],
            "boost_amount": 0,
            "has_telegram": False,
            "has_twitter": False,
        }
        cur["sources"] = list(set((cur.get("sources") or []) + ["pump_community"]))
        cur["has_telegram"] = cur.get("has_telegram") or has_tg
        cur["has_twitter"] = cur.get("has_twitter") or bool(tw)
        heat = 1 + (1 if has_tg else 0) + (1 if replies >= 15 else 0) + (1 if own_x else 0)
        cur["group_count"] = max(int(cur.get("group_count") or 0), heat)
        cur["symbol"] = coin.get("symbol") or cur.get("symbol")
        cur["name"] = coin.get("name") or cur.get("name")
        cur["description"] = (coin.get("description") or "")[:280]
        cur["mcap"] = mcap
        if "pump_community" not in (cur.get("groups") or []):
            cur.setdefault("groups", []).append("pump_community")
        merged[mint] = cur

    # Require minimum group heat
    rows = [
        r
        for r in merged.values()
        if int(r.get("group_count") or 0) >= max(1, ALPHA_TRACKER_MIN_GROUPS)
        or "padre_alpha_tracker" in (r.get("sources") or [])
    ]
    def _rank(r: dict[str, Any]) -> tuple:
        mcap = float(r.get("mcap") or 0)
        in_band = (
            0
            if (
                mcap <= 0
                or ALPHA_TRACKER_MCAP_MIN * 0.5
                <= mcap
                <= ALPHA_TRACKER_MCAP_MAX
            )
            else 1
        )
        return (
            0 if "padre_alpha_tracker" in (r.get("sources") or []) else 1,
            in_band,
            0 if "pump_community" in (r.get("sources") or []) else 1,
            -int(r.get("group_count") or 0),
            -int(r.get("boost_amount") or 0),
        )

    rows.sort(key=_rank)
    return rows[:limit]


async def _pair_for_mint(mint: str) -> dict[str, Any] | None:
    try:
        pairs = await _dex.get_token_pairs("solana", mint)
    except Exception:
        pairs = []
    if not pairs:
        return None
    # Prefer highest liquidity sol pair
    def liq(p: dict) -> float:
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    pairs = sorted(pairs, key=liq, reverse=True)
    return pairs[0]


async def _pump_for_mint(mint: str) -> dict[str, Any]:
    try:
        coin = await _pump.get_coin(mint)
        return coin if isinstance(coin, dict) else {}
    except Exception:
        return {}


def _score_pro(
    *,
    mcap: float,
    liq: float,
    age_min: float | None,
    buys5: int,
    sells5: int,
    group_count: int,
    boost: int,
    avoid: dict[str, Any],
    social: dict[str, Any],
    snipe_soc: dict[str, Any],
    honeypot: bool,
    ath_ret: float | None,
    sources: list[str],
) -> tuple[int, str, list[str]]:
    """Return (score 0-100, label BUY|WATCH|SKIP, reasons)."""
    why: list[str] = []
    score = 40

    if honeypot:
        return 0, "SKIP", ["honeypot"]
    flags = set(avoid.get("flags") or [])
    reasons = [str(r) for r in (avoid.get("reasons") or [])]
    reasons_l = [r.lower() for r in reasons]
    # Dump already happened — never chase
    if any("crashed" in r and "ath" in r for r in reasons_l):
        return 10, "SKIP", ["already dumped from ATH"]
    # Only true hard flags kill the trade — soft avoid notes demote score only
    hard_flags = flags & {
        "blocklist",
        "banned",
        "honeypot",
        "lp_unlocked",
        "mint_authority",
        "freeze_authority",
        "entry_trap_social",
        "social_spoof_scam",
        "flash_holders",
        "extreme_wash",
        "wash_buys",
        "drained_curve",
        "ghost_launch",
        "padre_danger",
    }
    # avoid.hard_avoid alone is too broad (includes soft stacks) — use flags
    if hard_flags:
        return 5, "SKIP", [f"hard avoid: {list(hard_flags)[:3]}"]
    soft = set(flags) - hard_flags

    # Group heat
    score += min(25, group_count * 8)
    why.append(f"{group_count} group heat")
    if boost >= 50:
        score += 8
        why.append(f"boost {boost}")
    if "padre_alpha_tracker" in sources:
        score += 12
        why.append("Padre Alpha Tracker mention")

    # Mcap band
    if ALPHA_TRACKER_MCAP_MIN <= mcap <= ALPHA_TRACKER_MCAP_MAX:
        score += 12
        why.append(f"mcap {_fmt_usd(mcap)} in entry band")
    elif mcap < ALPHA_TRACKER_MCAP_MIN:
        score -= 10
        why.append("mcap too low")
    else:
        score -= 18
        why.append("mcap late")

    # Liquidity
    if liq >= 8000:
        score += 10
        why.append(f"liq {_fmt_usd(liq)}")
    elif liq >= 2500:
        score += 5
    elif liq > 0 and liq < 1200:
        score -= 15
        why.append("thin liquidity")

    # Flow
    if buys5 + sells5 > 0:
        ratio = buys5 / max(1, sells5)
        if ratio >= 1.2 and buys5 >= 8:
            score += 10
            why.append(f"flow {buys5}b/{sells5}s")
        elif sells5 > buys5 * 1.5 and sells5 >= 10:
            score -= 12
            why.append("sell pressure")

    # Age
    if age_min is not None:
        if 2 <= age_min <= 90:
            score += 8
        elif age_min < 1.5:
            score -= 8
            why.append("too fresh")
        elif age_min > ALPHA_TRACKER_MAX_AGE_MIN:
            score -= 20
            why.append("too old")

    # ATH retention
    if ath_ret is not None:
        if ath_ret >= 0.75:
            score += 8
            why.append(f"near ATH {ath_ret:.0%}")
        elif ath_ret < 0.45:
            score -= 18
            why.append(f"dumped {ath_ret:.0%} of ATH")

    # Social honesty
    sflags = set(snipe_soc.get("flags") or [])
    if snipe_soc.get("hard_reject") or sflags & {
        "entry_trap_social",
        "social_spoof_scam",
        "fake_twitter",
    }:
        score -= 22
        why.append("spoofed / trap socials")
    else:
        delta = int(snipe_soc.get("score_delta") or 0)
        if delta > 0:
            score += min(10, delta)
            why.append("honest socials")
        elif delta < 0:
            score += max(-12, delta)
    if social.get("highlight"):
        score += 5
        why.append(str(social.get("highlight"))[:40])

    if soft:
        score -= min(15, 4 * len(soft))

    score = max(0, min(100, score))
    # BUY only in entry mcap band (or unknown mcap with strong group heat)
    mcap_ok = mcap <= 0 or mcap <= ALPHA_TRACKER_MCAP_MAX
    mcap_floor_ok = mcap <= 0 or mcap >= ALPHA_TRACKER_MCAP_MIN * 0.7
    if (
        score >= ALPHA_TRACKER_MIN_SCORE
        and mcap_ok
        and mcap_floor_ok
        and not (mcap > 0 and mcap < ALPHA_TRACKER_MCAP_MIN * 0.5)
    ):
        label = "BUY"
    elif score >= max(52, ALPHA_TRACKER_MIN_SCORE - 14):
        label = "WATCH"
    else:
        label = "SKIP"
    return score, label, why[:6]


async def analyze_candidate(cand: dict[str, Any]) -> dict[str, Any] | None:
    mint = str(cand.get("tokenAddress") or "").strip()
    if not mint or mint in BLOCKED_MINTS:
        return None

    pair, pump = await asyncio.gather(_pair_for_mint(mint), _pump_for_mint(mint))
    base = (pair or {}).get("baseToken") or {}
    symbol = (
        cand.get("symbol")
        or base.get("symbol")
        or pump.get("symbol")
        or "?"
    )
    name = cand.get("name") or base.get("name") or pump.get("name") or ""

    mcap = 0.0
    for src in (
        (pair or {}).get("marketCap"),
        (pair or {}).get("fdv"),
        pump.get("usd_market_cap"),
        cand.get("mcap"),
    ):
        try:
            if src and float(src) > 0:
                mcap = float(src)
                break
        except (TypeError, ValueError):
            continue

    liq = 0.0
    try:
        liq = float(((pair or {}).get("liquidity") or {}).get("usd") or 0)
    except (TypeError, ValueError):
        liq = 0.0

    age_min = None
    if (pair or {}).get("pairCreatedAt"):
        age_min = max(
            0.0, (time.time() * 1000 - float(pair["pairCreatedAt"])) / 60_000
        )
    elif pump.get("created_timestamp"):
        try:
            age_min = max(0.0, (time.time() - float(pump["created_timestamp"]) / 1000) / 60)
        except (TypeError, ValueError):
            age_min = None

    txns5 = ((pair or {}).get("txns") or {}).get("m5") or {}
    buys5 = int(txns5.get("buys") or 0)
    sells5 = int(txns5.get("sells") or 0)

    # Token-shaped dict for avoid + social
    tokenish = {
        "tokenAddress": mint,
        "mint": mint,
        "symbol": symbol,
        "name": name,
        "mcap_usd": mcap,
        "marketCap": mcap,
        "liquidity_usd": liq,
        "age_minutes": age_min,
        "pumpfun": pump,
        "pair": pair,
        "twitter": pump.get("twitter") or cand.get("twitter"),
        "website": pump.get("website"),
        "telegram": pump.get("telegram"),
        "description": pump.get("description") or cand.get("description") or "",
    }

    avoid = (
        analyze_avoid_flags(
            safety={},
            pump=pump,
            mint=mint,
            pair=pair or {},
        )
        or {}
    )
    social = (
        analyze_social_narrative(
            pump_coin=pump,
            name=str(name or ""),
            symbol=str(symbol or ""),
            description=str(tokenish.get("description") or ""),
        )
        or {}
    )
    try:
        snipe_soc = analyze_snipe_social(tokenish) or {}
    except Exception:
        snipe_soc = {}

    honeypot = False
    try:
        audit = await _padre.get_token_audit("solana", mint)
        parsed = PadreClient.parse_audit(audit)
        honeypot = bool(parsed.get("honeypot"))
        if parsed.get("danger_checks", 0) >= 2:
            avoid = dict(avoid)
            avoid["hard_avoid"] = list(avoid.get("hard_avoid") or []) + ["padre_danger"]
    except Exception:
        pass

    ath = None
    try:
        ath = float(pump.get("ath_market_cap") or 0) or None
    except (TypeError, ValueError):
        ath = None
    ath_ret = (mcap / ath) if ath and mcap > 0 else None

    score, label, why = _score_pro(
        mcap=mcap,
        liq=liq,
        age_min=age_min,
        buys5=buys5,
        sells5=sells5,
        group_count=int(cand.get("group_count") or 0),
        boost=int(cand.get("boost_amount") or 0),
        avoid=avoid,
        social=social,
        snipe_soc=snipe_soc,
        honeypot=honeypot,
        ath_ret=ath_ret,
        sources=list(cand.get("sources") or []),
    )

    return {
        "tokenAddress": mint,
        "mint": mint,
        "chainId": "solana",
        "symbol": symbol,
        "name": name,
        "mcap_usd": mcap,
        "liquidity_usd": liq,
        "age_minutes": age_min,
        "alpha_label": label,
        "alpha_score": score,
        "alpha": {
            "label": label,
            "score": score,
            "why": why,
            "group_count": int(cand.get("group_count") or 0),
            "groups": list(cand.get("groups") or [])[:8],
            "sources": list(cand.get("sources") or []),
            "boost_amount": int(cand.get("boost_amount") or 0),
        },
        "buys_m5": buys5,
        "sells_m5": sells5,
        "pumpfun": pump,
        "pair": pair,
        "padre_url": f"{PADRE_TRADE_URL}/trade/solana/{mint}",
        "ts": time.time(),
    }


def format_alpha_buy_telegram(card: dict[str, Any]) -> str:
    a = card.get("alpha") or {}
    mint = card.get("tokenAddress") or ""
    sym = card.get("symbol") or "?"
    name = card.get("name") or ""
    score = a.get("score") or card.get("alpha_score")
    groups = a.get("groups") or []
    gcount = a.get("group_count") or 0
    why = a.get("why") or []
    sources = a.get("sources") or []
    padre = card.get("padre_url") or f"{PADRE_TRADE_URL}/trade/solana/{mint}"
    pump = f"https://pump.fun/coin/{mint}" if mint else ""

    title = f"📣 <b>ALPHA BUY</b> ${_esc(sym)}"
    if name:
        title += f" <i>({_esc(name)[:36]})</i>"

    why_line = ""
    if why:
        why_line = "\n• " + "\n• ".join(_esc(str(w)[:90]) for w in why[:4])

    group_line = f"\n👥 Groups: <b>{int(gcount)}</b>"
    if groups:
        group_line += " · " + _esc(", ".join(str(g)[:24] for g in groups[:4]))

    src = "Padre Alpha Tracker" if "padre_alpha_tracker" in sources else "Group heat proxy"
    age = card.get("age_minutes")
    age_s = f"{float(age):.0f}m" if age is not None else "—"

    # Simple plan from mcap
    mcap = float(card.get("mcap_usd") or 0)
    stop = mcap * 0.82
    tp1 = mcap * 1.5
    tp2 = mcap * 2.0

    return (
        f"{title}\n"
        f"Alpha desk · {_fmt_usd(mcap)} · age {age_s} · score {score}"
        f"{group_line}"
        f"\n📡 Source: {_esc(src)}"
        f"{why_line}"
        f"\n\n<b>PLAN</b> (mcap ref)"
        f"\nEntry ≈ {_fmt_usd(mcap)}"
        f"\n🛑 STOP −18% → {_fmt_usd(stop)}"
        f"\n🎯 TP1 +50% → {_fmt_usd(tp1)}"
        f"\n🚀 TP2 +100% → {_fmt_usd(tp2)}"
        f"\n<a href=\"{padre}\">Padre</a> · <a href=\"{pump}\">Pump</a>"
        f"\n<code>{_esc(mint)}</code>"
    )


async def send_alpha_telegram(text: str) -> dict[str, Any]:
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "no bot token"}
    cid = (TELEGRAM_ALPHA_CHAT_ID or TELEGRAM_CHAT_ID or "").strip()
    if not cid:
        return {"ok": False, "error": "no chat id"}
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
            return {
                "ok": False,
                "error": str(data.get("description") or resp.status_code)[:200],
            }
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


async def scan_alpha_tracker(
    *,
    limit: int = 24,
    send_alerts: bool | None = None,
) -> dict[str, Any]:
    """Discover group-mentioned tokens, pro-analyze, optionally Telegram BUY."""
    if not ALPHA_TRACKER_ENABLED:
        return {"ok": True, "enabled": False, "buys": [], "watch": []}

    errors: list[str] = []
    cands = await discover_group_mentions(limit=limit)
    source = (
        "padre+proxy"
        if any("padre_alpha_tracker" in (c.get("sources") or []) for c in cands)
        else "group_heat_proxy"
    )

    # Cap concurrent deep checks
    sem = asyncio.Semaphore(4)

    async def one(c: dict[str, Any]) -> dict[str, Any] | None:
        async with sem:
            try:
                return await analyze_candidate(c)
            except Exception as exc:
                errors.append(str(exc)[:80])
                return None

    results = await asyncio.gather(*[one(c) for c in cands])
    cards = [r for r in results if isinstance(r, dict)]
    buys = [c for c in cards if c.get("alpha_label") == "BUY"]
    watch = [c for c in cards if c.get("alpha_label") == "WATCH"]
    buys.sort(key=lambda c: -int((c.get("alpha") or {}).get("score") or 0))

    do_send = ALPHA_TRACKER_TELEGRAM if send_alerts is None else send_alerts
    sent = 0
    if do_send and buys:
        seen = _load_seen()
        now = time.time()
        for card in buys[: ALPHA_TRACKER_MAX_PER_CYCLE]:
            mint = card.get("tokenAddress") or ""
            key = f"alpha:{mint}"
            if key in seen and now - seen[key] < 90 * 60:
                continue
            msg = format_alpha_buy_telegram(card)
            res = await send_alpha_telegram(msg)
            if res.get("ok"):
                sent += 1
                seen[key] = now
            else:
                errors.append(str(res.get("error") or "tg fail")[:80])
        _save_seen(seen)

    _last.update(
        {
            "ts": time.time(),
            "candidates": len(cands),
            "analyzed": len(cards),
            "buys": len(buys),
            "watch": len(watch),
            "sent": sent,
            "errors": errors[:5],
            "source": source,
        }
    )
    _cache["buys"] = buys[:20]
    _cache["watch"] = watch[:20]
    _cache["ts"] = time.time()

    return {
        "ok": True,
        "enabled": True,
        "source": source,
        "candidates": len(cands),
        "analyzed": len(cards),
        "buys": buys[:15],
        "watch": watch[:15],
        "sent": sent,
        "errors": errors[:5],
        "padre_ws": padre_ws_status(),
        "telegram": ALPHA_TRACKER_TELEGRAM,
        "poll_sec": ALPHA_TRACKER_POLL_SEC,
        "min_score": ALPHA_TRACKER_MIN_SCORE,
        "padre_token_set": bool(PADRE_AUTH_TOKEN),
    }


async def background_alpha_tracker_loop() -> None:
    if not ALPHA_TRACKER_ENABLED:
        logger.info("Alpha Tracker disabled (ALPHA_TRACKER_ENABLED=0)")
        return
    await start_padre_ws()
    logger.info(
        "Alpha Tracker loop on (poll=%.0fs telegram=%s padre_token=%s)",
        ALPHA_TRACKER_POLL_SEC,
        ALPHA_TRACKER_TELEGRAM,
        bool(PADRE_AUTH_TOKEN),
    )
    await asyncio.sleep(8)
    while True:
        try:
            if ALPHA_TRACKER_ENABLED:
                await scan_alpha_tracker(send_alerts=ALPHA_TRACKER_TELEGRAM)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("alpha tracker cycle: %s", exc)
            _last["errors"] = [str(exc)[:120]]
        await asyncio.sleep(max(25.0, ALPHA_TRACKER_POLL_SEC))
