# Telegram alerts — money mode (default)

Browser desktop alerts only work when a tab is open. **Telegram alerts run on the server** every ~45s while the process is up.

## Money mode (default — stop the bleed)

`TELEGRAM_MONEY_MODE=1` (default on Render):

| Setting | Value |
|---------|--------|
| Feeds | **moon, snipe only** (no heat/grad spam) |
| Labels | **MOON** and **SNIPE** only (no WATCH/SETUP/WARM/DIP) |
| Each alert | Entry · STOP · TP1 · TP2 · INVALID rules |
| Auto-CANCEL | If mcap −15% from alert, hits stop, or no +8% in 45m |
| Journal | `/api/journal` — open trades + win rate / expectancy R |

Set `TELEGRAM_MONEY_MODE=0` to restore multi-feed heat/grad alerts.

## Complete money system (v2)

| Piece | Behavior |
|-------|----------|
| **Risk size** | `BANKROLL_USD` × `RISK_PER_TRADE_PCT` / stop distance → USD (and SOL) size on every alert |
| **Session gates** | Max open, max trades/day, daily −R stop, profit lock |
| **Position manager** | TP1 (scale 50% + BE), TP2 close, STOP, INVALID via Telegram |
| **Money desk** | `/money` UI + `GET /api/money` |
| **Journal** | Paper P&amp;L + E[R] until you go live |

| Env | Default | Meaning |
|-----|---------|---------|
| `BANKROLL_USD` | 500 | Your paper/live equity |
| `RISK_PER_TRADE_PCT` | 1.0 | Risk 1% of bankroll to stop |
| `MAX_OPEN_TRADES` | 2 | Concurrent positions |
| `MAX_TRADES_PER_DAY` | 6 | Session cap |
| `MAX_DAILY_LOSS_R` | 3.0 | Halt after −3R day |
| `MONEY_SYSTEM_ARMED` | 1 | 0 = scan only, no new alerts |

**Trade journal / desk**

| Endpoint | Purpose |
|----------|---------|
| `GET /money` | Money desk UI |
| `GET /api/money` | Full desk snapshot |
| `GET /api/money/size` | Size calculator |
| `GET /api/journal` | EV summary |
| `GET /api/journal/trades` | List open/closed |
| `POST /api/journal/trades/{id}/close` | Close with `{"exit_mcap": N}` (+ `X-Admin-Key`) |
| `POST /api/money/manage` | Force position manager cycle |

Paper by default (`MONEY_PAPER_DEFAULT=1`). Only go live when E[R] &gt; 0 over ≥20 closed trades.

## 24/7 when your PC is OFF

Your laptop must **not** be the only place the scanner runs. Alerts need a **cloud process**.

| Option | Cost | 24/7 reliability |
|--------|------|------------------|
| **A) Render Starter** (recommended) | ~$7/mo | Always on — background loop sends Telegram |
| **B) Render Free + external cron** | $0 | Free tier **sleeps** when idle; cron wakes it every 2–3 min |
| Local only (`start.bat`) | $0 | **Stops when PC is off** |

### Option A — Always-on Render (best)

