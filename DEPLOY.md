# Deploy Moon Scanner to Render

## 1. Push to GitHub

```bash
cd moon-scanner
git init
git add .
git commit -m "Moon Scanner — Render deploy"
```

Create a new repo on GitHub (e.g. `moon-scanner`), then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/moon-scanner.git
git branch -M main
git push -u origin main
```

## 2. Deploy on Render

1. Go to [render.com](https://render.com) and sign in
2. **New** → **Blueprint**
3. Connect your GitHub account and select the `moon-scanner` repo
4. Render reads `render.yaml` automatically — click **Apply**

Your site will be live at: `https://moon-scanner.onrender.com` (or the name you choose)

## 3. Plan (always online)

| Plan | Cost | Behavior |
|------|------|----------|
| **Starter** (recommended) | ~$7/mo | Always on, 100 min request timeout |
| Free | $0 | Spins down after 15 min idle; 30s request limit |

`render.yaml` uses **Starter** so scans and RugCheck stay reliable.

To use Free tier, edit `render.yaml` and change `plan: starter` → `plan: free`.

## 4. How it works on Render

- Background job refreshes trenches cache every 2 minutes
- Web requests usually return cached data in &lt;1s (avoids timeouts)
- Health check: `/api/health`

## 5. Local vs Render

| | Local | Render |
|---|-------|--------|
| URL | http://127.0.0.1:8765 | https://your-app.onrender.com |
| Start | `start.bat` | Automatic on deploy |
| Background scan | Off | On (cache warmer) |