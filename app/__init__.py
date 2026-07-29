"""Moon Scanner FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.deps import init_shared
from app.lifespan import lifespan
from app.paths import BASE_DIR
from routes.analyze import router as analyze_router
from routes.health import router as health_router
from routes.learning import router as learning_router
from routes.legacy_scan import router as legacy_scan_router
from routes.moon import router as moon_router
from routes.realtime import router as realtime_router
from routes.snipes import router as snipes_router
from routes.trenches import router as trenches_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app() -> FastAPI:
    """Build the FastAPI app (routers, CORS, static, lifespan)."""
    init_shared()

    app = FastAPI(
        title="Moon Scanner",
        description="Identify safe early tokens on EVM & Solana with entry/exit signals",
        version="1.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(moon_router)
    app.include_router(snipes_router)
    app.include_router(realtime_router)
    app.include_router(analyze_router)
    app.include_router(trenches_router)
    app.include_router(health_router)
    app.include_router(learning_router)
    app.include_router(legacy_scan_router)

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    return app