1. Deploy moon-scanner on [Render](https://dashboard.render.com) (see `DEPLOY.md`).
2. Upgrade service plan to **Starter** (or set `plan: starter` in `render.yaml` and redeploy).
3. In Render → **Environment**, set:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_ALERTS=1
TELEGRAM_ALERT_FEEDS=moon,snipe,heat
TELEGRAM_ALERT_INTERVAL_SEC=45
ADMIN_API_KEY=some-long-random-string
```

4. Redeploy. Open your onrender URL `/api/alerts/status` → `"configured": true`.
5. You should get “alerts ON” in Telegram. PC can be off.

### Option B — Free Render + cron (PC still off)

Free web services **spin down** after ~15 min idle, so the in-app 45s loop dies. Fix: an external cron hits a wake endpoint.

1. Keep Render on **free** plan.
2. Set env vars as above, plus:

```text
TELEGRAM_CRON_SECRET=another-long-random-string
```

3. Create a free cron job at [cron-job.org](https://cron-job.org) (or similar):
   - **URL:** `https://YOUR-SERVICE.onrender.com/api/alerts/telegram/tick?key=YOUR_TELEGRAM_CRON_SECRET`
   - **Schedule:** every **2–3 minutes**
   - Method: GET

**Auth rules (P0):**
- Query `?key=` accepts **only** `TELEGRAM_CRON_SECRET` (never `ADMIN_API_KEY`).
- Prefer header `X-Cron-Secret` or `X-Admin-Key` when the cron tool supports headers.
- Production / bot wired: force endpoints **fail closed** without a valid secret.

Each tick wakes the app, scans moons/snipes/heat/grad, and Telegrams new picks. Expect cold starts (~30–60s) after sleep — not as fast as Starter, but works with the PC off.

**Never** put tokens in GitHub. Only Render dashboard / cron URL (cron URL should not be public).

---

## Setup (5 minutes)

### 1. Create a bot

1. Open Telegram and search **@BotFather**
2. Send `/newbot` and follow prompts
3. Copy the **token** (looks like `7123456789:AAH...`)

### 2. Get your chat id

1. Open **your new bot** in Telegram and press **Start** (or send `hi`)
2. In a browser open:

```text
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

3. Find:

```json
"chat": { "id": 123456789 }
```

That number is `TELEGRAM_CHAT_ID`.

**Group chat:** add the bot to a group, send a message mentioning it, then use `getUpdates` again. Group ids are often negative (e.g. `-100123...`).

### 3. Add to `.env`

In `moon-scanner/.env`:

```env
TELEGRAM_BOT_TOKEN=7123456789:AAH...
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ALERTS=1
TELEGRAM_ALERT_FEEDS=moon,snipe,heat
TELEGRAM_ALERT_INTERVAL_SEC=45
```

### 4. Restart

Double-click **`start.bat`** and keep the window open.

You should get:

```text
✅ Moon Scanner Telegram alerts ON
```

### 5. Test

```powershell
# Status (no secrets)
Invoke-RestMethod http://127.0.0.1:8765/api/alerts/status

# Test message (if ADMIN_API_KEY set, pass header)
Invoke-RestMethod -Method POST http://127.0.0.1:8765/api/alerts/telegram/test
```

Or open: http://127.0.0.1:8765/api/alerts/status

---

## What gets alerted

| Feed | Default labels | Message includes |
|------|----------------|------------------|
| **Moons** | MOON, WATCH | mcap, age, why, Padre + Pump links |
| **Safe Snipes** | SNIPE, SETUP | entry mcap, 2× TP, links |
| **Organic Heat** | HEAT, WARM | heat why + dev launched/migrated |

Same mint won’t re-alert for **45 minutes** (dedupe).

---

## Options

```env
# Only high-conviction moons
TELEGRAM_ALERT_MOON_LABELS=MOON

# Include RISKY heat (noisier)
TELEGRAM_ALERT_HEAT_LABELS=HEAT,WARM,RISKY

# Faster / slower scans
TELEGRAM_ALERT_INTERVAL_SEC=30

# Only moons + snipes
TELEGRAM_ALERT_FEEDS=moon,snipe

# Off
TELEGRAM_ALERTS=0
```

---

## Tips

- Keep **`start.bat` running** — if the process dies, alerts stop.
- Free public RPC is fine for alerts; a freemium Helius/Alchemy key is still better for speed.
- Desktop 🔔 alerts still work as a second channel when a tab is open.
- Health JSON includes `telegram_alerts: { configured, last_sent, ... }`.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No “alerts ON” message | Check token/chat id; restart server |
| `chat not found` | Message the bot first, then re-check chat id |
| Too many messages | Raise `TELEGRAM_ALERT_DEDUPE_SEC` or narrow labels |
| Empty feeds | Normal — capital filters. Heat is noisier for more pings |
| PC off, no alerts | App only runs locally — deploy to Render (see **24/7** above) |
| Free Render, alerts stop | Service slept — use cron tick or upgrade to Starter |
| Cron 401 | Wrong `key=` — must match `TELEGRAM_CRON_SECRET` or `ADMIN_API_KEY` |
