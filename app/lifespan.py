"""App lifespan: background loops, HTTP pool, realtime feeds."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import (
    BACKGROUND_SCAN_INTERVAL_SEC,
    BACKGROUND_SCAN_PER_COLUMN,
    IS_RENDER,
    RUNNER_RADAR_INTERVAL_SEC,
    rpc_is_paid,
    rpc_provider_label,
    SOLANA_RPC_HTTP,
)
from services import http_client as http_pool
from services.scan_moon import get_moon_outcomes
from services.snipe_outcomes import get_snipe_outcomes
from services.telegram_alerts import background_telegram_alert_loop, configured as tg_configured
from services.scan_trenches import (
    background_runner_alert_loop,
    background_trenches_warm,
)
from services.yellowstone_feed import yellowstone_feed

from app.deps import learning, learning_memory

logger = logging.getLogger("moon-scanner")


async def _background_learning_loop() -> None:
    """Poll tracked tokens; record mcap/dev-dump/crash; update learned model."""
    await asyncio.sleep(12)
    while True:
        try:
            n = await learning.poll_active()
            if n:
                logger.info("Learning poll updated %s active tokens", n)
        except Exception as exc:
            logger.warning("Learning poll failed: %s", exc)
        await asyncio.sleep(35)


async def _background_moon_outcomes_loop() -> None:
    """Poll mcap for moon UI recommendations; finalize win/dump at 15m–6h."""
    await asyncio.sleep(20)
    while True:
        try:
            outcomes = get_moon_outcomes()
            mints = outcomes.active_mints()
            updated = 0
            for mint in mints[:40]:
                mcap = await outcomes.fetch_mcap(mint)
                if mcap is not None:
                    updated += outcomes.apply_mcap(mint, mcap)
                await asyncio.sleep(0.15)
            if updated:
                logger.info(
                    "Moon outcomes updated %s rows (%s mints)", updated, len(mints)
                )
        except Exception as exc:
            logger.warning("Moon outcomes poll failed: %s", exc)
        await asyncio.sleep(90)


async def _background_snipe_outcomes_loop() -> None:
    """Poll mcap for safe-snipe recs; finalize 2× win/dump (shorter horizon)."""
    await asyncio.sleep(28)
    while True:
        try:
            outcomes = get_snipe_outcomes()
            mints = outcomes.active_mints()
            updated = 0
            for mint in mints[:30]:
                mcap = await outcomes.fetch_mcap(mint)
                if mcap is not None:
                    updated += outcomes.apply_mcap(mint, mcap)
                await asyncio.sleep(0.15)
            if updated:
                logger.info(
                    "Snipe outcomes updated %s rows (%s mints)", updated, len(mints)
                )
        except Exception as exc:
            logger.warning("Snipe outcomes poll failed: %s", exc)
        await asyncio.sleep(90)


@asynccontextmanager
async def lifespan(app: FastAPI):
    port = os.getenv("PORT", "8765")
    logger.info(
        "Moon Scanner starting on port %s (deploy=%s)",
        port,
        "render" if IS_RENDER else "local",
    )
    paid = rpc_is_paid()
    provider = rpc_provider_label()
    host = SOLANA_RPC_HTTP.split("?")[0][:64]
    if paid:
        logger.info("Solana RPC: %s (%s) — paid path, fewer 429s", provider, host)
    else:
        logger.warning(
            "Solana RPC: PUBLIC (%s) — expect 429s. "
            "Set HELIUS_API_KEY in .env (see .env.example) or SOLANA_RPC_HTTP/WSS.",
            host,
        )
    try:
        seeded = learning.seed_known_examples()
        if seeded:
            logger.info("Learning seeded %s historical examples", seeded)
    except Exception as exc:
        logger.warning("Learning seed failed: %s", exc)
    try:
        ver = "learn_lr_v3_lottery_dump_2026_07"
        if learning_memory.get_meta("learn_model_version") != ver:
            rebuilt = learning_memory.rebuild_feature_stats()
            learning_memory.set_meta("learn_model_version", ver)
            logger.info("Learning feature_stats rebuilt: %s", rebuilt)
    except Exception as exc:
        logger.warning("Learning rebuild failed: %s", exc)

    tasks: list[asyncio.Task] = []
    if BACKGROUND_SCAN_INTERVAL_SEC > 0 and BACKGROUND_SCAN_PER_COLUMN > 0:
        tasks.append(asyncio.create_task(background_trenches_warm()))
    tasks.append(asyncio.create_task(_background_learning_loop()))
    tasks.append(asyncio.create_task(_background_moon_outcomes_loop()))
    tasks.append(asyncio.create_task(_background_snipe_outcomes_loop()))
    tasks.append(asyncio.create_task(background_telegram_alert_loop()))
    if tg_configured():
        logger.info("Telegram alerts enabled — background scan loop will push picks")
    if RUNNER_RADAR_INTERVAL_SEC > 0:
        tasks.append(asyncio.create_task(background_runner_alert_loop()))

    try:
        await http_pool.startup()
    except Exception as exc:
        logger.warning("http pool start failed: %s", exc)
    try:
        await yellowstone_feed.start()
    except Exception as exc:
        logger.warning("Realtime feed start failed: %s", exc)

    yield

    logger.info("Moon Scanner shutting down")
    try:
        await yellowstone_feed.stop()
    except Exception:
        pass
    try:
        await http_pool.shutdown()
    except Exception:
        pass
    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
