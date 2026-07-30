# Free Solana RPC options (no paid Helius required)

Moon Scanner’s **Moons** and **Safe Snipes** feeds mainly use:

- pump.fun  
- DexScreener  
- RugCheck  

A Solana RPC key improves **realtime** and reduces **429** errors. You can still run **for free**.

---

## Quick start: zero signup

Double-click:

```text
start-free.bat
```

Or from PowerShell:

```powershell
cd C:\Users\MMghongo\moon-scanner
.\start-free.bat
```

Then open: **http://127.0.0.1:8765**

| Setting | Free mode |
|---------|-----------|
| Solana WS | Off (avoids public WS 429s) |
| Pump poll | Every 4s |
| Learning poll cap | 25 tokens |
| RPC | Public mainnet |

Check: http://127.0.0.1:8765/api/health  
→ `"rpc": { "provider": "public", "paid": false }` is OK in free mode.

---

## Free tier keys (recommended when possible)

### A) Helius free developer key

1. https://dashboard.helius.dev → sign up  
2. Create API key on free plan (if offered)  
3. In `.env`:

```env
HELIUS_API_KEY=your_key_here
```

4. Run normal `start.bat` (not free mode)

### B) Alchemy free Solana app

1. https://www.alchemy.com/ → create app → **Solana Mainnet**  
2. Copy HTTPS + WSS URLs  
3. In `.env`:

```env
HELIUS_API_KEY=
SOLANA_RPC_HTTP=https://solana-mainnet.g.alchemy.com/v2/YOUR_KEY
SOLANA_RPC_WSS=wss://solana-mainnet.g.alchemy.com/v2/YOUR_KEY
```

4. `start.bat`

### C) Other freemium hosts

Same pattern as Alchemy — set both:

```env
SOLANA_RPC_HTTP=https://...
SOLANA_RPC_WSS=wss://...
```

Examples to try: Ankr, Shyft, Chainstack free tier, QuickNode trial.

---

## Free mode permanently in `.env`

If you always want free/public mode without `start-free.bat`:

```env
HELIUS_API_KEY=
DISABLE_SOLANA_WS=1
REALTIME_PUMP_POLL_SEC=4
LEARNING_ACTIVE_CAP_PUBLIC=25
SOLANA_WS_MODE=logs
```

Then `start.bat` is fine.

---

## What free mode does / doesn’t do

| Works without paid RPC | Limited without better RPC |
|------------------------|----------------------------|
| Moon list | Live mint stream quality |
| Safe Snipes 2× | Solana `getTransaction` resolution |
| Analyze (RugCheck/Dex) | Fewer 429s under load |
| Local UI | Render free + public RPC is rough |

---

## Render (cloud) free setup

In Render → Environment (no paid key):

| Key | Value |
|-----|--------|
| `DISABLE_SOLANA_WS` | `1` |
| `REALTIME_PUMP_POLL_SEC` | `4` |
| `LEARNING_ACTIVE_CAP_PUBLIC` | `25` |

Optional later: add free Alchemy/Helius key as `SOLANA_RPC_*` or `HELIUS_API_KEY`.
