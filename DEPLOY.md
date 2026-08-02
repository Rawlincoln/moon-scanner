# Deploy Moon Scanner to Render

## 1. Push to GitHub

```bash
cd moon-scanner
git add .
git commit -m "Moon Scanner deploy"
git push origin main
```

Remote: `https://github.com/Rawlincoln/moon-scanner.git`

## 2. Deploy on Render

1. Run `.\deploy.ps1` (uses **Rawlincoln**)
2. Or open: **https://dashboard.render.com/blueprints/new?repo=https://github.com/Rawlincoln/moon-scanner**
3. Apply the blueprint — Render reads `render.yaml`

Live URL example: **https://moon-scanner-9tlz.onrender.com**

## 3. Plan

| Plan | Cost | Behavior |
|------|------|----------|
| **Free** (current `render.yaml`) | $0 | Spins down after idle; cold starts; 30s request limits |
| **Starter** | ~$7/mo | Always on, longer request timeout |

To upgrade: set `plan: starter` in `render.yaml`.

SQLite under `data/` is **ephemeral** on free tier (lost on redeploy). Use a persistent disk if you need learning history across deploys.

## 4. Required / recommended env vars (Render dashboard)

| Variable | Required | Purpose |
|----------|----------|---------|
| `MOON_SCANNER_DEPLOY` | set by yaml | `render` |
| `HELIUS_API_KEY` | **strongly recommended** | Paid Solana RPC/WSS — stops public 429s |
| `ADMIN_API_KEY` | **recommended** | Protects `POST /api/learning/reseed` and `/rebuild` |
| `CORS_ORIGINS` | optional | Comma-separated; defaults to known Render URLs in prod |
| `SOLANA_RPC_HTTP` / `SOLANA_RPC_WSS` | optional | Override Helius auto-wire |
| `RATE_LIMIT_PER_MIN` | optional | Default 30 scan calls / IP / min |
| `RATE_LIMIT_ANALYZE_PER_MIN` | optional | Default 12 deep analyze / IP / min |
| `RATE_LIMIT_FORCE_COST` | optional | `force=true` costs N tokens (default 4) |
| `TRUST_X_FORWARDED_FOR` | optional | Default on in production; rightmost XFF hop |
| `ANALYZE_CONCURRENCY` | optional | Global concurrent deep analyzes (default 4) |
| `TELEGRAM_BOT_TOKEN` | for 24/7 alerts | BotFather token |
| `TELEGRAM_CHAT_ID` | for 24/7 alerts | Your Telegram user/group id |
| `TELEGRAM_ALERTS` | optional | `1` to force on |
| `TELEGRAM_CRON_SECRET` | free-tier 24/7 | Secret for GET `/api/alerts/telegram/tick?key=` |

**Never** commit real keys. API responses redact RPC hosts (no `api-key=` leakage).

### 24/7 Telegram (PC off)

See **[TELEGRAM_ALERTS.md](./TELEGRAM_ALERTS.md)** — use **Render Starter** (always on) or **Free + cron** wake URL.

## 5. Free RPC (no paid Helius)

You do **not** need a paid plan. Options:

| Mode | How |
|------|-----|
| Zero signup | `start-free.bat` (public RPC, WS off) |
| Free key | Helius free tier **or** Alchemy free Solana → see **FREE_RPC.md** |
| Permanent free | `DISABLE_SOLANA_WS=1` in `.env` |

Full guide: [FREE_RPC.md](./FREE_RPC.md)

## 6. Local vs Render

| | Local | Render |
|---|-------|--------|
| URL | http://127.0.0.1:8765 | your onrender.com URL |
| Start | `start.bat` or `start-free.bat` | Automatic |
| Config | `.env` (from `.env.example`) | Dashboard env vars |
| Background trenches warm | Off by default | Controlled by config |
| Learning poll cap | 40 (public) / 80 (paid RPC) | same |

## 7. Health

- `GET /api/health` — includes `rpc.paid`, `rpc.provider` (no secrets)
- Moons: `/` · Safe Snipes: `/snipes`

## 8. Admin routes

Header only (never put the key in the query string):

```http
POST /api/learning/reseed?force=true
POST /api/learning/rebuild
X-Admin-Key: <ADMIN_API_KEY>
```

If `ADMIN_API_KEY` is unset on production, those routes return **403**.  
`/docs` is disabled when `MOON_SCANNER_DEPLOY=render|production`.
