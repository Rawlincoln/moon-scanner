"""Auth helpers for Telegram force endpoints (no circular imports)."""

from __future__ import annotations

import hmac

from config import ADMIN_API_KEY, IS_PRODUCTION, TELEGRAM_CRON_SECRET


def _eq(a: str, b: str) -> bool:
    if not a or not b or len(a) != len(b):
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def cron_secret_ok(key: str | None) -> bool:
    """Query-string / header cron auth: TELEGRAM_CRON_SECRET only."""
    provided = (key or "").strip()
    expected = (TELEGRAM_CRON_SECRET or "").strip()
    if not expected or not provided:
        return False
    return _eq(provided, expected)


def admin_header_ok(x_admin_key: str | None) -> bool:
    expected = (ADMIN_API_KEY or "").strip()
    provided = (x_admin_key or "").strip()
    if not expected:
        return False
    return _eq(provided, expected)


def force_auth_ok(
    *,
    x_admin_key: str | None = None,
    key: str | None = None,
    allow_query_cron: bool = False,
    bot_wired: bool = False,
) -> bool:
    """Auth for force Telegram endpoints.

    - Query ``key=`` only validates TELEGRAM_CRON_SECRET (never ADMIN).
    - Header X-Admin-Key validates ADMIN_API_KEY (or cron secret for cron clients).
    - Production / bot wired / secrets configured → fail closed without valid secret.
    - Local, nothing configured → allow (dev convenience).
    """
    has_any_secret = bool(
        (ADMIN_API_KEY or "").strip() or (TELEGRAM_CRON_SECRET or "").strip()
    )

    if admin_header_ok(x_admin_key):
        return True
    # Admin header slot may carry cron secret for clients that only set one header
    if cron_secret_ok(x_admin_key):
        return True
    if allow_query_cron and cron_secret_ok(key):
        return True

    if IS_PRODUCTION or bot_wired or has_any_secret:
        return False
    return True
