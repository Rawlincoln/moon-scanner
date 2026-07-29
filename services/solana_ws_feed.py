"""Realtime launch detection via Solana WebSocket.

Modes (SOLANA_WS_MODE):
  auto         — transactionSubscribe on paid WSS, else logsSubscribe
  transaction  — Helius/QuickNode-style full tx stream (best mint resolution)
  logs         — standard logsSubscribe + optional getTransaction

Env:
  HELIUS_API_KEY or SOLANA_RPC_WSS / SOLANA_RPC_HTTP
  DISABLE_SOLANA_WS=1
  YELLOWSTONE_ONLY=1  — skip this feed when Yellowstone is configured
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from config import (
    DISABLE_SOLANA_WS,
    YELLOWSTONE_GRPC_ENDPOINT,
    YELLOWSTONE_ONLY,
)
from services.realtime_bus import PUMPFUN_PROGRAM_ID, LaunchEvent, realtime_bus
from services.realtime_rpc import (
    classify_logs,
    extract_mint_from_tx_notification,
    http_url,
    is_paid_wss,
    mint_from_account_keys,
    mint_from_log_lines,
    redact_rpc_url,
    resolve_ws_mode,
    wss_url,
)

logger = logging.getLogger("moon-scanner.solana_ws")


def _ys_only() -> bool:
    return YELLOWSTONE_ONLY and bool(YELLOWSTONE_GRPC_ENDPOINT)


class SolanaLogsFeed:
    """Pump.fun program stream → realtime_bus."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._sig_seen: set[str] = set()
        self._mode = "logs"

    async def start(self) -> None:
        if DISABLE_SOLANA_WS:
            realtime_bus.feed_status["solana_ws"] = {
                "running": False,
                "detail": "Disabled via DISABLE_SOLANA_WS",
            }
            return
        if _ys_only():
            realtime_bus.feed_status["solana_ws"] = {
                "running": False,
                "detail": "Skipped (YELLOWSTONE_ONLY=1)",
            }
            return
        self._stop.clear()
        self._mode = resolve_ws_mode()
        self._task = asyncio.create_task(self._loop(), name="solana_ws")
        realtime_bus.feed_status["solana_ws"] = {
            "running": True,
            "wss": redact_rpc_url(wss_url()),
            "http": redact_rpc_url(http_url()),
            "program": PUMPFUN_PROGRAM_ID,
            "mode": self._mode,
            "paid": is_paid_wss(),
            "detail": f"{self._mode} starting…",
        }

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if "solana_ws" in realtime_bus.feed_status:
            realtime_bus.feed_status["solana_ws"]["running"] = False

    async def _loop(self) -> None:
        try:
            import websockets
        except ImportError:
            realtime_bus.feed_status["solana_ws"] = {
                "running": False,
                "detail": "pip install websockets for realtime WSS",
            }
            logger.warning("websockets not installed — Solana WS feed off")
            return

        backoff = 2.0
        prefer_tx = resolve_ws_mode() == "transaction"
        while not self._stop.is_set():
            wss = wss_url()
            mode = "transaction" if prefer_tx else "logs"
            try:
                async with websockets.connect(
                    wss,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=16 * 1024 * 1024,
                    close_timeout=5,
                ) as ws:
                    if mode == "transaction":
                        ok = await self._subscribe_transaction(ws)
                        if not ok:
                            logger.warning(
                                "transactionSubscribe failed — falling back to logsSubscribe"
                            )
                            prefer_tx = False
                            mode = "logs"
                            ok = await self._subscribe_logs(ws)
                            if not ok:
                                raise RuntimeError("logsSubscribe also failed")
                        else:
                            self._mode = "transaction"
                    else:
                        ok = await self._subscribe_logs(ws)
                        if not ok:
                            raise RuntimeError("logsSubscribe failed")
                        self._mode = "logs"

                    self._set_connected(wss, mode)
                    backoff = 2.0
                    logger.info(
                        "Solana WS connected mode=%s %s",
                        mode,
                        redact_rpc_url(wss),
                    )

                    while not self._stop.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(msg, bytes):
                            msg = msg.decode("utf-8", errors="ignore")
                        if mode == "transaction":
                            await self._handle_tx_message(msg)
                        else:
                            await self._handle_logs_message(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                realtime_bus.feed_status["solana_ws"] = {
                    "running": True,
                    "connected": False,
                    "mode": self._mode,
                    "detail": f"reconnect in {backoff:.0f}s: {exc}",
                    "wss": redact_rpc_url(wss),
                }
                logger.warning("Solana WS feed error: %s", exc)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(60.0, backoff * 1.5)

    def _set_connected(self, wss: str, mode: str) -> None:
        realtime_bus.feed_status["solana_ws"] = {
            "running": True,
            "connected": True,
            "wss": redact_rpc_url(wss),
            "http": redact_rpc_url(http_url()),
            "program": PUMPFUN_PROGRAM_ID,
            "mode": mode,
            "paid": is_paid_wss(wss),
            "detail": (
                f"{'transactionSubscribe' if mode == 'transaction' else 'logsSubscribe'} "
                "active (processed)"
            ),
        }
        cur = str(realtime_bus.feed_status.get("mode") or "")
        if cur in ("", "starting", "pump_poll", "offline"):
            realtime_bus.feed_status["mode"] = "solana_ws+poll"
        elif "yellowstone" in cur and "ws" not in cur:
            realtime_bus.feed_status["mode"] = "yellowstone+ws+poll"

    async def _subscribe_logs(self, ws: Any) -> bool:
        sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [PUMPFUN_PROGRAM_ID]},
                {"commitment": "processed"},
            ],
        }
        await ws.send(json.dumps(sub))
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        ack = json.loads(raw)
        if ack.get("error"):
            logger.warning("logsSubscribe error: %s", ack["error"])
            return False
        realtime_bus.feed_status.setdefault("solana_ws", {})["sub_id"] = ack.get(
            "result"
        )
        return True

    async def _subscribe_transaction(self, ws: Any) -> bool:
        """Helius / enhanced-provider transactionSubscribe (full jsonParsed)."""
        sub = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "transactionSubscribe",
            "params": [
                {
                    "failed": False,
                    "accountInclude": [PUMPFUN_PROGRAM_ID],
                },
                {
                    "commitment": "processed",
                    "encoding": "jsonParsed",
                    "transactionDetails": "full",
                    "showRewards": False,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        await ws.send(json.dumps(sub))
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        ack = json.loads(raw)
        if ack.get("error"):
            logger.warning("transactionSubscribe error: %s", ack["error"])
            return False
        realtime_bus.feed_status.setdefault("solana_ws", {})["sub_id"] = ack.get(
            "result"
        )
        return True

    async def _handle_tx_message(self, msg: str) -> None:
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return
        # subscription ack may arrive again — ignore non-notifications
        if data.get("method") not in (
            "transactionNotification",
            None,
        ) and "params" not in data:
            return
        result = (data.get("params") or {}).get("result") or {}
        if not isinstance(result, dict) or not result:
            return
        sig = str(result.get("signature") or "")
        if not sig or sig in self._sig_seen:
            return
        self._sig_seen.add(sig)
        if len(self._sig_seen) > 5000:
            self._sig_seen = set(list(self._sig_seen)[-2500:])

        # Skip failed
        tx_wrap = result.get("transaction") or {}
        meta = (tx_wrap.get("meta") if isinstance(tx_wrap, dict) else None) or {}
        if isinstance(meta, dict) and meta.get("err"):
            return

        mint, kind, slot = extract_mint_from_tx_notification(result)
        if not mint:
            return
        self._push(mint, sig, kind, slot, source="solana_ws_tx")

    async def _handle_logs_message(self, msg: str) -> None:
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return
        val = (data.get("params") or {}).get("result") or {}
        value = val.get("value") or val
        if not isinstance(value, dict):
            return
        sig = value.get("signature") or ""
        logs = value.get("logs") or []
        if value.get("err"):
            return
        if not sig or sig in self._sig_seen:
            return
        self._sig_seen.add(sig)
        if len(self._sig_seen) > 5000:
            self._sig_seen = set(list(self._sig_seen)[-2500:])

        kind = classify_logs(logs)
        mint = mint_from_log_lines(logs)
        if not mint:
            mint = await self._resolve_mint_rpc(sig, logs)
        if not mint:
            return
        self._push(
            mint,
            sig,
            "create" if kind == "create" else ("sell" if kind == "sell" else "buy"),
            None,
            source="solana_ws",
        )

    def _push(
        self,
        mint: str,
        sig: str,
        kind: str,
        slot: int | None,
        *,
        source: str,
    ) -> None:
        bus_kind = "create" if kind == "create" else "buy"
        realtime_bus.push(
            LaunchEvent(
                mint=mint,
                source=source,
                seen_at=time.time(),
                signature=sig,
                slot=slot,
                kind=bus_kind,
                meta={"logs_kind": kind},
            )
        )
        st = realtime_bus.feed_status.setdefault("solana_ws", {})
        st["last_event"] = time.time()
        st["detail"] = f"live — last {kind} {mint[:8]}…"
        st["connected"] = True

    async def _resolve_mint_rpc(self, sig: str, logs: list) -> str | None:
        """Fallback getTransaction on SOLANA_RPC_HTTP (paid HTTP only).

        Public mainnet RPC rate-limits hard (429s). Without Helius/paid RPC we
        only parse mints from log lines — never spam getTransaction.
        """
        mint = mint_from_log_lines(logs)
        if mint:
            return mint
        if not is_paid_wss():
            return None
        try:
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    sig,
                    {
                        "encoding": "jsonParsed",
                        "commitment": "confirmed",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
            }
            from services.http_client import get_client

            client = get_client()
            resp = await client.post(http_url(), json=body, timeout=4.0)
            if resp.status_code != 200:
                return None
            result = (resp.json() or {}).get("result")
            if not result:
                return None
            m, _, _ = extract_mint_from_tx_notification(result)
            if m:
                return m
            # legacy path via account keys on raw result
            tx = result.get("transaction") or {}
            msg = tx.get("message") or {}
            keys = []
            for k in msg.get("accountKeys") or []:
                if isinstance(k, str):
                    keys.append(k)
                elif isinstance(k, dict):
                    keys.append(str(k.get("pubkey") or ""))
            return mint_from_account_keys(keys)
        except Exception as exc:
            logger.debug("getTransaction %s: %s", sig[:12], exc)
        return None


solana_ws_feed = SolanaLogsFeed()
