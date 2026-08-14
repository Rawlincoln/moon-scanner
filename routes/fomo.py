"""FOMO aping channel — live buy/exit feed + wallet management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.paths import BASE_DIR
from config import FOMO_OPEN_MANAGE
from services.alert_auth import force_auth_ok
from services.fomo_wallets import add_wallet, list_wallets, remove_wallet, valid_address
from services.fomo_watch import poll_once, seed_wallet_history, status as fomo_status

router = APIRouter(tags=["fomo"])


class FomoWalletIn(BaseModel):
    address: str = Field(..., min_length=32, max_length=64)
    label: str | None = Field(default=None, max_length=40)
    tier: str = Field(default="S", max_length=8)
    note: str | None = Field(default=None, max_length=160)


def _require_manage_auth(x_admin_key: str | None) -> None:
    """FOMO desk: open manage by default so the UI works without pasting keys.

    Set FOMO_OPEN_MANAGE=0 to require X-Admin-Key / cron secret.
    """
    if FOMO_OPEN_MANAGE:
        return
    if not force_auth_ok(x_admin_key=x_admin_key, bot_wired=False):
        raise HTTPException(
            status_code=401,
            detail="X-Admin-Key required (or set FOMO_OPEN_MANAGE=1)",
        )


@router.get("/fomo")
async def fomo_page():
    return FileResponse(BASE_DIR / "static" / "fomo.html")


@router.get("/api/fomo")
async def fomo_status_api():
    """Recent FOMO events + watched wallets + poll health."""
    return fomo_status()


@router.get("/api/fomo/wallets")
async def fomo_list_wallets():
    """List managed FOMO wallets (same set the poller watches)."""
    wallets = list_wallets()
    return {"ok": True, "count": len(wallets), "wallets": wallets}


@router.post("/api/fomo/wallets")
async def fomo_add_wallet(
    body: FomoWalletIn,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Add a wallet to FOMO watch — starts buy/exit Telegram alerts after seed."""
    _require_manage_auth(x_admin_key)
    addr = (body.address or "").strip()
    if not valid_address(addr):
        raise HTTPException(status_code=400, detail="Invalid Solana wallet address")
    try:
        row = add_wallet(
            addr,
            label=body.label,
            tier=(body.tier or "S").upper(),
            note=body.note or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Don't alert on historical activity for this wallet
    seeded = 0
    try:
        seeded = await seed_wallet_history(addr)
    except Exception:
        seeded = 0

    return {
        "ok": True,
        "wallet": row,
        "seeded_sigs": seeded,
        "message": f"Watching {row['label']} — new buys/exits will alert on Telegram",
    }


@router.delete("/api/fomo/wallets/{address}")
async def fomo_remove_wallet(
    address: str,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Stop watching a wallet (no more FOMO alerts for it)."""
    _require_manage_auth(x_admin_key)
    addr = (address or "").strip()
    if not addr:
        raise HTTPException(status_code=400, detail="address required")
    ok = remove_wallet(addr)
    if not ok:
        raise HTTPException(status_code=404, detail="Wallet not on FOMO list")
    return {"ok": True, "removed": addr, "message": "Removed — no longer watched"}


@router.post("/api/fomo/poll")
async def fomo_force_poll(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """Force one FOMO poll cycle (admin)."""
    if not force_auth_ok(x_admin_key=x_admin_key, bot_wired=False):
        raise HTTPException(status_code=401, detail="X-Admin-Key required")
    return await poll_once(seed=False)
