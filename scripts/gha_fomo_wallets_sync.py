#!/usr/bin/env python3
"""Sync FOMO watchlist across free-tier redeploys via GHA cache.

Flow:
  1. Load local cache (previous authoritative list)
  2. GET /api/fomo/wallets/export from Render
  3. Prefer the newest user_touched snapshot
  4. If server lost customizations (re-seeded), POST import cache
  5. Save winning snapshot to cache

Env:
  BASE_URL              default https://moon-scanner-9tlz.onrender.com
  TELEGRAM_CRON_SECRET  preferred for import (or FOMO_OPEN_MANAGE=1 on server)
  FOMO_WALLETS_CACHE    default data/gha_fomo_wallets.json
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = (os.getenv("BASE_URL") or "https://moon-scanner-9tlz.onrender.com").rstrip("/")
CRON = (os.getenv("TELEGRAM_CRON_SECRET") or "").strip()
CACHE = os.getenv("FOMO_WALLETS_CACHE") or "data/gha_fomo_wallets.json"


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "fomo-wallets-sync/1.0"}
    if CRON:
        headers["X-Cron-Secret"] = CRON
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_cache() -> dict:
    try:
        if not os.path.isfile(CACHE):
            return {}
        raw = json.loads(open(CACHE, encoding="utf-8").read())
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        print(f"cache load skip: {exc}", file=sys.stderr)
        return {}


def _save_cache(payload: dict) -> None:
    os.makedirs(os.path.dirname(CACHE) or ".", exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _addrs(wallets: list) -> set[str]:
    return {
        str(w.get("address") or "").strip()
        for w in (wallets or [])
        if isinstance(w, dict) and w.get("address")
    }


def _is_seed_only(payload: dict) -> bool:
    """True if list looks like a fresh elite seed (no user edits)."""
    if payload.get("user_touched"):
        return False
    wallets = payload.get("wallets") or []
    if not wallets:
        return True
    sources = {str(w.get("source") or "") for w in wallets if isinstance(w, dict)}
    return sources <= {"elite_seed", ""}


def main() -> int:
    local = _load_cache()
    try:
        remote = _req("GET", f"{BASE}/api/fomo/wallets/export")
        print(
            f"export ok n={remote.get('count')} user_touched={remote.get('user_touched')} "
            f"updated={remote.get('updated')}"
        )
    except urllib.error.HTTPError as e:
        print(f"export HTTP {e.code}: {e.read()[:200]}", file=sys.stderr)
        remote = {}
    except Exception as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        remote = {}

    local_ut = bool(local.get("user_touched"))
    remote_ut = bool(remote.get("user_touched"))
    local_upd = float(local.get("updated") or 0)
    remote_upd = float(remote.get("updated") or 0)
    local_n = len(local.get("wallets") or [])
    remote_n = len(remote.get("wallets") or [])

    # Choose authoritative snapshot
    winner: dict
    if remote_ut and (not local_ut or remote_upd >= local_upd):
        winner = remote
        reason = "remote_user"
    elif local_ut and (not remote_ut or local_upd > remote_upd):
        winner = local
        reason = "local_user"
    elif remote and remote_n:
        winner = remote
        reason = "remote_default"
    elif local and local_n:
        winner = local
        reason = "local_default"
    else:
        print("nothing to sync")
        return 0

    snap = {
        "version": 1,
        "updated": float(winner.get("updated") or 0),
        "user_touched": bool(winner.get("user_touched")),
        "count": len(winner.get("wallets") or []),
        "wallets": winner.get("wallets") or [],
    }
    _save_cache(snap)
    print(
        f"cache saved n={snap['count']} user_touched={snap['user_touched']} "
        f"reason={reason} path={CACHE}"
    )

    # Restore to server if it lost a customized list (redeploy re-seed)
    need_restore = False
    if snap["user_touched"] and _is_seed_only(remote or {}):
        need_restore = True
    elif snap["user_touched"] and _addrs(snap["wallets"]) != _addrs(remote.get("wallets") or []):
        # Only push if cache is newer than remote
        if snap["updated"] >= remote_upd:
            need_restore = True
    elif snap["user_touched"] and not remote_n and snap["count"] >= 0:
        need_restore = True

    if not need_restore:
        print("server already has authoritative watchlist")
        return 0

    body = {
        "wallets": snap["wallets"],
        "user_touched": True,
        "updated": snap["updated"] or None,
    }
    try:
        imp = _req("POST", f"{BASE}/api/fomo/wallets/import", body)
        print(
            f"import ok count={imp.get('count')} user_touched={imp.get('user_touched')} "
            f"msg={imp.get('message')}"
        )
    except urllib.error.HTTPError as e:
        print(f"import HTTP {e.code}: {e.read()[:300]}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
