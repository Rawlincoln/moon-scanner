"""FOMO aping channel — watch elite S-tier wallets for buys & exits.

Polls Solana RPC (Helius when configured) for each tracked wallet's recent
signatures, parses pre/post token balances, and fires Telegram alerts on:

  🔥 FOMO BUY  — wallet increased SPL token balance (not SOL/stables)
  🚪 FOMO EXIT — wallet reduced/zeroed a tracked open bag

"Immediate" = fast poll loop (default ~10s). Not mempool; next block after confirm.
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
    FOMO_ALERT_TELEGRAM,
    FOMO_ENABLED,
    FOMO_MAX_WALLETS,
    FOMO_OPEN_MANAGE,
    FOMO_POLL_SEC,
    FOMO_SIGS_PER_WALLET,
    FOMO_WALLETS_PER_CYCLE,
    HELIUS_API_KEY,
    PADRE_TRADE_URL,
    SOLANA_RPC_HTTP,
    TELEGRAM_FOMO_CHAT_ID,
    rpc_is_paid,
)
from services.fomo_wallets import list_wallets as list_managed_wallets
from services.http_client import get_client
from services.realtime_rpc import http_url

logger = logging.getLogger("moon-scanner.fomo")

_SEEN_PATH = Path(DATA_DIR) / "fomo_seen_sigs.json"
_POS_PATH = Path(DATA_DIR) / "fomo_positions.json"
_EVENTS_PATH = Path(DATA_DIR) / "fomo_events.json"

# Skip native / stable / dust noise
_SKIP_MINTS = {
    "So11111111111111111111111111111111111111112",  # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB",  # USD1
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # ETH wormhole
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK (optional noise — keep for FOMO)
}

# Min absolute token amount change to count (ui amount)
_MIN_UI_DELTA = 1.0

_seen: dict[str, float] = {}
_positions: dict[str, dict[str, Any]] = {}  # key wallet:mint
_events: list[dict[str, Any]] = []
_last_status: dict[str, Any] = {
    "ts": 0.0,
    "cycle": 0,
    "wallets": 0,
    "new_sigs": 0,
    "buys": 0,
    "exits": 0,
    "errors": [],
    "seeded": False,
}
_lock = asyncio.Lock()
_rpc_id = 1
_rr_index = 0  # round-robin start index


def _next_id() -> int:
    global _rpc_id
    _rpc_id += 1
    return _rpc_id


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("fomo load %s: %s", path.name, exc)
    return default


def _save_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("fomo save %s: %s", path.name, exc)


def _boot_state() -> None:
    global _seen, _positions, _events
    raw = _load_json(_SEEN_PATH, {})
    if isinstance(raw, dict):
        _seen = {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))}
    pos = _load_json(_POS_PATH, {})
    if isinstance(pos, dict):
        _positions = {str(k): v for k, v in pos.items() if isinstance(v, dict)}
    ev = _load_json(_EVENTS_PATH, [])
    if isinstance(ev, list):
        _events = [e for e in ev if isinstance(e, dict)][-200:]


def _persist() -> None:
    # prune seen > 48h
    now = time.time()
    cut = now - 48 * 3600
    keep = {k: v for k, v in _seen.items() if v >= cut}
    if len(keep) > 8000:
        # keep newest
        items = sorted(keep.items(), key=lambda x: -x[1])[:6000]
        keep = dict(items)
    _save_json(_SEEN_PATH, keep)
    _save_json(_POS_PATH, _positions)
    _save_json(_EVENTS_PATH, _events[-200:])


def fomo_wallets() -> list[dict[str, Any]]:
    """Wallets managed in the app (DATA_DIR/fomo_wallets.json).

    Add/remove on /fomo UI — these always drive Telegram FOMO alerts.
    """
    managed = list_managed_wallets()
    # Prefer S then A then B for poll order
    tier_r = {"S": 0, "A": 1, "B": 2}
    managed = sorted(
        managed,
        key=lambda w: (
            tier_r.get(str(w.get("tier") or "S"), 9),
            str(w.get("label") or ""),
        ),
    )
    return managed[: max(1, int(FOMO_MAX_WALLETS))]


async def seed_wallet_history(address: str) -> int:
    """Mark current signatures seen so adding a wallet doesn't spam old buys."""
    addr = (address or "").strip()
    if not addr:
        return 0
    n = 0
    try:
        sigs = await _sigs_for_wallet(addr, max(3, min(25, int(FOMO_SIGS_PER_WALLET))))
        for s in sigs:
            sig = str(s.get("signature") or "")
            if sig:
                _seen[sig] = time.time()
                n += 1
        _persist()
    except Exception as exc:
        logger.warning("seed wallet %s: %s", addr[:8], exc)
    return n


