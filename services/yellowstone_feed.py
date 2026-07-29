"""Realtime layer: Yellowstone (optional) + Solana logsSubscribe + fast pump poll.

Latency (typical):
  ShredStream     earliest (not embedded — set SHREDSTREAM_ENDPOINT for status)
  Yellowstone     ~5–50 ms when provider SDK fully wired
  logsSubscribe   tens–hundreds of ms (this module — free/public or paid WSS)
  pump HTTP poll  ~2s fallback

Env:
  YELLOWSTONE_GRPC_ENDPOINT / YELLOWSTONE_GRPC_TOKEN / YELLOWSTONE_COMMITMENT
  SOLANA_RPC_WSS / SOLANA_RPC_HTTP
  REALTIME_PUMP_POLL_SEC (default 3 when WS active, 2 otherwise)
  DISABLE_SOLANA_WS=1 to turn off logsSubscribe
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from config import PUMPFUN_API_URL, REQUEST_TIMEOUT
from services.http_client import get as http_get
from services.realtime_bus import PUMPFUN_PROGRAM_ID, LaunchEvent, realtime_bus
from services.solana_ws_feed import solana_ws_feed

logger = logging.getLogger("moon-scanner.realtime")

LATENCY_STACK = [
    {
        "layer": "ShredStream (raw UDP shreds)",
        "latency": "0 ms reference / earliest",
        "notes": "Jito official sunsetting Sep 2026; third-party feeds available.",
    },
    {
        "layer": "Yellowstone gRPC (processed)",
        "latency": "~5–50 ms typical",
        "notes": "Geyser over gRPC. Set YELLOWSTONE_GRPC_ENDPOINT.",
    },
    {
        "layer": "Solana logsSubscribe (this app)",
        "latency": "~50–200 ms typical",
        "notes": "Pump.fun program mentions → bus. SOLANA_RPC_WSS.",
    },
    {
        "layer": "pump.fun HTTP poll",
        "latency": "~1–3 s",
        "notes": "Safety net + discovery for moon scan.",
    },
]


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def yellowstone_configured() -> bool:
    return bool(_env("YELLOWSTONE_GRPC_ENDPOINT"))


def shredstream_configured() -> bool:
    return bool(_env("SHREDSTREAM_ENDPOINT"))


class YellowstoneFeed:
    """Orchestrates realtime sources → realtime_bus."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._stop.clear()
        endpoint = _env("YELLOWSTONE_GRPC_ENDPOINT")
        token = _env("YELLOWSTONE_GRPC_TOKEN")
        commitment = _env("YELLOWSTONE_COMMITMENT", "processed")
        shred = _env("SHREDSTREAM_ENDPOINT")

        # Default poll slower when WS is on (save HTTP)
        default_poll = "3.0" if not _env("DISABLE_SOLANA_WS") else "2.0"
        poll_sec = float(_env("REALTIME_PUMP_POLL_SEC", default_poll) or default_poll)

        realtime_bus.feed_status["yellowstone"] = {
            "configured": bool(endpoint),
            "connected": False,
            "endpoint": (endpoint[:48] + "…") if len(endpoint) > 48 else endpoint,
            "commitment": commitment,
            "program_filter": PUMPFUN_PROGRAM_ID,
            "detail": (
                "Not set — using logsSubscribe + pump poll"
                if not endpoint
                else "Probing…"
            ),
        }
        realtime_bus.feed_status["shredstream"] = {
            "configured": bool(shred),
            "connected": False,
            "endpoint": (shred[:48] + "…") if len(shred) > 48 else shred,
            "detail": (
                "Optional earliest layer (custom UDP client)."
                if not shred
                else "Configured — pair with external deshred client."
            ),
        }
        realtime_bus.feed_status["mode"] = "starting"

        # 1) Solana logsSubscribe (real push stream)
        try:
            await solana_ws_feed.start()
        except Exception as exc:
            logger.warning("solana_ws start failed: %s", exc)

        # 2) Yellowstone channel probe / optional loop
        if endpoint:
            self._tasks.append(
                asyncio.create_task(
                    self._yellowstone_loop(endpoint, token, commitment),
                    name="yellowstone",
                )
            )

        # 3) Pump HTTP poll (discovery safety net)
        realtime_bus.feed_status["pump_poll"] = {
            "running": True,
            "interval_sec": poll_sec,
            "detail": "Fast pump.fun created-coins poll",
        }
        self._tasks.append(
            asyncio.create_task(self._pump_poll_loop(poll_sec), name="pump_poll")
        )

        # Mode summary
        ws_on = (realtime_bus.feed_status.get("solana_ws") or {}).get("running")
        if endpoint and ws_on:
            realtime_bus.feed_status["mode"] = "yellowstone+ws+poll"
        elif ws_on:
            realtime_bus.feed_status["mode"] = "solana_ws+poll"
        elif endpoint:
            realtime_bus.feed_status["mode"] = "yellowstone+poll"
        else:
            realtime_bus.feed_status["mode"] = "pump_poll"

        logger.info("Realtime feed mode=%s", realtime_bus.feed_status["mode"])

    async def stop(self) -> None:
        self._stop.set()
        try:
            await solana_ws_feed.stop()
        except Exception:
            pass
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        realtime_bus.feed_status["mode"] = "offline"
        if "pump_poll" in realtime_bus.feed_status:
            realtime_bus.feed_status["pump_poll"]["running"] = False

    async def _pump_poll_loop(self, interval: float) -> None:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Origin": "https://pump.fun",
            "Referer": "https://pump.fun/",
        }
        seen: set[str] = set()
        while not self._stop.is_set():
            try:
                resp = await http_get(
                    f"{PUMPFUN_API_URL}/coins",
                    params={
                        "limit": 40,
                        "offset": 0,
                        "sort": "created_timestamp",
                        "order": "DESC",
                        "includeNsfw": "false",
                    },
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code == 200:
                    coins = resp.json()
                    n_new = 0
                    if isinstance(coins, list):
                        for coin in coins:
                            mint = str(coin.get("mint") or "").strip()
                            if not mint or mint in seen:
                                continue
                            seen.add(mint)
                            if len(seen) > 2000:
                                seen = set(list(seen)[-1000:])
                            ts = coin.get("created_timestamp") or 0
                            age_min = (
                                (time.time() * 1000 - ts) / 60_000 if ts else 999
                            )
                            if age_min > 8:
                                continue
                            if realtime_bus.push(
                                LaunchEvent(
                                    mint=mint,
                                    source="pump_poll",
                                    seen_at=time.time(),
                                    kind="create",
                                    meta={
                                        "symbol": coin.get("symbol"),
                                        "name": coin.get("name"),
                                        "mcap": coin.get("usd_market_cap"),
                                        "age_minutes": round(age_min, 2),
                                    },
                                )
                            ):
                                n_new += 1
                    realtime_bus.feed_status["pump_poll"]["last_ok"] = time.time()
                    realtime_bus.feed_status["pump_poll"]["detail"] = (
                        f"OK +{n_new} new / {len(coins) if isinstance(coins, list) else 0} polled"
                    )
            except Exception as exc:
                realtime_bus.feed_status["pump_poll"]["detail"] = f"poll error: {exc}"
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _yellowstone_loop(
        self, endpoint: str, token: str, commitment: str
    ) -> None:
        """Probe Yellowstone and keep status fresh.

        Full Subscribe RPC needs provider protos; logsSubscribe covers live mints.
        When grpc channel is ready we mark connected and rely on solana_ws + poll
        for event payload until full proto client is installed.
        """
        while not self._stop.is_set():
            try:
                ok = await self._probe_yellowstone(endpoint, token)
                realtime_bus.feed_status["yellowstone"]["connected"] = ok
                if ok:
                    realtime_bus.feed_status["yellowstone"]["detail"] = (
                        f"gRPC channel ready ({commitment}). "
                        f"Filter program {PUMPFUN_PROGRAM_ID}. "
                        "Live mints via logsSubscribe; full Geyser decode optional next step."
                    )
                    if "ws" not in str(realtime_bus.feed_status.get("mode")):
                        realtime_bus.feed_status["mode"] = "yellowstone+poll"
                else:
                    realtime_bus.feed_status["yellowstone"]["detail"] = (
                        "Endpoint set but channel not ready / grpcio missing. "
                        "logsSubscribe + poll still active."
                    )
            except Exception as exc:
                realtime_bus.feed_status["yellowstone"]["connected"] = False
                realtime_bus.feed_status["yellowstone"]["detail"] = str(exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                pass

    async def _probe_yellowstone(self, endpoint: str, token: str) -> bool:
        try:
            import grpc  # type: ignore
        except ImportError:
            return False
        host = endpoint.replace("https://", "").replace("http://", "").split("/")[0]
        if ":" not in host:
            host = f"{host}:443"
        try:
            if endpoint.startswith("http://"):
                channel = grpc.aio.insecure_channel(host)
            else:
                channel = grpc.aio.secure_channel(host, grpc.ssl_channel_credentials())
            try:
                await asyncio.wait_for(channel.channel_ready(), timeout=5.0)
                return True
            except Exception:
                return False
            finally:
                await channel.close()
        except Exception:
            return False


def status_payload() -> dict[str, Any]:
    from services.realtime_rpc import is_paid_wss, redact_rpc_url, resolve_ws_mode, wss_url
    from config import HELIUS_API_KEY, SOLANA_WS_MODE

    return {
        "ok": True,
        "latency_stack": LATENCY_STACK,
        "pumpfun_program": PUMPFUN_PROGRAM_ID,
        "yellowstone_configured": yellowstone_configured(),
        "shredstream_configured": shredstream_configured(),
        "solana_ws": {
            "mode": resolve_ws_mode(),
            "mode_env": SOLANA_WS_MODE,
            "paid": is_paid_wss(),
            "wss_preview": redact_rpc_url(wss_url()),
            "helius_key_set": bool(HELIUS_API_KEY),
        },
        "bus": realtime_bus.stats(),
        "setup": {
            "recommended": [
                "Set HELIUS_API_KEY=…  (auto-wires paid WSS + HTTP)",
                "Or SOLANA_RPC_WSS=wss://… + SOLANA_RPC_HTTP=https://…",
                "Optional: SOLANA_WS_MODE=transaction|logs|auto",
            ],
            "yellowstone_env": [
                "YELLOWSTONE_GRPC_ENDPOINT",
                "YELLOWSTONE_GRPC_TOKEN",
                "YELLOWSTONE_COMMITMENT=processed",
                "YELLOWSTONE_ONLY=1  (skip Solana WSS when gRPC set)",
            ],
            "solana_ws_env": [
                "HELIUS_API_KEY",
                "SOLANA_RPC_WSS",
                "SOLANA_RPC_HTTP",
                "SOLANA_WS_MODE=auto",
                "DISABLE_SOLANA_WS=1",
            ],
            "shredstream_env": ["SHREDSTREAM_ENDPOINT"],
            "providers": [
                "https://www.helius.dev/docs/rpc/websocket/transaction-subscribe",
                "https://github.com/rpcpool/yellowstone-grpc",
                "https://docs.triton.one/project-yellowstone/dragons-mouth-grpc-subscriptions",
                "https://www.quicknode.com/",
                "https://chainstack.com/",
            ],
            "subscribe_hint": {
                "account_include": [PUMPFUN_PROGRAM_ID],
                "failed": False,
                "vote": False,
                "commitment": "processed",
                "method_paid": "transactionSubscribe",
                "method_public": "logsSubscribe",
            },
            "notes": [
                "Paid WSS (Helius etc.): transactionSubscribe → full tx, no getTransaction lag.",
                "Public WSS: logsSubscribe + HTTP resolve (rate-limited).",
                "Yellowstone gRPC: channel probe today; full Geyser decode needs provider protos.",
                "Moon UI never waits on sniper latency — bus only prioritizes mints.",
            ],
        },
    }


yellowstone_feed = YellowstoneFeed()
