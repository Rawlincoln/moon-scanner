#!/usr/bin/env python3
"""Sync moon outcomes across free-tier redeploys via GHA cache.

Flow:
  1. Load local cache JSON (previous export)
  2. GET /api/moon/outcomes/export from Render (if cron secret set)
  3. Merge rows (mint+shown_at+cohort)
  4. POST merge back if server is missing rows we have
  5. Save merged cache

Env:
  BASE_URL              default https://moon-scanner-9tlz.onrender.com
  TELEGRAM_CRON_SECRET  required for export/import
  OUTCOMES_CACHE        default data/gha_moon_outcomes.json
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (os.getenv("BASE_URL") or "https://moon-scanner-9tlz.onrender.com").rstrip("/")
CRON = (os.getenv("TELEGRAM_CRON_SECRET") or "").strip()
CACHE = os.getenv("OUTCOMES_CACHE") or "data/gha_moon_outcomes.json"


def _req(method: str, url: str, body: dict | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "moon-outcomes-sync/1.0"}
    if CRON:
        headers["X-Cron-Secret"] = CRON
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _row_key(r: dict) -> str:
    return f"{r.get('mint')}|{float(r.get('shown_at') or 0):.3f}|{r.get('cohort') or 'shown'}"


def _load_cache() -> list[dict]:
    try:
        if not os.path.isfile(CACHE):
            return []
        raw = json.loads(open(CACHE, encoding="utf-8").read())
        rows = raw.get("rows") if isinstance(raw, dict) else raw
        return [r for r in (rows or []) if isinstance(r, dict)]
    except Exception as exc:
        print(f"cache load skip: {exc}", file=sys.stderr)
        return []


def _save_cache(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(CACHE) or ".", exist_ok=True)
    # Keep newest 8k
    rows = sorted(rows, key=lambda r: float(r.get("shown_at") or 0), reverse=True)[:8000]
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({"n": len(rows), "rows": rows}, f)


def main() -> int:
    if not CRON:
        print("TELEGRAM_CRON_SECRET missing — skip outcomes sync", file=sys.stderr)
        return 0

    local = _load_cache()
    by_key = {_row_key(r): r for r in local}

    try:
        exp = _req(
            "GET",
            f"{BASE}/api/moon/outcomes/export?limit=8000&key={urllib.parse.quote(CRON)}",
        )
        remote = exp.get("rows") or []
        print(f"export ok n={len(remote)} db={exp.get('db_path')}")
    except urllib.error.HTTPError as e:
        print(f"export HTTP {e.code}: {e.read()[:200]}", file=sys.stderr)
        remote = []
    except Exception as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        remote = []

    for r in remote:
        if isinstance(r, dict) and r.get("mint"):
            by_key[_row_key(r)] = r

    merged = list(by_key.values())
    _save_cache(merged)
    print(f"cache saved n={len(merged)} path={CACHE}")

    # Push back rows server may have lost (redeploy wipe)
    remote_keys = {
        _row_key(r) for r in remote if isinstance(r, dict) and r.get("mint")
    }
    missing = [r for k, r in by_key.items() if k not in remote_keys]
    # Prefer finalized / shown recs for restore value
    missing.sort(
        key=lambda r: (
            0 if r.get("outcome") else 1,
            0 if (r.get("entry_label") or "") in ("MOON", "WATCH") else 1,
            -float(r.get("shown_at") or 0),
        )
    )
    to_push = missing[:2000]
    if not to_push:
        print("server already has all cached rows")
        return 0

    try:
        imp = _req("POST", f"{BASE}/api/moon/outcomes/import", {"rows": to_push})
        print(
            f"import ok inserted={imp.get('inserted')} skipped={imp.get('skipped')} "
            f"pushed={len(to_push)}"
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