def status() -> dict[str, Any]:
    wallets = fomo_wallets()
    return {
        "ok": True,
        "enabled": FOMO_ENABLED,
        "poll_sec": FOMO_POLL_SEC,
        "telegram": FOMO_ALERT_TELEGRAM,
        "chat_override": bool((TELEGRAM_FOMO_CHAT_ID or "").strip()),
        "rpc": (http_url() or SOLANA_RPC_HTTP)[:48],
        "helius": bool(HELIUS_API_KEY),
        "manageable": True,
        "open_manage": FOMO_OPEN_MANAGE,
        "wallets": [
            {
                "label": w.get("label"),
                "address": w.get("address"),
                "tier": w.get("tier"),
                "note": w.get("note"),
                "source": w.get("source"),
                "added_at": w.get("added_at"),
                "id": w.get("id"),
            }
            for w in wallets
        ],
        "open_positions": len(_positions),
        "events": list(reversed(_events[-40:])),
        "last": dict(_last_status),
        "hint": (
            "Add/remove wallets on this page — they keep firing FOMO buy/exit alerts. "
            "KOL dropdown shows 1d/7d/30d PnL when BIRDEYE_API_KEY or CIELO_API_KEY is set. "
            "Set HELIUS_API_KEY for reliable buy/exit polls."
        ),
    }


async def status_with_pnl(*, force_pnl: bool = False) -> dict[str, Any]:
    """Status payload plus PnL on each wallet for the KOL dropdown."""
    st = status()
    try:
        from services.wallet_pnl import fetch_pnl_for_wallets

        st["wallets"] = await fetch_pnl_for_wallets(
            st.get("wallets") or [], force=force_pnl
        )
    except Exception as exc:
        logger.debug("status_with_pnl: %s", exc)
    return st


async def _rpc(method: str, params: list[Any]) -> Any:
    client = get_client()
    url = http_url() or SOLANA_RPC_HTTP
    body = {"jsonrpc": "2.0", "id": _next_id(), "method": method, "params": params}
    r = await client.post(url, json=body, timeout=18.0)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:200])
    return data.get("result")


async def _sigs_for_wallet(address: str, limit: int) -> list[dict[str, Any]]:
    result = await _rpc(
        "getSignaturesForAddress",
        [address, {"limit": limit, "commitment": "confirmed"}],
    )
    if not isinstance(result, list):
        return []
    return [x for x in result if isinstance(x, dict) and x.get("signature")]


