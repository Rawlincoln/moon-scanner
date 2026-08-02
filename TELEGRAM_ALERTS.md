# Telegram alerts — don’t miss Moons / Snipes / Heat

Browser desktop alerts only work when a tab is open. **Telegram alerts run on the server** every ~45s while `start.bat` is running.

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
