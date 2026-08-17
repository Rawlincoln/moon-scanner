"""Padre Alpha Tracker multiplex client (optional, needs PADRE_AUTH_TOKEN).

Wire protocol (from trade.padre.gg JS):
  hello:      server → [3]
  auth:       client → [1, bearer_token, fre]   fre like \"d-<11chars>\"
  auth ok:    server → [2, ...]
  open sub:   client → [4, conn_id, path]
  data:       server → [5|6|7, conn_id, payload]
  close sub:  client → [6, conn_id, code]

Path for public tracker feed:
  /mentions/subscribe-feed/tracker?minPublicMentions=1&maxTokenAgeInSeconds=86400
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any

from config import PADRE_AUTH_TOKEN

logger = logging.getLogger("moon-scanner.padre-alpha-ws")

MUX_URL = "wss://backend.padre.gg/_multiplex"
TRACKER_PATH = (
    "/mentions/subscribe-feed/tracker"
    "?minPublicMentions=1"
    "&maxTokenAgeInSeconds=86400"
)

_recent: deque[dict[str, Any]] = deque(maxlen=200)
_status: dict[str, Any] = {
    "running": False,
    "authed": False,
    "last_msg_ts": 0.0,
    "mentions": 0,
    "error": None,
    "source": "padre_ws",
}
_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def status() -> dict[str, Any]:
    return {
        **_status,
        "token_set": bool(PADRE_AUTH_TOKEN),
        "buffer": len(_recent),
        "sample": list(_recent)[:3],
    }


def recent_mentions(*, limit: int = 40) -> list[dict[str, Any]]:
    items = list(_recent)
    items.reverse()
    return items[:limit]


def _fre() -> str:
    return f"d-{uuid.uuid4().hex[:11]}"


def _extract_mentions(payload: Any) -> list[dict[str, Any]]:
    """Best-effort parse of tracker feed messages into mint rows."""
    out: list[dict[str, Any]] = []
    now = time.time()

    def take(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        mint = (
            obj.get("tokenAddress")
            or obj.get("mint")
            or obj.get("address")
            or (obj.get("token") or {}).get("address")
            or (obj.get("token") or {}).get("mint")
            or ""
        )
        mint = str(mint).strip()
        if not mint or len(mint) < 32:
            # nested containers
            for k in ("mention", "item", "data", "token", "update"):
                if isinstance(obj.get(k), dict):
                    take(obj[k])
            for k in ("mentions", "items", "updates", "snapshot"):
                v = obj.get(k)
                if isinstance(v, list):
                    for x in v:
                        take(x)
                elif isinstance(v, dict) and isinstance(v.get("items"), list):
                    for x in v["items"]:
                        take(x)
            return

        groups = (
            obj.get("groups")
            or obj.get("alphaGroups")
            or obj.get("groupMentions")
            or obj.get("publicMentions")
            or obj.get("mentions")
            or []
        )
        group_count = 0
        group_names: list[str] = []
        if isinstance(groups, list):
            group_count = len(groups)
            for g in groups[:8]:
                if isinstance(g, dict):
                    group_names.append(
                        str(g.get("name") or g.get("title") or g.get("id") or "")[:40]
                    )
                else:
                    group_names.append(str(g)[:40])
        elif isinstance(groups, (int, float)):
            group_count = int(groups)

        # Some payloads put count flat
        for key in (
            "publicMentionsCount",
            "alphaMentions",
            "mentionCount",
            "groupCount",
            "minPublicMentions",
        ):
            if group_count <= 0 and obj.get(key) is not None:
                try:
                    group_count = int(obj[key])
                except (TypeError, ValueError):
                    pass

        chain = str(
            obj.get("chain")
            or obj.get("chainId")
            or (obj.get("token") or {}).get("chain")
            or "SOLANA"
        ).upper()
        if chain in ("SOL", "SOLANA", ""):
            chain = "solana"
        elif chain in ("BSC", "BASE", "ETH", "ETHEREUM"):
            chain = chain.lower()
            if chain == "ethereum":
                chain = "ethereum"
        else:
            chain = chain.lower()

        out.append(
            {
                "tokenAddress": mint,
                "chainId": "solana" if chain in ("solana", "sol") else chain,
                "symbol": obj.get("symbol")
                or (obj.get("token") or {}).get("symbol")
                or "",
                "name": obj.get("name") or (obj.get("token") or {}).get("name") or "",
                "group_count": max(1, group_count) if group_count else 1,
                "groups": [g for g in group_names if g],
                "source": "padre_alpha_tracker",
                "ts": now,
                "raw_type": obj.get("type") or obj.get("msgType"),
            }
        )

    if isinstance(payload, dict):
        if payload.get("type") in ("init", "snapshot") and isinstance(
            payload.get("items") or payload.get("snapshot"), list
        ):
            for x in payload.get("items") or payload.get("snapshot") or []:
                take(x)
        else:
            take(payload)
    elif isinstance(payload, list):
        for x in payload:
            take(x)
    return out


async def _run_loop() -> None:
    if not PADRE_AUTH_TOKEN:
        _status.update(
            {
                "running": False,
                "error": "PADRE_AUTH_TOKEN not set — using public group-heat proxy",
            }
        )
        return

    try:
        import msgpack
        import websockets
    except ImportError as exc:
        _status["error"] = f"missing dep: {exc}"
        logger.warning("padre alpha ws deps: %s", exc)
        return

    _status["running"] = True
    backoff = 3.0
    while True:
        try:
            fre = _fre()
            async with websockets.connect(
                MUX_URL,
                origin="https://trade.padre.gg",
                additional_headers={
                    "User-Agent": "Mozilla/5.0 MoonScannerAlpha/1.0",
                    "Origin": "https://trade.padre.gg",
                },
                open_timeout=12,
                max_size=8_000_000,
                ping_interval=20,
            ) as ws:
                hello = await asyncio.wait_for(ws.recv(), timeout=8)
                _ = msgpack.unpackb(hello, raw=False)
                await ws.send(
                    msgpack.packb([1, PADRE_AUTH_TOKEN, fre], use_bin_type=True)
                )
                auth = await asyncio.wait_for(ws.recv(), timeout=8)
                auth_msg = msgpack.unpackb(auth, raw=False)
                if not (isinstance(auth_msg, list) and auth_msg and auth_msg[0] == 2):
                    raise RuntimeError(f"auth rejected: {str(auth_msg)[:120]}")
                _status["authed"] = True
                _status["error"] = None
                backoff = 3.0
                await ws.send(
                    msgpack.packb([4, 1, TRACKER_PATH], use_bin_type=True)
                )
                logger.info("Padre Alpha Tracker subscribed")
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=90)
                    msg = msgpack.unpackb(raw, raw=False)
                    if not isinstance(msg, list) or not msg:
                        continue
                    mtype = msg[0]
                    if mtype in (5, 6, 7) and len(msg) >= 3:
                        payload = msg[2]
                        rows = _extract_mentions(payload)
                        for row in rows:
                            if row["chainId"] != "solana":
                                continue
                            _recent.appendleft(row)
                            _status["mentions"] = int(_status.get("mentions") or 0) + 1
                            _status["last_msg_ts"] = time.time()
                    elif mtype == 8:
                        # ping — optional pong not required for many multiplexes
                        continue
        except asyncio.CancelledError:
            _status["running"] = False
            _status["authed"] = False
            raise
        except Exception as exc:
            _status["authed"] = False
            _status["error"] = str(exc)[:200]
            logger.warning("padre alpha ws reconnect in %.0fs: %s", backoff, exc)
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 1.6)


async def start_background() -> None:
    global _task
    async with _lock:
        if _task and not _task.done():
            return
        if not PADRE_AUTH_TOKEN:
            _status["error"] = "PADRE_AUTH_TOKEN not set — public proxy only"
            return
        _task = asyncio.create_task(_run_loop(), name="padre-alpha-ws")


async def stop_background() -> None:
    global _task
    async with _lock:
        if _task and not _task.done():
            _task.cancel()
            try:
                await _task
            except asyncio.CancelledError:
                pass
        _task = None
        _status["running"] = False
        _status["authed"] = False
