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
| `RATE_LIMIT_PER_MIN` | optional | Default 45 expensive API calls / IP / min |

**Never** commit real keys. API responses redact RPC hosts (no `api-key=` leakage).

## 5. Local vs Render

| | Local | Render |
|---|-------|--------|
| URL | http://127.0.0.1:8765 | your onrender.com URL |
| Start | `start.bat` | Automatic |
| Config | `.env` (from `.env.example`) | Dashboard env vars |
| Background trenches warm | Off by default | Controlled by config |
| Learning poll cap | 40 (public) / 80 (paid RPC) | same |

## 6. Health

- `GET /api/health` — includes `rpc.paid`, `rpc.provider` (no secrets)
- Moons: `/` · Safe Snipes: `/snipes`

## 7. Admin routes

```http
POST /api/learning/reseed?force=true
POST /api/learning/rebuild
X-Admin-Key: <ADMIN_API_KEY>
```

If `ADMIN_API_KEY` is unset on production, those routes return **403**.
