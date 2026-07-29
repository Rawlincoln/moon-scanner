# Moon Scanner — Concrete Refactor Plan

Goal: one product path (moon capital-protection scanner), smaller surface, testable modules, measurable outcomes.

## Phase 0 — Done in parallel

- [x] Outcome tracking (`services/moon_outcomes.py` + `/api/moon/outcomes`)
- [x] Efficiency pass (single discovery, shared httpx, Dex-before-RugCheck, start.bat single instance)

---

## Phase 1 — Split `main.py` (no behavior change) ✅

**Done:**
- [x] `services/scan_moon.py` — full moon pipeline
- [x] `routes/moon.py` — `/api/moon`, `/api/moon/outcomes`
- [x] `routes/realtime.py` — `/api/realtime/*`
- [x] `services/analyze_token.py` — single-token analyze + pair resolve
- [x] `routes/analyze.py` — `/api/analyze*`, `/api/checkers/*`
- [x] `services/scan_trenches.py` — trenches scan, sticky near-mig, runner alerts
- [x] `routes/trenches.py` — `/api/padre/*`, `/api/runner-radar`
- [x] `app/create_app()` package — deps, lifespan, state
- [x] `routes/health.py`, `learning.py`, `legacy_scan.py`
- [x] `services/legacy_scan.py` — deprecated scan/invest logic
- [x] thin `main.py` — `app = create_app()` (still `main:app` for deploy)

```
moon-scanner/
  app/
    __init__.py          # create_app()
    deps.py              # clients + learning
    state.py             # scan cache
    lifespan.py          # background loops
    paths.py             # BASE_DIR
    deprecated.py        # legacy headers
  routes/                # all HTTP routers
  services/              # domain logic
  main.py                # app = create_app()  →  uvicorn main:app
```

**Rule:** no logic changes in Phase 1 — move only, then smoke-test `/api/moon` + `/api/health`.

---

## Phase 2 — Delete / archive dead product surface ✅

### Archived (not served)

| Path | Where |
|------|--------|
| `static/js/app.js` | `_archive/legacy-ui/app.js` |
| `static/css/style.css` | `_archive/legacy-ui/style.css` |

Live static tree is **moon-only**: `index.html` + `moon.js` + `moon.css`.

### Deprecated APIs (still work; Sunset Nov 2026)

| Endpoint | Status |
|----------|--------|
| `/api/scan` | `deprecated=True` + Deprecation headers → use `/api/moon` |
| `/api/invest` | same |
| `/api/padre/trenches` | same |
| `/api/padre/trenches/feed` | same |
| `/api/runner-radar` | same |

Headers: `Deprecation: true`, `Link: </api/moon>; rel="successor-version"`.

### Keep

| Path | Why |
|------|-----|
| `services/moon_picks.py`, `bundle_sniper.py`, `social_signals.py` | Core product |
| `services/moon_outcomes.py` | Feedback loop |
| `services/realtime_*`, `yellowstone_feed.py`, `solana_ws_feed.py` | Detection |
| `static/index.html`, `moon.js`, `moon.css` | Only UI |
| `start.bat`, `config.py`, deploy files | Ops |

---

## Phase 3 — Outcome loop → gates (data-driven) ✅

1. [x] Log every shown MOON/WATCH
2. [x] Poll mcap at 15m / 1h / 6h (background)
3. [x] Segment dump rates: `by_label`, `by_influencer`, `by_bundled_band`
4. [x] Adaptive gates via `suggested_gates()` → `filter_and_rank`
   - dump ≥50/60/75% → raise score/conf
   - toxic WATCH / non-influencer / bundled bands → tighten
   - floors 52/50, ceilings 72/70; max_bundled 5–12%
5. [x] UI shows segment stats + applied gates

API: `GET /api/moon/outcomes` (full analytics + `gates`)

---

## Phase 4 — Realtime (paid WSS) ✅

1. [x] Paid WSS path: `HELIUS_API_KEY` or `SOLANA_RPC_WSS` / `SOLANA_RPC_HTTP`
2. [x] `transactionSubscribe` on paid providers (auto mode) → mint from full tx
3. [x] `logsSubscribe` fallback (public / when tx sub fails)
4. [x] Shared helpers + tests: `services/realtime_rpc.py`
5. [x] `.env.example` setup notes; status API surfaces mode/paid
6. [x] Moon UI never blocks on feed — bus only prioritizes mints
7. [ ] Yellowstone **full** Geyser decode (still channel probe; needs provider protos/SDK)

```bat
set HELIUS_API_KEY=your_key
# or set SOLANA_RPC_WSS=… and SOLANA_RPC_HTTP=…
```

---

## Phase 5 — Tests (minimum) ✅

| Test file | Coverage |
|-----------|----------|
| `tests/test_moon_picks.py` | reject dump, name-jack, influencer pass, bundled cap |
| `tests/test_bundle_sniper.py` | 5% / 12% / 25% bands, hard_reject, sniper bag |
| `tests/test_social_signals.py` | Elon/CZ/Ansem status URLs, junk ticker |
| `tests/test_moon_outcomes.py` | classify win/dump, adaptive gates |
| `tests/test_scan_moon_smoke.py` | card builder + priority |

```bat
py -3 -m pip install pytest -q
py -3 -m pytest tests -q
```

---

## Order of work (recommended)

1. Phase 1 extract `services/scan_moon.py` + `routes/moon.py` (1 PR)
2. Phase 2 delete legacy static JS/CSS (1 PR)
3. Phase 3 use outcomes summary in UI footer (1 PR)
4. Phase 5 tests before more threshold tuning
5. Phase 4 only if you buy a stream

## Success metrics

| Metric | Target |
|--------|--------|
| `/api/moon` empty-market latency | &lt; 1s |
| Finalized dump_rate | Track; aim to decrease over time |
| `main.py` lines | ~20 (`create_app` factory) |
| Single UI | Only moon.js served |