async def _get_tx(sig: str) -> dict[str, Any] | None:
    result = await _rpc(
        "getTransaction",
        [
            sig,
            {
                "encoding": "jsonParsed",
                "commitment": "confirmed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    )
    return result if isinstance(result, dict) else None


def _ui_amount(entry: dict[str, Any]) -> float:
    ui = entry.get("uiTokenAmount") or {}
    if ui.get("uiAmount") is not None:
        try:
            return float(ui["uiAmount"])
        except (TypeError, ValueError):
            pass
    try:
        amt = float(ui.get("amount") or 0)
        dec = int(ui.get("decimals") or 0)
        return amt / (10**dec) if dec >= 0 else amt
    except (TypeError, ValueError):
        return 0.0


def parse_wallet_token_deltas(
    tx: dict[str, Any],
    wallet: str,
) -> list[dict[str, Any]]:
    """Return list of {mint, pre, post, delta, side} for wallet's token changes."""
    meta = tx.get("meta") or {}
    if meta.get("err"):
        return []
    pre = meta.get("preTokenBalances") or []
    post = meta.get("postTokenBalances") or []
    wallet = (wallet or "").strip()

    # index by (mint, owner)
    def _map(rows: list) -> dict[tuple[str, str], float]:
        out: dict[tuple[str, str], float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            owner = str(row.get("owner") or "").strip()
            mint = str(row.get("mint") or "").strip()
            if not owner or not mint:
                continue
            out[(mint, owner)] = _ui_amount(row)
        return out

    pre_m = _map(pre)
    post_m = _map(post)
    keys = set(pre_m) | set(post_m)
    deltas: list[dict[str, Any]] = []
    for mint, owner in keys:
        if owner != wallet:
            continue
        if mint in _SKIP_MINTS:
            continue
        p0 = pre_m.get((mint, owner), 0.0)
        p1 = post_m.get((mint, owner), 0.0)
        d = p1 - p0
        if abs(d) < _MIN_UI_DELTA and not (p0 > 0 and p1 <= 0):
            # allow full exit even if ui rounding small if went to zero
            if not (p0 >= 1 and p1 < 0.5):
                continue
        if d > 0:
            side = "buy"
        elif d < 0 or (p0 > 0 and p1 < p0 * 0.05):
            side = "sell"
        else:
            continue
        deltas.append(
            {
                "mint": mint,
                "pre": p0,
                "post": p1,
                "delta": d,
                "side": side,
            }
        )
    return deltas


def _pos_key(wallet: str, mint: str) -> str:
    return f"{wallet}:{mint}"


def _record_event(ev: dict[str, Any]) -> None:
    _events.append(ev)
    if len(_events) > 250:
        del _events[:-200]


def _fmt_usd(n: Any) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        return "?"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"${v / 1e3:.1f}k"
    return f"${v:.0f}"


async def _fetch_mcap_symbol(mint: str) -> dict[str, Any]:
    """Best-effort symbol + mcap for alert richness."""
    out: dict[str, Any] = {"symbol": "?", "name": "", "mcap": None}
    try:
        client = get_client()
        r = await client.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=8.0,
        )
        if r.status_code == 200:
            pairs = (r.json() or {}).get("pairs") or []
            if pairs:
                p0 = pairs[0]
                base = p0.get("baseToken") or {}
                out["symbol"] = base.get("symbol") or "?"
                out["name"] = base.get("name") or ""
                try:
                    out["mcap"] = float(p0.get("marketCap") or p0.get("fdv") or 0) or None
                except (TypeError, ValueError):
                    pass
                return out
    except Exception:
        pass
    try:
        client = get_client()
        r = await client.get(
            f"https://frontend-api.pump.fun/coins/{mint}",
            timeout=8.0,
        )
        if r.status_code == 200:
            coin = r.json() or {}
            if isinstance(coin, dict):
                out["symbol"] = coin.get("symbol") or out["symbol"]
                out["name"] = coin.get("name") or ""
                try:
                    out["mcap"] = float(coin.get("usd_market_cap") or 0) or None
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return out


def format_fomo_telegram(ev: dict[str, Any]) -> str:
    side = str(ev.get("side") or "").lower()
    label = str(ev.get("wallet_label") or "Elite")
    sym = str(ev.get("symbol") or "?")
    mint = str(ev.get("mint") or "")
    mcap = ev.get("mcap")
    sig = str(ev.get("signature") or "")
    padre = f"{PADRE_TRADE_URL}/trade/solana/{mint}" if mint else "#"
    pump = f"https://pump.fun/coin/{mint}" if mint else "#"
    solscan_tx = f"https://solscan.io/tx/{sig}" if sig else "#"
    solscan_w = f"https://solscan.io/account/{ev.get('wallet')}" if ev.get("wallet") else "#"

    if side == "buy":
        head = f"🔥 <b>FOMO BUY</b> · {_esc(label)}"
        act = "just <b>BOUGHT</b>"
    else:
        head = f"🚪 <b>FOMO EXIT</b> · {_esc(label)}"
        act = "just <b>SOLD / EXITED</b>"

    pre = ev.get("pre")
    post = ev.get("post")
    bag = ""
    try:
        if pre is not None and post is not None:
            bag = f"\nBag: {float(pre):,.0f} → {float(post):,.0f}"
    except (TypeError, ValueError):
        bag = ""

    mcap_s = f" · mcap {_fmt_usd(mcap)}" if mcap else ""
    hold = ""
    if side == "sell" and ev.get("hold_sec"):
        try:
            mins = float(ev["hold_sec"]) / 60.0
            hold = f"\n⏱ Held ~{mins:.1f}m"
        except (TypeError, ValueError):
            pass

    return (
        f"{head}\n"
        f"{_esc(label)} {act} <b>${_esc(sym)}</b>{mcap_s}"
        f"{bag}{hold}\n"
        f"<a href=\"{padre}\">Padre</a> · <a href=\"{pump}\">Pump</a> · "
        f"<a href=\"{solscan_tx}\">Tx</a> · <a href=\"{solscan_w}\">Wallet</a>\n"
        f"<code>{_esc(mint)}</code>\n"
        f"<i>FOMO channel · not financial advice · size small</i>"
    )


def _esc(s: Any) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def _notify(ev: dict[str, Any]) -> None:
    if not FOMO_ALERT_TELEGRAM:
        return
    try:
        from services.telegram_alerts import send_telegram

        msg = format_fomo_telegram(ev)
        chat = (TELEGRAM_FOMO_CHAT_ID or "").strip() or None
        await send_telegram(msg, chat_id=chat)
    except Exception as exc:
        logger.warning("fomo telegram failed: %s", exc)


async def process_delta(
    *,
    wallet: str,
    wallet_label: str,
    tier: str,
    sig: str,
    delta: dict[str, Any],
    notify: bool,
) -> dict[str, Any] | None:
    mint = str(delta.get("mint") or "")
    side = str(delta.get("side") or "")
    if not mint or side not in ("buy", "sell"):
        return None
    meta = await _fetch_mcap_symbol(mint)
    now = time.time()
    pk = _pos_key(wallet, mint)
    ev: dict[str, Any] = {
        "ts": now,
        "side": side,
        "wallet": wallet,
        "wallet_label": wallet_label,
        "tier": tier,
        "mint": mint,
        "symbol": meta.get("symbol") or "?",
        "name": meta.get("name") or "",
        "mcap": meta.get("mcap"),
        "pre": delta.get("pre"),
        "post": delta.get("post"),
        "delta": delta.get("delta"),
        "signature": sig,
    }

    if side == "buy":
        prev = _positions.get(pk) or {}
        _positions[pk] = {
            "wallet": wallet,
            "wallet_label": wallet_label,
            "mint": mint,
            "symbol": ev["symbol"],
            "opened_at": prev.get("opened_at") or now,
            "entry_mcap": meta.get("mcap") or prev.get("entry_mcap"),
            "last_sig": sig,
            "last_post": delta.get("post"),
            "buys": int(prev.get("buys") or 0) + 1,
        }
        ev["event"] = "FOMO_BUY"
    else:
        prev = _positions.get(pk) or {}
        if prev.get("opened_at"):
            ev["hold_sec"] = now - float(prev["opened_at"])
            ev["entry_mcap"] = prev.get("entry_mcap")
        post = float(delta.get("post") or 0)
        # Close position if bag ~gone
        if post < max(1.0, float(delta.get("pre") or 0) * 0.05):
            _positions.pop(pk, None)
            ev["event"] = "FOMO_EXIT"
        else:
            if pk in _positions:
                _positions[pk]["last_post"] = post
                _positions[pk]["last_sig"] = sig
            ev["event"] = "FOMO_SELL_PARTIAL"
            # Still alert partials as EXIT-style for FOMO channel
            ev["side"] = "sell"

    _record_event(ev)
    if notify:
        await _notify(ev)
    return ev


async def poll_once(*, seed: bool = False) -> dict[str, Any]:
    """One FOMO poll cycle over tracked wallets (round-robin to avoid RPC 429)."""
    global _rr_index
    if not FOMO_ENABLED:
        return {"ok": False, "error": "FOMO disabled", "buys": 0, "exits": 0}

    wallets = fomo_wallets()
    buys = 0
    exits = 0
    new_sigs = 0
    errors: list[str] = []
    emitted: list[dict[str, Any]] = []
    sig_limit = max(2, min(12, int(FOMO_SIGS_PER_WALLET)))
    paid = rpc_is_paid()
    # Public RPC: few wallets/cycle + longer gaps. Paid: more aggressive.
    per_cycle = len(wallets) if seed else max(1, int(FOMO_WALLETS_PER_CYCLE))
    if paid and not seed:
        per_cycle = max(per_cycle, min(8, len(wallets) or 1))
    gap = 0.35 if paid else 1.1
    tx_gap = 0.2 if paid else 0.55

    if not wallets:
        _last_status.update(
            {
                "ts": time.time(),
                "cycle": int(_last_status.get("cycle") or 0) + 1,
                "wallets": 0,
                "new_sigs": 0,
                "buys": 0,
                "exits": 0,
                "errors": ["No FOMO wallets — add some on /fomo"],
                "emitted": 0,
            }
        )
        return {"ok": True, "wallets": 0, "buys": 0, "exits": 0, "events": []}

    # Round-robin slice
    n = len(wallets)
    start = 0 if seed else (_rr_index % n)
    batch: list[dict[str, Any]] = []
    for i in range(min(per_cycle if not seed else n, n)):
        batch.append(wallets[(start + i) % n])
    if not seed:
        _rr_index = (start + len(batch)) % n

    for w in batch:
        addr = str(w.get("address") or "").strip()
        label = str(w.get("label") or addr[:6])
        tier = str(w.get("tier") or "S")
        if not addr:
            continue
        try:
            sigs = await _sigs_for_wallet(addr, sig_limit)
        except Exception as exc:
            err = str(exc)
            if "429" in err:
                errors.append(f"{label}: RPC rate-limited (add HELIUS_API_KEY)")
                await asyncio.sleep(2.0 if not paid else 0.5)
            else:
                errors.append(f"{label}:sigs {err[:55]}")
                await asyncio.sleep(gap)
            continue

        # Newest first from RPC; process oldest first for chronological opens
        fresh = []
        for s in sigs:
            sig = str(s.get("signature") or "")
            if not sig or sig in _seen:
                continue
            if s.get("err"):
                _seen[sig] = time.time()
                continue
            fresh.append(sig)

        if seed:
            for sig in fresh:
                _seen[sig] = time.time()
            await asyncio.sleep(gap)
            continue

        # Only process a few newest per wallet per cycle
        for sig in reversed(fresh[-3:]):
            _seen[sig] = time.time()
            new_sigs += 1
            try:
                tx = await _get_tx(sig)
            except Exception as exc:
                err = str(exc)
                if "429" in err:
                    errors.append(f"{label}:tx rate-limited")
                    await asyncio.sleep(1.5)
                    break
                errors.append(f"{label}:tx {err[:45]}")
                await asyncio.sleep(tx_gap)
                continue
            if not tx:
                continue
            deltas = parse_wallet_token_deltas(tx, addr)
            for d in deltas:
                try:
                    ev = await process_delta(
                        wallet=addr,
                        wallet_label=label,
                        tier=tier,
                        sig=sig,
                        delta=d,
                        notify=True,
                    )
                except Exception as exc:
                    errors.append(f"{label}:delta {str(exc)[:45]}")
                    continue
                if not ev:
                    continue
                emitted.append(ev)
                if ev.get("side") == "buy":
                    buys += 1
                else:
                    exits += 1
            await asyncio.sleep(tx_gap)
        await asyncio.sleep(gap)

    _persist()
    _last_status.update(
        {
            "ts": time.time(),
            "cycle": int(_last_status.get("cycle") or 0) + 1,
            "wallets": len(wallets),
            "polled": len(batch),
            "new_sigs": new_sigs,
            "buys": buys,
            "exits": exits,
            "errors": errors[-8:],
            "seeded": bool(_last_status.get("seeded") or seed),
            "emitted": len(emitted),
            "rpc_paid": paid,
        }
    )
    if seed:
        _last_status["seeded"] = True
        logger.info("FOMO seeded %s wallets (no alerts)", len(wallets))
    elif buys or exits:
        logger.info("FOMO cycle buys=%s exits=%s new_sigs=%s", buys, exits, new_sigs)
    return {
        "ok": True,
        "seed": seed,
        "wallets": len(wallets),
        "polled": len(batch),
        "new_sigs": new_sigs,
        "buys": buys,
        "exits": exits,
        "events": emitted,
        "errors": errors[:8],
    }


async def background_fomo_loop() -> None:
    """Fast poll loop for FOMO buy/exit alerts."""
    await asyncio.sleep(18)
    if not FOMO_ENABLED:
        logger.info("FOMO watch disabled (FOMO_ENABLED=0)")
        while True:
            await asyncio.sleep(120)
            if FOMO_ENABLED:
                break
    _boot_state()
    try:
        await poll_once(seed=True)
    except Exception as exc:
        logger.warning("FOMO seed failed: %s", exc)

    try:
        if FOMO_ALERT_TELEGRAM:
            from services.telegram_alerts import send_telegram, configured

            if configured():
                n = len(fomo_wallets())
                chat = (TELEGRAM_FOMO_CHAT_ID or "").strip() or None
                await send_telegram(
                    "🔥 <b>FOMO APING CHANNEL ON</b>\n"
                    f"Watching <b>{n}</b> elite wallets for buys & exits\n"
                    f"Poll ~{FOMO_POLL_SEC:.0f}s · /fomo\n"
                    "<i>Alerts fire after tx confirm — size small, not financial advice</i>",
                    chat_id=chat,
                )
    except Exception as exc:
        logger.debug("fomo seed telegram: %s", exc)

    while True:
        try:
            if FOMO_ENABLED:
                await poll_once(seed=False)
        except Exception as exc:
            logger.warning("FOMO poll error: %s", exc)
            _last_status["errors"] = [str(exc)[:120]]
        await asyncio.sleep(max(6.0, float(FOMO_POLL_SEC)))


# Boot empty state for import-time status()
_boot_state()
