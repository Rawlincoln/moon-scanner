"""GitHub Actions / free cron worker: wake Render, scan feeds, Telegram new picks.

Env:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (required)
  BASE_URL (default https://moon-scanner-9tlz.onrender.com)
  TELEGRAM_CRON_SECRET (optional — if set, prefer /api/alerts/telegram/tick)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = (os.getenv("BASE_URL") or "https://moon-scanner-9tlz.onrender.com").rstrip("/")
TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
CRON = (os.getenv("TELEGRAM_CRON_SECRET") or "").strip()
SEEN_PATH = os.getenv("SEEN_PATH") or "data/gha_telegram_seen.json"
DEDUPE_SEC = int(os.getenv("TELEGRAM_ALERT_DEDUPE_SEC") or str(45 * 60))


def http_json(url: str, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "moon-scanner-gha/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "moon-scanner-gha/1.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_seen() -> dict[str, float]:
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        now = time.time()
        return {k: float(v) for k, v in raw.items() if now - float(v) < DEDUPE_SEC * 2}
    except Exception:
        return {}


def save_seen(seen: dict[str, float]) -> None:
    os.makedirs(os.path.dirname(SEEN_PATH) or ".", exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f)


def send_tg(text: str) -> bool:
    if not TOKEN or not CHAT:
        print("missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        http_post_json(
            url,
            {
                "chat_id": CHAT,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )
        return True
    except Exception as exc:
        print(f"telegram send failed: {exc}", file=sys.stderr)
        return False


def main() -> int:
    # Prefer server-side tick (dedupe + enrich quality) when cron secret set
    if CRON:
        url = f"{BASE}/api/alerts/telegram/tick?key={urllib.parse.quote(CRON)}"
        try:
            data = http_json(url, timeout=180.0)
            print("tick", json.dumps(data)[:500])
            return 0 if data.get("ok") is not False else 1
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:300]
            print(f"tick HTTP {exc.code}: {body}", file=sys.stderr)
            # fall through to client-side if secret not yet on Render
        except Exception as exc:
            print(f"tick failed: {exc}", file=sys.stderr)

    if not TOKEN or not CHAT:
        print("No telegram credentials", file=sys.stderr)
        return 2

    seen = load_seen()
    now = time.time()
    sent = 0
    feeds = [
        ("moon", f"{BASE}/api/moon?limit=12&force=true", "MOON", "WATCH"),
        ("snipe", f"{BASE}/api/snipes?limit=10&force=true", "SNIPE", "SETUP"),
        ("heat", f"{BASE}/api/heat?limit=12&force=true", "HEAT", "WARM"),
        (
            "grad",
            f"{BASE}/api/graduated?limit=12&force=true&max_age_minutes=10080",
            "RUNNER",
            "DIP",
        ),
    ]
    for kind, url, *labels in feeds:
        try:
            data = http_json(url, timeout=180.0)
        except Exception as exc:
            print(f"{kind} scan failed: {exc}", file=sys.stderr)
            continue
        tokens = data.get("tokens") or []
        allow = set(labels)
        for t in tokens:
            if not isinstance(t, dict):
                continue
            mint = (t.get("tokenAddress") or t.get("mint") or "").strip()
            if not mint:
                continue
            lab = (
                t.get("moon_label")
                or t.get("snipe_label")
                or t.get("heat_label")
                or ""
            ).upper()
            if lab not in allow:
                continue
            key = f"{kind}:{mint}"
            if key in seen and now - seen[key] < DEDUPE_SEC:
                continue
            sym = t.get("symbol") or "?"
            mcap = t.get("mcap_usd") or 0
            padre = f"https://trade.padre.gg/trade/solana/{mint}"
            msg = (
                f"{'🌕' if kind=='moon' else '⚡' if kind=='snipe' else '🔥'} "
                f"<b>{lab}</b> ${sym}\n"
                f"{kind.upper()} · ${mcap:,.0f}\n"
                f'<a href="{padre}">Padre</a>\n'
                f"<code>{mint}</code>"
            )
            if send_tg(msg):
                seen[key] = now
                sent += 1
                time.sleep(0.4)
    save_seen(seen)
    print(f"sent={sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
