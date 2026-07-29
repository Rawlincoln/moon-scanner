const CHAINS = ["solana", "bsc", "base", "ethereum", "arbitrum", "polygon"];
let selectedChains = new Set(["solana"]);
let lastTokens = [];
let refreshTimer = null;
let scanInFlight = false;
const SCAN_TIMEOUT_MS = 120000;

/** Production cloud API — single source of truth for token lists */
const CLOUD_API = "https://moon-scanner-9tlz.onrender.com";
const IS_CLOUD_HOST = /onrender\.com$/i.test(location.hostname);

function defaultApiMode() {
  // Localhost → local server (cloud free tier sleeps / times out)
  if (IS_CLOUD_HOST) return "cloud";
  if (/^(localhost|127\.0\.0\.1)$/i.test(location.hostname || "")) return "local";
  return "cloud";
}

function getApiBase() {
  // On Render always use same origin (already the cloud backend)
  if (IS_CLOUD_HOST) return "";
  const mode = localStorage.getItem("moon_api_mode") || defaultApiMode();
  if (mode === "local") return "";
  return CLOUD_API;
}

function apiUrl(path) {
  const base = getApiBase().replace(/\/$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}

function getApiModeLabel() {
  if (IS_CLOUD_HOST) return "cloud";
  return localStorage.getItem("moon_api_mode") || defaultApiMode();
}

const $ = (sel) => document.querySelector(sel);
const grid = $("#tokenSections") || $("#tokenGrid");
const sectionNav = $("#sectionNav");
const statusBar = $("#statusBar");
const statCount = $("#statCount");

function setGridHtml(html) {
  if (!grid) {
    console.error("tokenSections missing from page");
    return;
  }
  grid.innerHTML = html;
}
let sectionFilter = "all";
let lastScanMeta = {};
let lastRunnerAlerts = [];
let notifiedMints = new Set(JSON.parse(localStorage.getItem("moon_notified_mints") || "[]"));
let runnerPollTimer = null;
/**
 * Server hard-blocklist mirror — UI must never show these.
 * Keep in sync with services/avoid_filters.py BLOCKED_MINTS.
 */
const HARD_BLOCKED_MINTS = new Set([
  "BD42EGwRsQArB2SKwgdqPzjsBbme963ZrR9sioTopump",
  "4GTkEsYhegrJmbAiiUe9TrsQrTrqx7n1jDMSH5GGpump",
  "FAAnKpATxZuWWsCbxWZ5yaNn9CyCj4d9Wnqzhhdqpump",
  "62pzwoXyHi5Z1iEdD67RDPTT12spZ4ph8WsLU5y8pump",
  "5ocgBRqLyQxZEvtAYcX1nXeVhAj1cuCHi2ZfSZKVpump",
  "BTU78ZNs11eDYsaUXysXnEPEJrCDYDobAkTfQQafpump", // USWR first
  "9Sj7Yi6oYCATrjC68or2Rqk3D6YkgKaqc9UepDogpump", // CUBEMAN
  "Bw1gX5ih2DJFtXggXnnGbWqqpBte1uvb9jurUSecpump", // Cashoty
  "P5PhPnXd6AS9JgTiZJzi4Y2CuDYF5nvPNrWpuUFUKgX", // USWR relaunch −93% dump
]);

/** mint → peak mcap seen this browser (survives refresh; used to catch dumps without ATH) */
const PEAK_MCAP_KEY = "moon_peak_mcap_v2";
const PEAK_MCAP_TTL_MS = 6 * 60 * 60 * 1000; // 6h
// Clear legacy sticky that used to re-show dumps with frozen high mcap
try {
  localStorage.removeItem("moon_sticky_near_mig");
} catch {
  /* ignore */
}

function mintOf(t) {
  return String(t?.tokenAddress || t?.mint || t?.address || "").trim();
}

function isHardBlocked(tOrMint) {
  const m = typeof tOrMint === "string" ? tOrMint.trim() : mintOf(tOrMint);
  return Boolean(m && HARD_BLOCKED_MINTS.has(m));
}

function tokenMcap(t) {
  if (!t || typeof t !== "object") return 0;
  return (
    Number(
      t.mcap_usd ||
        t.market_cap_usd ||
        t.market_cap ||
        t.marketCap ||
        t._mcap ||
        t.pumpfun?.usd_market_cap ||
        t.market?.pumpfun?.usd_market_cap ||
        t.market?.marketCap ||
        0
    ) || 0
  );
}

function tokenAth(t) {
  if (!t || typeof t !== "object") return 0;
  return (
    Number(
      t.ath_mcap ||
        t.ath_market_cap ||
        t._ath_mcap ||
        t.pumpfun?.ath_market_cap ||
        t.market?.pumpfun?.ath_market_cap ||
        t.market?.ath_market_cap ||
        0
    ) || 0
  );
}

function loadPeakMap() {
  try {
    const raw = JSON.parse(localStorage.getItem(PEAK_MCAP_KEY) || "{}");
    const now = Date.now();
    const out = {};
    for (const [mint, rec] of Object.entries(raw || {})) {
      if (!mint || isHardBlocked(mint)) continue;
      const peak = Number(rec?.peak || rec) || 0;
      const ts = Number(rec?.ts) || now;
      if (peak <= 0 || now - ts > PEAK_MCAP_TTL_MS) continue;
      out[mint] = { peak, ts };
    }
    return out;
  } catch {
    return {};
  }
}

let peakMcapMap = loadPeakMap();

function savePeakMap() {
  try {
    localStorage.setItem(PEAK_MCAP_KEY, JSON.stringify(peakMcapMap));
  } catch {
    /* quota */
  }
}

/** Raise session peak for mint; attach peak onto token for dump checks. */
function trackTokenPeak(t) {
  if (!t || typeof t !== "object") return t;
  const mint = mintOf(t);
  if (!mint || isHardBlocked(mint)) return t;
  const mcap = tokenMcap(t);
  const ath = tokenAth(t);
  const prev = peakMcapMap[mint]?.peak || 0;
  const peak = Math.max(prev, ath, Number(t._peak_mcap || t.peak_mcap || 0), mcap);
  if (peak > 0) {
    peakMcapMap[mint] = { peak, ts: Date.now() };
    t._peak_mcap = peak;
    if (ath > 0 || peak > mcap) t.ath_mcap = Math.max(ath, peak);
  }
  return t;
}

function isClientCrashedRunner(t) {
  if (!t || typeof t !== "object") return false;
  if (isHardBlocked(t)) return true;
  if (t.skipped || t.skipReason || t.skip_reason) {
    const why = String(t.skipReason || t.skip_reason || "").toLowerCase();
    if (/dump|crash|block|rug|ath/.test(why)) return true;
  }
  const mint = mintOf(t);
  const mcap = tokenMcap(t);
  const ath = tokenAth(t);
  const storedPeak = mint ? Number(peakMcapMap[mint]?.peak || 0) : 0;
  // Peak = ATH + session-tracked high (never rely on current alone)
  const peak = Math.max(ath, storedPeak, Number(t._peak_mcap || t.peak_mcap || 0));
  const rr = t.runnerRadar || {};
  if (rr.crashed || rr.stage === "crashed") return true;
  if (t.crashed_runner || t.skip_reason === "dumped" || t.avoid_reason === "crashed_runner") return true;
  const avoid = t.safetyReport?.avoid || t.avoid || {};
  const flags = new Set(avoid.flags || []);
  if (
    flags.has("blocklist") ||
    flags.has("flash_pump_dump") ||
    flags.has("post_ath_crash") ||
    flags.has("drained_curve") ||
    flags.has("creator_dumped") ||
    flags.has("sell_pressure")
  ) {
    return true;
  }
  if (avoid.avoid && /blocklist|dump|crash|scam|rug|relaunch|ath/i.test(String(avoid.summary || avoid.reason || ""))) {
    return true;
  }
  if (mcap <= 0) return peak >= 4000;
  // −20% from ATH/session peak — hide dumps (matches server)
  if (peak >= 2000 && mcap < peak * 0.80) return true;
  if (peak >= 1800 && mcap < peak * 0.6) return true;
  const pc = t.priceChange || t.market?.priceChange || {};
  if (Number(pc.m5) <= -18 || Number(pc.h1) <= -22 || Number(pc.h6) <= -28 || Number(pc.h24) <= -40) {
    return true;
  }
  const deep = t.deepAnalysis || {};
  if (deep.dump?.is_dumped) return true;
  if (deep.verdict === "SKIP" && Number(deep.dump?.dump_pct_from_ath || 0) >= 20) return true;
  if (peak >= 10000 && mcap < 7000) return true;
  if (peak >= 18000 && mcap < peak * 0.5) return true;
  if (peak >= 5000 && peak < 18000 && mcap < 3200) return true;
  if (ath >= 12000 && mcap > 0 && mcap < ath * 0.45) return true;
  return false;
}

/** Strip dumped / hard-blocked tokens from every list before render */
function purgeDumpedTokens(tokens) {
  if (!Array.isArray(tokens)) return [];
  const out = [];
  for (const t of tokens) {
    trackTokenPeak(t);
    if (isHardBlocked(t) || isClientCrashedRunner(t)) continue;
    out.push(t);
  }
  savePeakMap();
  return out;
}

/**
 * Enrich live tokens with peak tracking only.
 * Never re-inject tokens missing from the live scan (that resurrected dumps).
 */
function mergeStickyNearMigration(tokens) {
  const live = Array.isArray(tokens) ? tokens : [];
  const out = [];
  for (const t of live) {
    const mint = mintOf(t);
    if (!mint || isHardBlocked(mint)) continue;
    trackTokenPeak(t);
    if (isClientCrashedRunner(t)) continue;
    out.push(t);
  }
  savePeakMap();
  return out;
}

function initChains() {
  const container = $("#chainChips");
  CHAINS.forEach((chain) => {
    const chip = document.createElement("button");
    chip.className = `chip${selectedChains.has(chain) ? " active" : ""}`;
    chip.textContent = chain;
    chip.onclick = () => {
      if (selectedChains.has(chain)) selectedChains.delete(chain);
      else selectedChains.add(chain);
      chip.classList.toggle("active");
    };
    container.appendChild(chip);
  });
}

function setStatus(msg, loading = false) {
  statusBar.textContent = msg;
  statusBar.classList.toggle("loading", loading);
}

function showLoadingGrid(msg = "Scanning Padre Trenches + RugCheck…") {
  setGridHtml(`
    <div class="loading-state">
      <div class="loading-spinner"></div>
      <p>${msg}</p>
      <p class="loading-hint">First load can take 30–90s. If stuck: start local server (start.bat) or switch Backend → Local.</p>
    </div>`);
  if (statCount) statCount.textContent = "…";
}

async function fetchWithTimeout(url, timeoutMs = SCAN_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    // Relative /api paths go through apiUrl() for local↔cloud sync
    const full = url.startsWith("http") ? url : apiUrl(url);
    const res = await fetch(full, { signal: ctrl.signal });
    if (!res.ok) throw new Error((await res.text()).slice(0, 200) || `HTTP ${res.status}`);
    return res;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error("Scan timed out — try a lower limit (10) or click Scan again");
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function updateBackendPill() {
  const pill = $("#backendPill");
  if (!pill) return;
  const mode = getApiModeLabel();
  const base = getApiBase() || location.origin;
  pill.textContent = mode === "cloud" || IS_CLOUD_HOST ? "Cloud synced" : "Local only";
  pill.classList.toggle("cloud", mode === "cloud" || IS_CLOUD_HOST);
  pill.classList.toggle("local", mode === "local" && !IS_CLOUD_HOST);
  pill.title = `API: ${base}`;
}

function initBackendSync() {
  const sel = $("#apiBackend");
  if (!sel) return;
  if (IS_CLOUD_HOST) {
    sel.value = "cloud";
    sel.disabled = true;
    updateBackendPill();
    return;
  }
  sel.value = getApiModeLabel();
  sel.onchange = () => {
    localStorage.setItem("moon_api_mode", sel.value);
    updateBackendPill();
    lastTokens = [];
    setStatus(
      sel.value === "cloud"
        ? `Switched to cloud backend (${CLOUD_API}) — same tokens as Render`
        : "Switched to local backend — independent scan",
      true
    );
    runScan(true);
  };
  updateBackendPill();
}

function fmtUsd(n) {
  const v = parseFloat(n);
  if (!v || isNaN(v)) return "—";
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(2)}`;
}

function fmtPct(n) {
  const v = parseFloat(n);
  if (isNaN(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
}

function fmtPrice(n) {
  const v = parseFloat(n);
  if (!v || isNaN(v)) return "—";
  if (v < 0.00001) return `$${v.toExponential(2)}`;
  if (v < 1) return `$${v.toFixed(8)}`;
  return `$${v.toFixed(4)}`;
}

const SOURCE_LABELS = {
  "pump.fun": "pump.fun",
  padre_trenches_new: "Padre NEW",
  padre_trenches_almost_bonded: "Almost Bonded",
  padre_trenches_recently_bonded: "Recently Bonded",
  padre_trending: "Trending",
  padre_new_pairs: "New Pairs",
  approaching_6k: "Approaching $6K",
};

function checkerStatusClass(status) {
  const s = (status || "unknown").toLowerCase();
  if (s === "pass") return "pass";
  if (s === "warn") return "warn";
  if (s === "fail") return "fail";
  return "unknown";
}

function checkerMiniHtml(hub) {
  if (!hub?.checkers?.length) return "";
  const dots = hub.checkers.map((ch) => {
    const cls = checkerStatusClass(ch.status);
    const tip = `${ch.name}: ${ch.summary}`;
    return `<span class="checker-dot ${cls}" title="${tip}">${ch.icon}</span>`;
  }).join("");
  return `<div class="checker-mini" aria-label="Security checker results">${dots}</div>`;
}

function checkerHubHtml(hub, compact = false) {
  if (!hub?.checkers?.length) return "";
  const c = hub.consensus || {};
  if (compact) {
    return `<div class="checker-compact ${checkerStatusClass(c.verdict)}">
      <span class="checker-compact-score">${c.passed ?? 0}/${c.total ?? 0} checkers</span>
      <span class="checker-compact-verdict">${c.verdict || "—"} ${c.score ?? 0}%</span>
    </div>${checkerMiniHtml(hub)}`;
  }
  const rows = hub.checkers.map((ch) => `
    <div class="checker-row ${checkerStatusClass(ch.status)}">
      <div class="checker-row-head">
        <span class="checker-icon">${ch.icon}</span>
        <span class="checker-name">${ch.name}</span>
        ${ch.score ? `<span class="checker-score">${ch.score}</span>` : ""}
      </div>
      <div class="checker-summary">${ch.summary}</div>
      ${(ch.details || []).slice(0, 3).map((d) => `<div class="checker-detail">${d}</div>`).join("")}
      ${(ch.issues || []).slice(0, 2).map((i) => `<div class="checker-issue">${i}</div>`).join("")}
      ${ch.url ? `<a class="checker-link" href="${ch.url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Open ${ch.name} →</a>` : ""}
    </div>`).join("");
  const links = hub.links || {};
  const linkBtns = Object.entries(links).map(([k, url]) =>
    `<a class="action-btn checker-ext" href="${url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${k}</a>`
  ).join("");
  return `
    <div class="checker-panel">
      <div class="checker-consensus ${checkerStatusClass(c.verdict)}">
        <strong>Security consensus: ${c.verdict || "—"}</strong> — ${c.summary || ""}
        <span class="checker-pct">${c.score ?? 0}%</span>
      </div>
      <div class="checker-grid">${rows}</div>
      <div class="action-links checker-links">${linkBtns}</div>
    </div>`;
}

function socialBadgesHtml(social) {
  if (!social?.badges?.length) return "";
  const badges = social.badges.map((b) => {
    let cls = "social-badge";
    if (b.type === "influencer" || b.type === "influencer_tweet") cls += " influencer";
    else if (b.id === "tiktok") cls += " tiktok";
    else if (b.id === "x") cls += " x-social";
    else if (b.type === "narrative") cls += " narrative";
    return `<span class="${cls}" title="${social.summary || ""}">${b.label}</span>`;
  });
  return `<div class="social-badges">${badges.join("")}</div>`;
}

function tradePlanHtml(plan, compact = false) {
  if (!plan || !plan.action) return "";
  const act = plan.action;
  const cls = act === "ENTER" ? "enter" : act === "SKIP" ? "skip" : "watch";
  if (compact) {
    return `<div class="trade-plan-row" title="${plan.summary || ""}">
      <span class="trade-plan-badge ${cls}">${act} ${plan.confidence || 0}%</span>
      ${plan.p_good != null ? `<span class="trade-plan-meta">win≈${Math.round((plan.p_good || 0) * 100)}%</span>` : ""}
    </div>`;
  }
  const tps = (plan.take_profit || []).map((t) =>
    `<div class="analysis-item"><div class="k">${t.label} (${t.multiple}x)</div>
     <div class="v">${t.mcap ? fmtUsd(t.mcap) : "—"} · ${t.action || ""}</div></div>`
  ).join("");
  const sl = plan.stop_loss || {};
  const exits = (plan.exit_triggers || []).slice(0, 5).map((e) => `<li>${e}</li>`).join("");
  const entry = plan.entry || {};
  return `<div class="analysis-section trade-plan-panel">
    <h4>Learned plan: ${act} (${plan.confidence || 0}%)</h4>
    <p style="color:var(--accent);margin-bottom:8px">${plan.summary || ""}</p>
    <div class="analysis-grid">
      <div class="analysis-item"><div class="k">P(good)</div><div class="v">${Math.round((plan.p_good || 0) * 100)}%</div></div>
      <div class="analysis-item"><div class="k">P(bad)</div><div class="v">${Math.round((plan.p_bad || 0) * 100)}%</div></div>
      <div class="analysis-item"><div class="k">Samples</div><div class="v">${plan.sample_size ?? 0}</div></div>
      <div class="analysis-item"><div class="k">Avg winner</div><div class="v">${plan.learned_avg_winner_multiple || "—"}x</div></div>
      <div class="analysis-item"><div class="k">Sweet entry</div><div class="v">${entry.ideal_min_mcap ? fmtUsd(entry.ideal_min_mcap) + "–" + fmtUsd(entry.ideal_max_mcap) : "—"}</div></div>
      <div class="analysis-item"><div class="k">Now</div><div class="v">${entry.current_mcap ? fmtUsd(entry.current_mcap) : "—"} ${entry.in_sweet_zone ? "✓ sweet" : ""}</div></div>
    </div>
    <h4 style="margin-top:12px">Take profit</h4>
    <div class="analysis-grid">${tps}</div>
    <h4 style="margin-top:12px">Stop loss</h4>
    <div class="analysis-grid">
      <div class="analysis-item"><div class="k">SL (${sl.multiple || "—"}x)</div>
      <div class="v">${sl.mcap ? fmtUsd(sl.mcap) : "—"} · ${sl.action || ""}</div></div>
    </div>
    ${plan.dev_dump_hint_mcap ? `<p style="margin-top:8px;color:var(--warn)">Historical dev-dump cluster ~${fmtUsd(plan.dev_dump_hint_mcap)}</p>` : ""}
    <ul class="reason-list" style="margin-top:8px">${exits}</ul>
  </div>`;
}

function fingerprintHtml(fp, compact = false) {
  if (!fp || !fp.score || fp.tier === "NONE") return "";
  const tier = fp.tier || "NONE";
  const cls = {
    MEGA_10M: "fp-badge mega10m",
    HIGH_10M: "fp-badge high10m",
    BUILDING_10M: "fp-badge building10m",
  }[tier] || "fp-badge";
  const label = {
    MEGA_10M: "💎 $10M FINGERPRINT",
    HIGH_10M: "◎ High $10M path",
    BUILDING_10M: "… Building $10M",
  }[tier] || tier;
  if (compact) {
    return `<div class="fp-row" title="${fp.summary || ""}">
      <span class="${cls}">${label} ${fp.score}</span>
      ${(fp.narrative_tags || []).slice(0, 2).map((t) =>
        `<span class="fp-tag">${String(t).replace(/_/g, " ")}</span>`
      ).join("")}
    </div>`;
  }
  const check = fp.checklist || {};
  const items = Object.entries(check).map(([k, v]) =>
    `<span class="fp-check ${v ? "on" : "off"}">${v ? "✓" : "✗"} ${k.replace(/_/g, " ")}</span>`
  ).join("");
  const tags = (fp.narrative_tags || []).map((t) =>
    `<span class="fp-tag">${String(t).replace(/_/g, " ")}</span>`
  ).join("");
  const ladder = fp.tp_ladder || {};
  const missing = (fp.missing || []).slice(0, 5).join(", ");
  return `<div class="analysis-section fingerprint-panel">
    <h4>${label} · ${fp.score}/100 · ${fp.checklist_hits || 0}/${fp.checklist_total || 0} checks</h4>
    <p style="color:var(--accent);margin-bottom:8px">${fp.summary || ""}</p>
    <div class="fp-tags">${tags}</div>
    <div class="fp-checklist">${items}</div>
    ${ladder.tp1_mcap ? `<div class="analysis-grid" style="margin:8px 0">
      <div class="analysis-item"><div class="k">TP1</div><div class="v">${fmtUsd(ladder.tp1_mcap)} · sell ${ladder.sell_pct?.tp1 ?? 30}%</div></div>
      <div class="analysis-item"><div class="k">TP2</div><div class="v">${fmtUsd(ladder.tp2_mcap)} · sell ${ladder.sell_pct?.tp2 ?? 25}%</div></div>
      <div class="analysis-item"><div class="k">TP3</div><div class="v">${fmtUsd(ladder.tp3_mcap)} · sell ${ladder.sell_pct?.tp3 ?? 20}%</div></div>
      <div class="analysis-item"><div class="k">Moon / mega</div><div class="v">${fmtUsd(ladder.moon_mcap || 0)} → ${fmtUsd(ladder.mega_band_mcap || 0)}</div></div>
    </div>` : ""}
    ${missing ? `<p class="fp-missing">Missing: ${missing}</p>` : ""}
    ${ladder.notes ? `<p class="fp-notes">${ladder.notes}</p>` : ""}
  </div>`;
}

function alphaSetupHtml(alpha, compact = false) {
  if (!alpha || alpha.tier === "WEAK" || alpha.tier === "SKIP" || !alpha.score) return "";
  if (!alpha.highlight && alpha.score < 45 && !(alpha.megaFingerprint || {}).score) return "";
  const tier = alpha.tier || "SPEC";
  const fp = alpha.megaFingerprint || {};
  const is10m = alpha.is_mega_10m || fp.tier === "MEGA_10M" || alpha.ceiling === "10M_to_100M";
  const cls = {
    MEGA_MOON: is10m ? "alpha-badge mega mega10m" : "alpha-badge mega",
    MOON_SETUP: "alpha-badge moon",
    ALPHA: "alpha-badge alpha",
    WATCH_ALPHA: "alpha-badge watch",
    SPEC: "alpha-badge spec",
  }[tier] || "alpha-badge";
  const label = {
    MEGA_MOON: is10m ? "💎 MEGA $10M+" : "💎 MEGA MOON",
    MOON_SETUP: "🚀 MOON SETUP",
    ALPHA: "✦ ALPHA",
    WATCH_ALPHA: "◎ Building mega",
    SPEC: "Spec (low ceiling)",
  }[tier] || tier;
  const ceil = alpha.ceiling_label ? ` · ${alpha.ceiling_label}` : "";
  const win = (alpha.entry_window || "").replace(/_/g, " ");
  if (compact) {
    return `<div class="alpha-row" title="${alpha.summary || ""}">
      <span class="${cls}">${label} ${alpha.score}${ceil}</span>
      ${fp.score ? `<span class="fp-badge ${fp.tier === "MEGA_10M" ? "mega10m" : "high10m"}">FP ${fp.score}</span>` : ""}
      ${win ? `<span class="alpha-window">${win}</span>` : ""}
    </div>`;
  }
  const reasons = (alpha.reasons || []).slice(0, 8).map((r) => `<li>${r}</li>`).join("");
  const badges = (alpha.badges || []).map((b) =>
    `<span class="alpha-mini ${b.type || ""}">${b.label}</span>`
  ).join("");
  const tps = alpha.tp_mcap_targets || {};
  return `<div class="analysis-section alpha-panel">
    <h4>${label} · score ${alpha.score} · ceiling ${alpha.ceiling_label || "n/a"}</h4>
    <p style="color:var(--accent);margin-bottom:8px">${alpha.summary || ""}</p>
    <div class="alpha-minis">${badges}</div>
    ${tps.tp1_mcap ? `<div class="analysis-grid" style="margin:8px 0">
      <div class="analysis-item"><div class="k">TP1 mcap</div><div class="v">${fmtUsd(tps.tp1_mcap)}${tps.sell_pct?.tp1 != null ? ` · sell ${tps.sell_pct.tp1}%` : ""}</div></div>
      <div class="analysis-item"><div class="k">TP2 mcap</div><div class="v">${fmtUsd(tps.tp2_mcap)}${tps.sell_pct?.tp2 != null ? ` · sell ${tps.sell_pct.tp2}%` : ""}</div></div>
      <div class="analysis-item"><div class="k">TP3 / moon</div><div class="v">${fmtUsd(tps.tp3_mcap || tps.moon_mcap)}${tps.mega_band_mcap ? ` → ${fmtUsd(tps.mega_band_mcap)}` : ""}</div></div>
    </div>` : ""}
    ${fingerprintHtml(fp, false)}
    <ul class="reason-list">${reasons}</ul>
  </div>`;
}

function avoidBadgesHtml(avoid) {
  if (!avoid?.avoid && !avoid?.flags?.length) return "";
  const reason = avoid.summary || (avoid.reasons || [])[0] || "Junk pattern";
  const flags = (avoid.flags || []).slice(0, 3).join(", ");
  return `<div class="avoid-row" title="${reason}">
    <span class="avoid-badge">⛔ AVOID${flags ? ` · ${flags}` : ""}</span>
  </div>`;
}

function smartMoneyBadgesHtml(sm) {
  if (!sm || sm.signal === "NONE" || !sm.signal) return "";
  const sig = sm.signal;
  let label = "Whale";
  let cls = "smart-money-badge whale";
  if (sig === "MAJOR_TRADER") {
    label = "🐋 Major trader";
    cls = "smart-money-badge major";
  } else if (sig === "WHALE_BUY") {
    label = "🐋 Whale buy";
    cls = "smart-money-badge whale";
  } else if (sig === "DISTRIBUTED_WHALES") {
    label = "Distributed whales";
    cls = "smart-money-badge distributed";
  } else if (sig === "PAID_INTEREST") {
    label = "Paid promo";
    cls = "smart-money-badge paid";
  } else if (sig === "TAINTED") {
    label = "Large bag ⚠ insider risk";
    cls = "smart-money-badge tainted";
  }
  if (sm.anti_rug_signal) label += " · anti-rug";
  return `<div class="smart-money-row" title="${sm.summary || ""}">
    <span class="${cls}">${label}</span>
    ${sm.confidence ? `<span class="smart-money-conf">${sm.confidence}%</span>` : ""}
  </div>`;
}

function smartMoneyPanelHtml(sm) {
  if (!sm || sm.signal === "NONE" || !sm.signal) {
    return `<div class="analysis-section"><h4>Major traders / whales</h4>
      <p style="color:var(--muted)">No major trader or healthy whale bag detected yet.</p></div>`;
  }
  const known = (sm.known_traders || []).map((t) =>
    `<div class="analysis-item"><div class="k">${t.label}</div>
     <div class="v">${t.pct}% · ~$${Number(t.est_usd || 0).toLocaleString()}</div>
     <div class="smart-money-addr">${t.owner?.slice(0, 6)}…${t.owner?.slice(-4) || ""}</div></div>`
  ).join("");
  const whales = (sm.whale_holders || []).map((t) =>
    `<div class="analysis-item"><div class="k">Whale ${t.pct}%</div>
     <div class="v">~$${Number(t.est_usd || 0).toLocaleString()}</div>
     <div class="smart-money-addr">${t.owner?.slice(0, 6)}…${t.owner?.slice(-4) || ""}</div></div>`
  ).join("");
  return `
    <div class="analysis-section">
      <h4>Major traders / whales ${sm.anti_rug_signal ? "✓ anti-rug signal" : ""}</h4>
      <p style="margin-bottom:10px;color:var(--accent)">${sm.summary || ""}</p>
      ${smartMoneyBadgesHtml(sm)}
      <div class="analysis-grid" style="margin-top:10px">${known}${whales}</div>
      ${(sm.paid_interest || []).length ? `<p style="margin-top:8px;color:var(--muted);font-size:0.8rem">DexScreener: ${(sm.paid_interest || []).map((p) => p.label).join(", ")}</p>` : ""}
    </div>`;
}

function sourceBadgesHtml(sources) {
  if (!sources?.length) return "";
  const badges = sources.map((s) => {
    const label = SOURCE_LABELS[s] || s.replace("padre_", "Padre ");
    const cls = s === "pump.fun" ? "source-badge pump" : "source-badge";
    return `<span class="${cls}">${label}</span>`;
  });
  return `<div class="source-badges">${badges.join("")}</div>`;
}

function volTrendClass(trend) {
  return `vol-trend-${trend || "stable"}`;
}

function devRiskClass(level) {
  return `dev-risk-${level || "medium"}`;
}

function gradeClass(grade) {
  if (!grade) return "grade-d";
  if (grade.startsWith("A")) return "grade-a";
  if (grade === "B") return "grade-b";
  if (grade === "C") return "grade-c";
  return "grade-d";
}

function shorten(addr) {
  if (!addr || addr.length < 12) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  }
  const label = btn.querySelector(".copy-addr-text") || btn;
  const original = btn.dataset.label || label.textContent;
  btn.classList.add("copied");
  label.textContent = "Copied!";
  setStatus(`Copied ${shorten(text)}`);
  setTimeout(() => {
    btn.classList.remove("copied");
    label.textContent = original;
  }, 1500);
}

function copyBtnHtml(address, { full = false, size = "sm" } = {}) {
  const text = full ? address : shorten(address);
  return `<button type="button" class="copy-addr copy-addr--${size}" data-copy="${address}" data-label="${text}" title="Copy contract address">
    <span class="copy-addr-text">${text}</span>
    <span class="copy-icon" aria-hidden="true">⧉</span>
  </button>`;
}

function renderCard(token) {
  const m = token.market || {};
  const base = m.baseToken || {};
  const safety = token.safety || {};
  const moon = token.moonScore || {};
  const entry = token.entrySignal || {};
  const exit = token.exitSignal || {};
  const invest = token.investSignal || {};
  const trench = token.trenchAnalysis || invest.trench || {};
  const market = invest.market || {};
  const vol = market.volume || {};
  const dev = market.dev || {};
  const pc = m.priceChange || {};
  const social = token.socialSignals || {};
  const sm = token.smartMoney || {};
  const alpha = token.alphaSetup || {};
  const plan = token.tradePlan || {};
  const avoid = token.safety?.avoid || token.safetyReport?.avoid || {};
  const hub = token.checkerHub || {};

  const h1 = parseFloat(pc.h1);
  const h1Class = h1 >= 0 ? "up" : "down";

  const card = document.createElement("article");
  card.className = `token-card${social.highlight ? " token-card--narrative" : ""}${sm.anti_rug_signal ? " token-card--smart-money" : ""}${alpha.is_alpha ? " token-card--alpha" : ""}${avoid.avoid ? " token-card--avoid" : ""}`;
  card.addEventListener("click", (e) => {
    if (e.target.closest("[data-copy]")) return;
    openModal(token);
  });

  const iconHtml = token.icon
    ? `<img class="token-icon" src="${token.icon}" alt="" onerror="this.style.display='none'" />`
    : `<div class="token-icon placeholder">◎</div>`;

  const safetyTags = [];
  if (safety.passed) safetyTags.push('<span class="tag safe">✓ Safety Pass</span>');
  if (safety.is_honeypot) safetyTags.push('<span class="tag danger">Honeypot</span>');
  if (safety.sell_tax > 0) safetyTags.push(`<span class="tag">Sell tax ${safety.sell_tax}%</span>`);
  if (safety.rug_score !== undefined) safetyTags.push(`<span class="tag">Rug ${safety.rug_score}/100</span>`);
  if (safety.lp_locked_pct) safetyTags.push(`<span class="tag">LP ${safety.lp_locked_pct.toFixed(0)}%</span>`);
  if (m.is_pumpfun) safetyTags.push('<span class="tag safe">pump.fun</span>');
  if (token.padre?.trade) safetyTags.push('<span class="tag">Padre</span>');
  if (m.pumpfun?.bonding_progress != null) {
    safetyTags.push(`<span class="tag">Curve ${m.pumpfun.bonding_progress}%</span>`);
  }
  if (social.highlight) {
    if (social.influencer_tweet) safetyTags.push('<span class="tag influencer">Influencer tweet</span>');
    if (social.has_x) safetyTags.push('<span class="tag x-tag">X</span>');
    if (social.has_tiktok) safetyTags.push('<span class="tag tiktok-tag">TikTok</span>');
    (social.narratives || []).slice(0, 2).forEach((n) => {
      safetyTags.push(`<span class="tag narrative-tag">${n}</span>`);
    });
  }

  const ageDisplay = m.age_minutes != null
    ? `${m.age_minutes}m`
    : m.age_hours != null
      ? `${m.age_hours}h`
      : "—";

  const mcapDisplay = m.pumpfun?.usd_market_cap
    ? fmtUsd(m.pumpfun.usd_market_cap)
    : fmtUsd(m.marketCap || m.fdv);

  const investSignal = invest.signal || entry.signal || "WATCH";
  const investConf = invest.confidence ?? entry.confidence ?? 0;

  if (sm.anti_rug_signal) {
    safetyTags.unshift('<span class="tag smart-money-tag">🐋 Major / whale buy</span>');
  }

  card.innerHTML = `
    ${sourceBadgesHtml(token.sources)}
    ${socialBadgesHtml(social)}
    ${alphaSetupHtml(alpha, true)}
    ${fingerprintHtml(alpha.megaFingerprint || {}, true)}
    ${tradePlanHtml(plan, true)}
    ${avoidBadgesHtml(avoid)}
    ${smartMoneyBadgesHtml(sm)}
    ${checkerHubHtml(hub, true)}
    <div class="invest-banner ${avoid.avoid || plan.action === "SKIP" ? "AVOID" : (plan.action === "ENTER" || alpha.is_alpha ? "STRONG_INVEST" : investSignal)}">
      <div class="invest-title">▸ ${avoid.avoid || plan.action === "SKIP" ? "AVOID" : (plan.action === "ENTER" ? "ENTER (learned)" : (alpha.is_alpha ? alpha.tier.replace(/_/g, " ") : investSignal.replace(/_/g, " ")))} (${plan.confidence || (alpha.is_alpha ? alpha.confidence : investConf)}%)</div>
      <div class="invest-action">${avoid.avoid ? (avoid.summary || "Junk / ghost launch") : (plan.summary || (alpha.is_alpha ? alpha.summary : (invest.summary || invest.action || entry.action || "")))}</div>
    </div>
    <div class="moon-score">
      <div class="score-ring ${gradeClass(moon.grade)}">${moon.total || 0}</div>
      <div class="score-grade">${moon.grade || "—"}</div>
    </div>
    <div class="card-header">
      ${iconHtml}
      <div class="card-title">
        <h3>${base.name || "Unknown"}</h3>
        <div class="symbol">$${base.symbol || "?"} · ${fmtPrice(m.priceUsd)}</div>
        <span class="chain-badge">${token.chainId}</span>
        ${copyBtnHtml(token.tokenAddress)}
      </div>
    </div>
    <div class="metrics">
      <div class="metric"><div class="label">MCap</div><div class="value">${mcapDisplay}</div></div>
      <div class="metric"><div class="label">Bonding</div><div class="value">${m.pumpfun?.bonding_progress != null ? m.pumpfun.bonding_progress + "%" : "—"}</div></div>
      <div class="metric"><div class="label">Replies</div><div class="value">${m.pumpfun?.reply_count ?? "—"}</div></div>
      <div class="metric"><div class="label">Age</div><div class="value age-fresh">${ageDisplay}</div></div>
    </div>
    <div class="metrics" style="margin-top:8px">
      <div class="metric"><div class="label">MCap</div><div class="value">${trench.mcap_usd ? fmtUsd(trench.mcap_usd) : mcapDisplay}</div></div>
      <div class="metric"><div class="label">5m</div><div class="value ${(trench.price_change_m5 ?? pc.m5) >= 0 ? "up" : "down"}">${fmtPct(trench.price_change_m5 ?? pc.m5)}</div></div>
      <div class="metric"><div class="label">Snipers</div><div class="value ${devRiskClass((trench.snipers || {}).risk_level)}">${(trench.snipers || {}).risk_level || "—"}</div></div>
      <div class="metric"><div class="label">Trench</div><div class="value">${trench.passed ? "✓ Pass" : "✗ Wait"}</div></div>
    </div>
    <div class="signals">
      <span class="signal-badge signal-${investSignal}">Invest: ${investSignal}</span>
      <span class="signal-badge signal-${exit.signal}">Exit: ${exit.signal}</span>
    </div>
    <div class="safety-tags">${safetyTags.join("")}</div>
    ${token.padre?.trade ? `<div class="action-links">
      <a class="action-btn padre" href="${token.padre.trade}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Trade on Padre</a>
      ${m.pumpfun?.pump_url ? `<a class="action-btn pump" href="${m.pumpfun.pump_url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">pump.fun</a>` : ""}
    </div>` : ""}
  `;
  return card;
}

function renderTrenchesCard(t) {
  const rep = t.safetyReport || {};
  const bundle = rep.bundle || {};
  const snipers = rep.snipers || {};
  const social = t.socialSignals || {};
  const sm = t.smartMoney || t.safetyReport?.smartMoney || {};
  const alpha = t.alphaSetup || {};
  const plan = t.tradePlan || {};
  const avoid = t.safetyReport?.avoid || {};
  const hub = t.checkerHub || t.safetyReport?.checkerHub || {};
  const tier = t.safetyTier || "AVOID";
  const isPreview = t.preview || tier === "SCANNING";
  const card = document.createElement("article");
  const mig = t.migrationPath || {};
  const lane = tokenLane(t);
  card.className = `token-card lane-${lane}${social.highlight ? " token-card--narrative" : ""}${isPreview ? " token-card--scanning" : ""}${sm.anti_rug_signal ? " token-card--smart-money" : ""}${lane === "near_migration" ? " token-card--migration" : ""}${alpha.is_alpha || plan.action === "ENTER" ? " token-card--alpha" : ""}${avoid.avoid || plan.action === "SKIP" ? " token-card--avoid" : ""}`;
  card.addEventListener("click", () => openTrenchesModal(t));

  const iconHtml = t.icon
    ? `<img class="token-icon" src="${t.icon}" alt="" onerror="this.style.display='none'" />`
    : `<div class="token-icon placeholder">◎</div>`;

  const invSig = t.investSignal || "";
  // Never show ENTER/STRONG on early lottery (most die under $7k)
  const lottery = lane === "early_lottery";
  const dumped = isClientCrashedRunner(t);
  const bannerCls = isPreview
    ? "WATCH"
    : dumped || avoid.avoid || plan.action === "SKIP" || invSig === "AVOID"
      ? "AVOID"
      : lottery
        ? "WATCH"
        : lane === "near_migration" && (invSig === "STRONG_INVEST" || invSig === "INVEST" || (mig.score || 0) >= 55)
          ? "STRONG_INVEST"
          : invSig === "STRONG_INVEST" || invSig === "INVEST"
            ? invSig
            : tier === "SAFE_ENTRY"
              ? "WATCH"
              : tier === "WATCH"
                ? "WATCH"
                : "AVOID";
  const mcap = t.mcap_usd || 0;
  const sweet = t.entrySweet || (mcap >= 3500 && mcap <= 7500);
  const sixk = t.sixkRadar || (mcap >= 2000 && mcap <= 9000);
  const bond = Number(t.bonding_progress ?? mig.bonding_pct ?? 0);
  const title = isPreview
    ? (lane === "near_migration" ? `🚀 ${bond.toFixed(0)}% MIGRATION PATH` : sweet ? "🎯 LOTTERY (no ENTER)" : sixk ? "EARLY LOTTERY" : "SCANNING…")
    : dumped
      ? "DUMPED — skip"
      : lottery
        ? `EARLY LOTTERY · no ENTER · ${tier}`
        : lane === "near_migration"
          ? `NEAR MIGRATION ${bond.toFixed(0)}% (mig ${mig.score ?? "—"})`
          : alpha.is_alpha
            ? `${alpha.tier.replace(/_/g, " ")} (${alpha.score})`
            : `${tier} (${t.safetyScore ?? 0}%)`;
  const action = isPreview
    ? (lane === "near_migration"
      ? `MCap ${fmtUsd(mcap)} · ${bond.toFixed(0)}% bonded — analyzing…`
      : lottery
        ? `Most die under $7k — watch only · ${fmtUsd(mcap)}`
        : (rep.verdict || "RugCheck + Padre analysis running…"))
    : dumped
      ? "Already dumped from peak — removed from recommendations"
      : lottery
        ? (t.investSummary || plan.summary || "Early lottery — most never clear $7k. No ENTER.")
        : (mig.summary || alpha.summary || plan.summary || rep.verdict || "");

  card.innerHTML = `
    ${sourceBadgesHtml([t.column ? `padre_trenches_${t.column}` : "pump.fun"])}
    ${migrationBadgeHtml(t)}
    ${txActivityBadgeHtml(t)}
    ${deepAnalysisHtml(t, true)}
    ${(t.runnerRadar || {}).alert || (t.runnerRadar || {}).score >= 55 ? `<div class="runner-score-row"><span class="runner-score-badge">⚡ RUNNER ${t.runnerRadar.score} · ${(t.runnerRadar.stage || "").replace(/_/g, " ")}</span></div>` : ""}
    ${sixk && lane === "early_lottery" ? `<div class="sixk-row"><span class="sixk-badge ${sweet ? "sweet" : ""}">${sweet ? "🎯 LOTTERY $3.5–7.5K" : "EARLY LOTTERY"} · ${fmtUsd(mcap)}</span></div>` : ""}
    ${socialBadgesHtml(social)}
    ${alphaSetupHtml(alpha, true)}
    ${fingerprintHtml(alpha.megaFingerprint || {}, true)}
    ${tradePlanHtml(plan, true)}
    ${avoidBadgesHtml(avoid)}
    ${smartMoneyBadgesHtml(sm)}
    <div class="invest-banner ${bannerCls}">
      <div class="invest-title">▸ ${title}</div>
      <div class="invest-action">${action}</div>
    </div>
    <div class="card-header">
      ${iconHtml}
      <div class="card-title">
        <h3>${t.name || "Unknown"}</h3>
        <div class="symbol">$${t.symbol || "?"} · ${t.column || "trenches"}</div>
        <span class="chain-badge">solana</span>
        ${copyBtnHtml(t.tokenAddress)}
      </div>
    </div>
    ${checkerHubHtml(hub, true)}
    <div class="metrics">
      <div class="metric"><div class="label">MCap</div><div class="value">${fmtUsd(t.mcap_usd)}</div></div>
      <div class="metric"><div class="label">Bonding</div><div class="value">${bond ? bond.toFixed(0) + "%" : "—"}</div></div>
      <div class="metric"><div class="label">Age</div><div class="value">${t.age_minutes ?? "—"}m</div></div>
      <div class="metric"><div class="label">Snipers</div><div class="value ${devRiskClass(snipers.risk_level)}">${snipers.risk_level || "—"}</div></div>
    </div>
    <div class="action-links">
      ${t.padre?.trade ? `<a class="action-btn padre" href="${t.padre.trade}" target="_blank" rel="noopener" onclick="event.stopPropagation()">Trade Padre</a>` : ""}
      ${t.pump_url ? `<a class="action-btn pump" href="${t.pump_url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">pump.fun</a>` : ""}
    </div>`;
  return card;
}

function openTrenchesModal(t) {
  const rep = t.safetyReport || {};
  const checks = (rep.checks || []).map((c) =>
    `<div class="analysis-item"><div class="k">${c.ok ? "✓" : "✗"} ${c.name.replace(/_/g, " ")}</div><div class="v">${c.detail}</div></div>`
  ).join("");
  const blockers = (rep.blockers || []).map((b) => `<li>${b.detail}</li>`).join("");

  const social = t.socialSignals || {};
  const sm = t.smartMoney || rep.smartMoney || {};
  const alpha = t.alphaSetup || {};
  const avoid = rep.avoid || t.avoid || {};
  const hub = t.checkerHub || rep.checkerHub || {};
  $("#modalContent").innerHTML = `
    <h2>${t.name || "Token"} ($${t.symbol || "?"})</h2>
    <div class="addr-copy-row">${copyBtnHtml(t.tokenAddress, { full: true, size: "lg" })}</div>
    ${socialBadgesHtml(social)}
    ${social.summary ? `<p style="color:var(--accent);margin-bottom:12px">${social.summary}</p>` : ""}
    ${social.x_url ? `<p style="margin-bottom:8px"><a href="${social.x_url}" target="_blank" rel="noopener" style="color:#5b9fff">X / Twitter →</a></p>` : ""}
    ${social.tiktok_url ? `<p style="margin-bottom:8px"><a href="${social.tiktok_url}" target="_blank" rel="noopener" style="color:#ff6b9d">TikTok →</a></p>` : ""}
    <p style="color:var(--muted);margin-bottom:16px">${rep.verdict || ""}</p>
    ${deepAnalysisHtml(t, false)}
    ${alphaSetupHtml(alpha)}
    ${fingerprintHtml(alpha.megaFingerprint || {})}
    ${tradePlanHtml(t.tradePlan || {})}
    ${avoid.avoid ? `<div class="analysis-section"><h4>⛔ Avoid filters</h4>
      <p style="color:var(--danger)">${avoid.summary || ""}</p>
      <ul class="reason-list issue-list">${(avoid.reasons || []).map((r) => `<li>${r}</li>`).join("")}</ul>
    </div>` : ""}
    ${smartMoneyPanelHtml(sm)}
    ${checkerHubHtml(hub)}
    <div class="analysis-section" style="margin-top:16px"><h4>Trench Checks</h4></div>
    <div class="analysis-grid">${checks}</div>
    ${blockers ? `<ul class="reason-list issue-list" style="margin-top:12px">${blockers}</ul>` : ""}
    <div class="analysis-grid" style="margin-top:16px">
      <div class="analysis-item"><div class="k">Dev holds</div><div class="v">${rep.dev?.holds_pct ?? 0}%</div></div>
      <div class="analysis-item"><div class="k">Max wallet</div><div class="v">${rep.snipers?.max_wallet_pct ?? "—"}%</div></div>
      <div class="analysis-item"><div class="k">Insiders</div><div class="v">${rep.snipers?.insider_count ?? 0}</div></div>
      <div class="analysis-item"><div class="k">Replies</div><div class="v">${rep.community?.reply_count ?? 0}</div></div>
    </div>`;
  $("#modalOverlay").classList.add("open");
}

function flattenTrenchesData(data) {
  if (!data?.columns && data?.tokens) return data.tokens || [];
  if (!data?.columns) return data?.tokens || [];
  return [
    ...(data.migration_picks || []),
    ...(data.under25k_picks || []),
    ...(data.early_lottery || []),
    ...(data.sixk_picks || []),
    ...(data.alpha_picks || []),
    ...(data.safe_picks || []),
    ...(data.columns?.almost_bonded || []),
    ...(data.columns?.under_25k || []),
    ...(data.columns?.sixk_radar || []),
    ...(data.columns?.new || []),
    ...(data.columns?.recently_bonded || []),
  ].filter((tok, i, arr) => {
    const k = tok.tokenAddress;
    return k && arr.findIndex((x) => x.tokenAddress === k) === i;
  });
}

function groupByLane(tokens) {
  const groups = {
    near_migration: [],
    under_25k: [],
    early_lottery: [],
    migrated: [],
    other: [],
  };
  for (const t of tokens) {
    const lane = tokenLane(t);
    if (groups[lane]) groups[lane].push(t);
    else groups.other.push(t);
  }
  // Prefer higher bonding within each group
  for (const k of Object.keys(groups)) {
    groups[k].sort((a, b) => {
      const ba = Number(a.bonding_progress ?? a.migrationPath?.bonding_pct ?? 0);
      const bb = Number(b.bonding_progress ?? b.migrationPath?.bonding_pct ?? 0);
      const sa = a.migrationPath?.score || 0;
      const sb = b.migrationPath?.score || 0;
      return sb - sa || bb - ba || (b.mcap_usd || 0) - (a.mcap_usd || 0);
    });
  }
  return groups;
}

function applyClientFilters(tokens) {
  let out = [...tokens];
  if ($("#checkerPassOnly")?.checked) {
    out = out.filter((t) => {
      if (t.preview || t.safetyTier === "SCANNING") return true;
      const v = (t.checkerHub || t.safetyReport?.checkerHub || {}).consensus?.verdict;
      return v === "PASS";
    });
  }
  if ($("#safeOnly")?.checked) {
    out = out.filter((t) => {
      if (t.preview || t.safetyTier === "SCANNING") return true;
      if (t.safetyTier) return !["UNSAFE", "AVOID"].includes(t.safetyTier);
      const sig = t.investSignal?.signal || t.entrySignal?.signal;
      return !["AVOID"].includes(sig);
    });
  }
  return out;
}

function renderGrid(tokens) {
  if (!grid) {
    setStatus("UI error: #tokenSections missing — hard-refresh (Ctrl+F5)");
    return;
  }
  let visible = purgeDumpedTokens(applyClientFilters(tokens));
  const hidden = tokens.length - visible.length;
  if (sectionFilter && sectionFilter !== "all") {
    visible = visible.filter((t) => {
      const lane = tokenLane(t);
      if (sectionFilter === "runners") {
        if (isClientCrashedRunner(t)) return false;
        const rr = t.runnerRadar || {};
        if (rr.crashed || rr.stage === "crashed") return false;
        return rr.alert || rr.score >= 55;
      }
      if (sectionFilter === "near_migration") return lane === "near_migration" || lane === "migrated";
      return lane === sectionFilter;
    });
  }
  grid.innerHTML = "";
  if (sectionNav) sectionNav.hidden = !tokens.length;

  if (!visible.length) {
    let msg = "No tokens matched your filters.";
    if (tokens.length && hidden) {
      msg = `${tokens.length} tokens scanned but ${hidden} hidden (dumps/filters). Uncheck filters or view All.`;
    } else if (tokens.length && sectionFilter !== "all") {
      msg = `No tokens in this section yet. Try “All” or re-scan.`;
    } else if (!tokens.length) {
      msg = $("#checkerPassOnly")?.checked
        ? "No tokens passed checkers. Disable filters or re-scan."
        : "No tokens found — re-scan. If stuck: run start.bat, Backend → Local, open http://127.0.0.1:8765";
    }
    setGridHtml(`<div class="empty-state"><div class="icon">◈</div><p>${msg}</p>
      ${tokens.length ? `<button class="btn btn-secondary" id="showAllBtn">Show all ${tokens.length} scanned</button>` : ""}</div>`);
    const showAll = $("#showAllBtn");
    if (showAll) {
      showAll.onclick = () => {
        $("#safeOnly").checked = false;
        $("#checkerPassOnly").checked = false;
        sectionFilter = "all";
        document.querySelectorAll(".sec-btn").forEach((b) => b.classList.toggle("active", b.dataset.sec === "all"));
        renderGrid(tokens);
      };
    }
    if (statCount) statCount.textContent = "0";
    return;
  }

  const groups = groupByLane(visible);
  const runners = visible
    .filter((t) => {
      if (isClientCrashedRunner(t)) return false;
      const rr = t.runnerRadar || {};
      if (rr.crashed || rr.stage === "crashed") return false;
      return rr.alert || rr.score >= 55;
    })
    .sort((a, b) => ((b.runnerRadar || {}).score || 0) - ((a.runnerRadar || {}).score || 0));

  const sections = [
    {
      id: "runners",
      title: "⚡ Runner Radar ($10M–$100M path)",
      hint: "Multi-stage alerts: early structure · mid climb · near migration. Act fast.",
      tokens: runners,
    },
    {
      id: "near_migration",
      title: "Near Migration",
      hint: "Can actually graduate (~$69k). Primary focus — not $6k dust.",
      tokens: [...groups.near_migration, ...groups.migrated],
    },
    {
      id: "under_25k",
      title: "Under $25k",
      hint: "Mid-curve structure. Better than pure lottery; still must hold to migrate.",
      tokens: groups.under_25k,
    },
    {
      id: "early_lottery",
      title: "Early Lottery ($2–8k) — no ENTER",
      hint: "Historically most die under $7k. Shown for structure watch only — never size as a conviction play.",
      tokens: purgeDumpedTokens([...groups.early_lottery, ...groups.other]),
    },
  ];

  for (const sec of sections) {
    if (sectionFilter !== "all" && sectionFilter !== sec.id) continue;
    if (!sec.tokens.length) continue;
    const wrap = document.createElement("section");
    wrap.className = `token-section sec-${sec.id}`;
    wrap.innerHTML = `
      <div class="section-head">
        <h2>${sec.title} <span class="sec-count">${sec.tokens.length}</span></h2>
        <p class="sec-hint">${sec.hint}</p>
      </div>
      <div class="token-grid sec-grid"></div>`;
    const g = wrap.querySelector(".sec-grid");
    sec.tokens.forEach((t) => g.appendChild(t.safetyTier || t.column || t.preview ? renderTrenchesCard(t) : renderCard(t)));
    grid.appendChild(wrap);
  }
  statCount.textContent = hidden > 0 ? `${visible.length}/${tokens.length}` : String(visible.length);
}

function initSectionNav() {
  if (!sectionNav) return;
  sectionNav.querySelectorAll(".sec-btn").forEach((btn) => {
    btn.onclick = () => {
      sectionFilter = btn.dataset.sec || "all";
      sectionNav.querySelectorAll(".sec-btn").forEach((b) => b.classList.toggle("active", b === btn));
      renderGrid(lastTokens);
    };
  });
}

function openModal(token) {
  const m = token.market || {};
  const base = m.baseToken || {};
  const safety = token.safety || {};
  const moon = token.moonScore || {};
  const entry = token.entrySignal || {};
  const exit = token.exitSignal || {};
  const invest = token.investSignal || {};
  const trench = token.trenchAnalysis || invest.trench || {};
  const mkt = invest.market || {};
  const vol = mkt.volume || {};
  const dev = mkt.dev || {};
  const bonding = mkt.bonding || {};
  const pressure = mkt.buy_pressure || {};
  const bd = moon.breakdown || {};

  const issues = (safety.issues || []).map((i) => `<li>${i}</li>`).join("");
  const investReasons = (invest.reasons || []).map((r) => `<li>${r}</li>`).join("");
  const entryReasons = (entry.reasons || []).map((r) => `<li>${r}</li>`).join("");
  const exitReasons = (exit.reasons || []).map((r) => `<li>${r}</li>`).join("");
  const devReasons = (dev.dev_dump_reasons || []).map((r) => `<li>${r}</li>`).join("");

  const entryZone = entry.entry_zone || {};
  const targets = exit.targets || {};

  let safetyDetails = "";
  if (safety.type === "evm") {
    safetyDetails = `
      <div class="analysis-item"><div class="k">Honeypot</div><div class="v">${safety.is_honeypot ? "YES" : "No"}</div></div>
      <div class="analysis-item"><div class="k">Buy Tax</div><div class="v">${safety.buy_tax ?? 0}%</div></div>
      <div class="analysis-item"><div class="k">Sell Tax</div><div class="v">${safety.sell_tax ?? 0}%</div></div>
      <div class="analysis-item"><div class="k">Risk Level</div><div class="v">${safety.risk_level ?? "—"} (${safety.risk || "?"})</div></div>
      <div class="analysis-item"><div class="k">Open Source</div><div class="v">${safety.open_source ? "Yes" : "No"}</div></div>
      <div class="analysis-item"><div class="k">Failed Sells</div><div class="v">${safety.failed_sells ?? 0}</div></div>
    `;
  } else if (safety.type === "solana") {
    safetyDetails = `
      <div class="analysis-item"><div class="k">Rug Score</div><div class="v">${safety.rug_score}/100 (lower=safer)</div></div>
      <div class="analysis-item"><div class="k">LP Locked</div><div class="v">${safety.lp_locked_pct?.toFixed(1) ?? 0}%</div></div>
      <div class="analysis-item"><div class="k">Mint Authority</div><div class="v">${safety.mint_authority ? "ACTIVE ⚠" : "Revoked ✓"}</div></div>
      <div class="analysis-item"><div class="k">Freeze Authority</div><div class="v">${safety.freeze_authority ? "ACTIVE ⚠" : "Revoked ✓"}</div></div>
      <div class="analysis-item"><div class="k">Danger Risks</div><div class="v">${safety.danger_risks ?? 0}</div></div>
      <div class="analysis-item"><div class="k">Markets</div><div class="v">${safety.markets_count ?? 0}</div></div>
    `;
  }

  const pf = m.pumpfun || {};
  const padre = token.padre || {};
  const hub = token.checkerHub || {};
  const actionLinks = `
    <div class="action-links" style="margin-bottom:20px">
      ${padre.trade ? `<a class="action-btn padre" href="${padre.trade}" target="_blank" rel="noopener">Trade on Padre</a>` : ""}
      ${padre.trenches ? `<a class="action-btn padre" href="${padre.trenches}" target="_blank" rel="noopener">Padre Trenches</a>` : ""}
      ${pf.pump_url ? `<a class="action-btn pump" href="${pf.pump_url}" target="_blank" rel="noopener">pump.fun</a>` : ""}
    </div>`;

  $("#modalContent").innerHTML = `
    <h2>${base.name || "Token"} ($${base.symbol || "?"})</h2>
    <div class="addr-copy-row">
      <span class="addr-chain">${token.chainId}</span>
      ${copyBtnHtml(token.tokenAddress, { full: true, size: "lg" })}
    </div>
    ${actionLinks}
    ${sourceBadgesHtml(token.sources)}

    <div class="analysis-section" style="margin-bottom:16px">
      <h4>Invest Signal: ${invest.signal || "—"} (${invest.confidence ?? 0}% confidence)</h4>
      <p style="margin-bottom:12px;color:var(--muted)">${invest.action || ""}</p>
      <p style="margin-bottom:8px;font-size:0.85rem">${invest.summary || ""}</p>
      <ul class="reason-list">${investReasons}</ul>
      <div class="analysis-grid" style="margin-top:12px">
        <div class="analysis-item"><div class="k">Timing</div><div class="v">${invest.timing || "—"}</div></div>
        <div class="analysis-item"><div class="k">Exit Trigger</div><div class="v">${invest.exit_trigger ? "YES" : "No"}</div></div>
        <div class="analysis-item"><div class="k">Source Overlap</div><div class="v">${mkt.sources?.overlap_count ?? 0} feeds</div></div>
      </div>
    </div>

    <div class="analysis-section" style="margin-bottom:16px">
      <h4>Trench Gate ${trench.passed ? "✓ PASSED" : "✗ NOT READY"}</h4>
      <p style="margin-bottom:8px;color:var(--muted)">${trench.verdict || ""}</p>
      <div class="analysis-grid" style="margin-bottom:12px">
        <div class="analysis-item"><div class="k">MCap</div><div class="v">${trench.mcap_usd ? fmtUsd(trench.mcap_usd) : "—"} → $6K</div></div>
        <div class="analysis-item"><div class="k">Trench Score</div><div class="v">${trench.trench_score ?? "—"}</div></div>
        <div class="analysis-item"><div class="k">Real Dex</div><div class="v">${trench.has_real_dex ? "Yes" : "No — synthetic"}</div></div>
        <div class="analysis-item"><div class="k">Data Quality</div><div class="v">${mkt.data_quality || "—"}</div></div>
      </div>
      ${(trench.checks || []).map((c) => `
        <div class="analysis-item" style="margin-bottom:6px">
          <div class="k">${c.passed ? "✓" : "✗"} ${c.name.replace(/_/g, " ")}</div>
          <div class="v" style="font-size:0.8rem;color:var(--muted)">${c.detail}</div>
        </div>`).join("")}
    </div>

    <div class="analysis-section" style="margin-bottom:16px">
      <h4>Market Analysis (live)</h4>
      <div class="analysis-grid">
        <div class="analysis-item"><div class="k">Volume Trend</div><div class="v ${volTrendClass(vol.trend)}">${vol.trend || "—"} (${vol.velocity ?? "—"}x)</div></div>
        <div class="analysis-item"><div class="k">Volume Decay</div><div class="v">${vol.decay_pct != null ? vol.decay_pct + "%" : "—"}</div></div>
        <div class="analysis-item"><div class="k">Buy Pressure m5</div><div class="v">${pressure.ratio_m5 ?? "—"}x</div></div>
        <div class="analysis-item"><div class="k">Buy Pressure h1</div><div class="v">${pressure.ratio_h1 ?? "—"}x</div></div>
        <div class="analysis-item"><div class="k">Pressure Shift</div><div class="v">${pressure.trend || "—"}</div></div>
        <div class="analysis-item"><div class="k">Bonding Stage</div><div class="v">${bonding.stage || "—"} (${bonding.progress_pct ?? "—"}%)</div></div>
      </div>
    </div>

    <div class="analysis-section" style="margin-bottom:16px">
      <h4>Dev Behaviour</h4>
      <div class="analysis-grid">
        <div class="analysis-item"><div class="k">Dev Risk</div><div class="v ${devRiskClass(dev.risk_level)}">${dev.risk_level || "—"}</div></div>
        <div class="analysis-item"><div class="k">Dev Holds</div><div class="v">${dev.creator_pct != null ? dev.creator_pct + "%" : "—"}</div></div>
        <div class="analysis-item"><div class="k">Top 10 Holders</div><div class="v">${dev.top10_pct != null ? dev.top10_pct + "%" : "—"}</div></div>
        <div class="analysis-item"><div class="k">Insiders</div><div class="v">${dev.insider_detected ? "Detected ⚠" : "None"}</div></div>
        <div class="analysis-item"><div class="k">Dev Dumping</div><div class="v">${dev.dev_dumping ? "YES ⚠" : "No"}</div></div>
        <div class="analysis-item"><div class="k">Creator Tokens</div><div class="v">${dev.creator_token_count ?? "—"}</div></div>
      </div>
      ${devReasons ? `<ul class="reason-list issue-list" style="margin-top:12px">${devReasons}</ul>` : ""}
    </div>

    ${pf.bonding_progress != null ? `
    <div class="analysis-section" style="margin-bottom:16px">
      <h4>Live pump.fun Stats</h4>
      <div class="analysis-grid">
        <div class="analysis-item"><div class="k">Age</div><div class="v">${m.age_minutes ?? "—"} min</div></div>
        <div class="analysis-item"><div class="k">Bonding Curve</div><div class="v">${pf.bonding_progress}%</div></div>
        <div class="analysis-item"><div class="k">MCap USD</div><div class="v">${fmtUsd(pf.usd_market_cap)}</div></div>
        <div class="analysis-item"><div class="k">Replies</div><div class="v">${pf.reply_count ?? 0}</div></div>
        <div class="analysis-item"><div class="k">Graduated</div><div class="v">${pf.complete ? "Yes" : "No — still on curve"}</div></div>
      </div>
    </div>` : ""}

    <div class="analysis-section">
      <h4>Moon Score: ${moon.total} (${moon.grade})</h4>
      <div class="analysis-grid">
        <div class="analysis-item"><div class="k">Safety</div><div class="v">${bd.safety}</div></div>
        <div class="analysis-item"><div class="k">Momentum</div><div class="v">${bd.momentum}</div></div>
        <div class="analysis-item"><div class="k">Volume</div><div class="v">${bd.volume}</div></div>
        <div class="analysis-item"><div class="k">Early Factor</div><div class="v">${bd.early}</div></div>
      </div>
    </div>

    <div class="analysis-section">
      <h4>Entry Signal: ${entry.signal} (${entry.confidence}% confidence)</h4>
      <p style="margin-bottom:12px;color:var(--muted)">${entry.action || ""}</p>
      <ul class="reason-list">${entryReasons}</ul>
      ${entryZone.current ? `
        <div class="analysis-grid" style="margin-top:12px">
          <div class="analysis-item"><div class="k">Current</div><div class="v">${fmtPrice(entryZone.current)}</div></div>
          <div class="analysis-item"><div class="k">Ideal Entry</div><div class="v">${fmtPrice(entryZone.ideal)}</div></div>
          <div class="analysis-item"><div class="k">Aggressive</div><div class="v">${fmtPrice(entryZone.aggressive)}</div></div>
        </div>
      ` : ""}
    </div>

    <div class="analysis-section">
      <h4>Exit Signal: ${exit.signal} (${exit.confidence}% confidence)</h4>
      <p style="margin-bottom:12px;color:var(--muted)">${exit.action || ""}</p>
      <ul class="reason-list">${exitReasons}</ul>
      ${targets.current ? `
        <div class="analysis-grid" style="margin-top:12px">
          <div class="analysis-item"><div class="k">TP1 (1.5x)</div><div class="v">${fmtPrice(targets.take_profit_1)}</div></div>
          <div class="analysis-item"><div class="k">TP2 (2.5x)</div><div class="v">${fmtPrice(targets.take_profit_2)}</div></div>
          <div class="analysis-item"><div class="k">TP3 (5x)</div><div class="v">${fmtPrice(targets.take_profit_3)}</div></div>
          <div class="analysis-item"><div class="k">Stop Loss</div><div class="v">${fmtPrice(targets.stop_loss)}</div></div>
        </div>
      ` : ""}
    </div>

    ${alphaSetupHtml(token.alphaSetup || {})}
    ${fingerprintHtml((token.alphaSetup || {}).megaFingerprint || {})}
    ${tradePlanHtml(token.tradePlan || {})}
    ${smartMoneyPanelHtml(token.smartMoney || {})}

    <div class="analysis-section">
      <h4>Security Checkers (RugCheck, Padre, DexScreener…)</h4>
      ${checkerHubHtml(hub)}
    </div>

    <div class="analysis-section">
      <h4>Safety Analysis</h4>
      <div class="analysis-grid">${safetyDetails}</div>
      ${issues ? `<ul class="reason-list issue-list" style="margin-top:12px">${issues}</ul>` : "<p style='color:var(--accent)'>No critical issues detected.</p>"}
      ${safety.padre?.available ? `
        <div class="analysis-grid" style="margin-top:12px">
          <div class="analysis-item"><div class="k">Padre Rug Checks</div><div class="v">${safety.padre.rugcheck_checks ?? 0}</div></div>
          <div class="analysis-item"><div class="k">Padre Danger</div><div class="v">${safety.padre.danger_checks ?? 0}</div></div>
          <div class="analysis-item"><div class="k">Padre Honeypot</div><div class="v">${safety.padre.honeypot ? "YES" : "No"}</div></div>
        </div>` : ""}
    </div>

    <div class="analysis-section">
      <h4>Market Data</h4>
      <div class="analysis-grid">
        <div class="analysis-item"><div class="k">Price</div><div class="v">${fmtPrice(m.priceUsd)}</div></div>
        <div class="analysis-item"><div class="k">MCap</div><div class="v">${fmtUsd(m.marketCap || m.fdv)}</div></div>
        <div class="analysis-item"><div class="k">Liquidity</div><div class="v">${fmtUsd(m.liquidity?.usd)}</div></div>
        <div class="analysis-item"><div class="k">Vol 24h</div><div class="v">${fmtUsd(m.volume?.h24)}</div></div>
        <div class="analysis-item"><div class="k">Buys 1h</div><div class="v">${m.txns_h1?.buys ?? 0}</div></div>
        <div class="analysis-item"><div class="k">Sells 1h</div><div class="v">${m.txns_h1?.sells ?? 0}</div></div>
        <div class="analysis-item"><div class="k">5m</div><div class="v">${fmtPct(m.priceChange?.m5)}</div></div>
        <div class="analysis-item"><div class="k">24h</div><div class="v">${fmtPct(m.priceChange?.h24)}</div></div>
      </div>
      ${m.url ? `<p style="margin-top:12px"><a href="${m.url}" target="_blank" style="color:var(--accent)">View on DexScreener →</a></p>` : ""}
    </div>
  `;
  $("#modalOverlay").classList.add("open");
}

function scheduleAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  if ($("#autoRefresh").checked) {
    // 10s — climbers leave entry/mid bands fast
    refreshTimer = setInterval(() => runScan(false, true), 10000);
  }
  scheduleRunnerPoll();
}

function scheduleRunnerPoll() {
  if (runnerPollTimer) clearInterval(runnerPollTimer);
  if ($("#runnerAlerts")?.checked === false) return;
  runnerPollTimer = setInterval(() => pollRunnerRadar(false), 10000);
}

function playRunnerBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.connect(g);
    g.connect(ctx.destination);
    o.frequency.value = 880;
    g.gain.value = 0.06;
    o.start();
    setTimeout(() => {
      o.frequency.value = 1175;
    }, 90);
    setTimeout(() => {
      o.stop();
      ctx.close();
    }, 220);
  } catch {
    /* audio blocked until user gesture */
  }
}

function notifyRunner(alert) {
  const mint = alert.tokenAddress;
  if (!mint || notifiedMints.has(mint)) return;
  notifiedMints.add(mint);
  // Cap stored set
  const arr = [...notifiedMints].slice(-80);
  notifiedMints = new Set(arr);
  localStorage.setItem("moon_notified_mints", JSON.stringify(arr));
  playRunnerBeep();
  const rr = alert.runnerRadar || {};
  const title = `⚡ RUNNER: $${alert.symbol || "?"} · ${rr.stage || ""}`;
  const body = rr.summary || `${fmtUsd(alert.mcap_usd)} · score ${rr.score || "?"}`;
  if (typeof Notification !== "undefined" && Notification.permission === "granted") {
    try {
      const n = new Notification(title, { body, tag: mint });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    } catch {
      /* ignore */
    }
  }
}

function renderRunnerAlertBar(alerts) {
  const bar = $("#runnerAlertBar");
  const list = $("#runnerAlertList");
  const countEl = $("#runnerAlertCount");
  if (!bar || !list) return;
  // Never show crashed / dumped runners (e.g. CHOCI ATH→dust)
  lastRunnerAlerts = (alerts || []).filter((a) => {
    if (isHardBlocked(a)) return false;
    if ((a.runnerRadar || {}).crashed || (a.runnerRadar || {}).stage === "crashed") return false;
    return !isClientCrashedRunner(a);
  });
  if (!lastRunnerAlerts.length || $("#runnerAlerts")?.checked === false) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  if (countEl) countEl.textContent = String(lastRunnerAlerts.length);
  list.innerHTML = lastRunnerAlerts
    .slice(0, 12)
    .map((a) => {
      const rr = a.runnerRadar || {};
      const isNew = a.is_new_alert ? " is-new" : "";
      const stage = (rr.stage || "").replace(/_/g, " ");
      return `<button type="button" class="runner-chip${isNew}" data-mint="${a.tokenAddress}" title="${(rr.summary || "").replace(/"/g, "'")}">
        <span class="rc-sym">$${a.symbol || "?"}</span>
        <span class="rc-stage">${stage}</span>
        <span class="rc-meta">${fmtUsd(a.mcap_usd)} · ${Number(a.bonding_progress || rr.bonding_pct || 0).toFixed(0)}% · ${rr.score ?? "—"}</span>
      </button>`;
    })
    .join("");
  list.querySelectorAll(".runner-chip").forEach((btn) => {
    btn.onclick = () => {
      const mint = btn.dataset.mint;
      const tok =
        lastTokens.find((t) => t.tokenAddress === mint) ||
        lastRunnerAlerts.find((t) => t.tokenAddress === mint);
      if (tok) {
        if (tok.safetyTier) openTrenchesModal(tok);
        else openModal(tok);
      } else if (mint) {
        $("#lookupAddress").value = mint;
        $("#lookupChain").value = "solana";
        runLookup();
      }
    };
  });
}

async function pollRunnerRadar(silent = true) {
  if ($("#runnerAlerts")?.checked === false) return;
  try {
    const res = await fetchWithTimeout("/api/runner-radar", 12000);
    const data = await res.json();
    const alerts = data.alerts || [];
    renderRunnerAlertBar(alerts);
    for (const a of data.new_alerts || []) {
      if (a.is_new_alert) notifyRunner(a);
    }
    // Also notify high-score first-seen from full list if not yet notified
    for (const a of alerts) {
      if ((a.runnerRadar || {}).score >= 62 && a.is_new_alert) notifyRunner(a);
    }
    if (!silent && alerts.length) {
      setStatus(
        `⚡ ${alerts.length} runner alert(s) · ${(data.new_count || 0)} new · multi-stage radar live`,
        true
      );
    }
  } catch {
    /* non-fatal */
  }
}

const MAX_EARLY_MCAP = 25000;
const MAX_MIGRATION_MCAP = 78000;

function tokenLane(t) {
  const mig = t.migrationPath || {};
  if (mig.lane) return mig.lane;
  if (t.migrationLane) return t.migrationLane;
  if (t.column === "almost_bonded" || t.column === "recently_bonded") return "near_migration";
  if (t.column === "under_25k") return "under_25k";
  const m = t.mcap_usd || 0;
  const bond = Number(t.bonding_progress ?? mig.bonding_pct ?? 0);
  if (t.column === "recently_bonded" || bond >= 99) return "migrated";
  if (bond >= 45 || (m >= 30000 && m <= MAX_MIGRATION_MCAP)) return "near_migration";
  if (m >= 8000 && m <= 25000) return "under_25k";
  if (m > 0 && m < 8000) return "early_lottery";
  return "under_25k";
}

function filterDisplayMcap(tokens) {
  // Allow near-migration up to graduation (~$69k); early sections stay ≤$25k
  return tokens.filter((t) => {
    const m = t.mcap_usd ?? t.market?.pumpfun?.usd_market_cap ?? t.market?.marketCap ?? 0;
    if (!m || m <= 0) return true;
    const lane = tokenLane(t);
    if (lane === "near_migration" || lane === "migrated" || t.column === "almost_bonded" || t.column === "recently_bonded") {
      return m <= MAX_MIGRATION_MCAP * 1.5;
    }
    return m <= MAX_EARLY_MCAP;
  });
}

function deepAnalysisHtml(t, compact = false) {
  const d = t.deepAnalysis || {};
  if (!d.verdict) return "";
  const v = d.verdict;
  const cls = v === "BUY" ? "deep-buy" : v === "SKIP" ? "deep-skip" : "deep-watch";
  if (compact) {
    return `<div class="deep-row"><span class="deep-badge ${cls}">${v} ${d.confidence || 0}% · ${d.gates_passed || 0}/${d.gates_total || 0} gates</span>
      ${d.tx_interest?.total_m5 != null ? `<span class="deep-meta">${d.tx_interest.total_m5} tx · ${d.migration?.bonding_pct ?? "—"}% bond</span>` : ""}
    </div>`;
  }
  const checks = (d.checklist || []).map((c) =>
    `<li class="${c.ok ? "ok" : "bad"}">${c.ok ? "✓" : "✗"} ${c.label}${c.detail ? " — " + c.detail : ""}</li>`
  ).join("");
  const dump = d.dump || {};
  return `<div class="analysis-section deep-panel">
    <h4>Deep verdict: ${v} (${d.confidence || 0}%)</h4>
    <p style="color:var(--accent);margin-bottom:8px">${d.summary || ""}</p>
    <p style="margin-bottom:8px;font-size:0.85rem">${d.position_advice || ""}</p>
    <div class="analysis-grid">
      <div class="analysis-item"><div class="k">Dump risk</div><div class="v">${dump.is_dumped ? "DUMPED" : "ok"} ${dump.dump_pct_from_ath != null ? "−" + dump.dump_pct_from_ath + "% ATH" : ""}</div></div>
      <div class="analysis-item"><div class="k">ATH → now</div><div class="v">${dump.ath_mcap ? fmtUsd(dump.ath_mcap) : "—"} → ${fmtUsd(dump.mcap)}</div></div>
      <div class="analysis-item"><div class="k">Tx zone</div><div class="v">${d.tx_interest?.zone || "—"} · ${d.tx_interest?.total_m5 ?? "—"} tx · tilt ${d.tx_interest?.tilt || "—"}</div></div>
      <div class="analysis-item"><div class="k">Migration</div><div class="v">${d.migration?.bonding_pct ?? "—"}% · score ${d.migration?.score ?? "—"}</div></div>
    </div>
    <ul class="reason-list deep-checks">${checks}</ul>
  </div>`;
}

function txActivityBadgeHtml(t) {
  const tx = t.txActivity || t.alphaSetup?.txActivity || t.runnerRadar?.txActivity || {};
  if (!tx.total_m5 && tx.total_m5 !== 0) {
    const m5 = t.txns_m5 || {};
    const b = Number(m5.buys || 0);
    const s = Number(m5.sells || 0);
    if (b + s <= 0) return "";
    tx.total_m5 = b + s;
    tx.buys_m5 = b;
    tx.sells_m5 = s;
    tx.buy_ratio_m5 = b / Math.max(s, 1);
  }
  if (tx.total_m5 == null) return "";
  const zone = tx.zone || (tx.in_sweet_spot ? "sweet" : "");
  const tilt = tx.tilt || "";
  let cls = "tx-badge";
  if (tx.in_sweet_spot || zone === "sweet") cls += " tx-sweet";
  else if (tilt === "UP") cls += " tx-up";
  else if (tilt === "DOWN" || zone === "dead" || zone === "wash") cls += " tx-down";
  const label = tx.in_sweet_spot
    ? `📊 ${tx.total_m5} tx SWEET · ${tx.buys_m5 || "?"}B/${tx.sells_m5 || "?"}S`
    : `📊 ${tx.total_m5} tx/5m · ${Number(tx.buy_ratio_m5 || 0).toFixed(1)}x${tilt ? " · " + tilt : ""}`;
  return `<div class="tx-row"><span class="${cls}" title="${(tx.summary || tx.sweet_band?.learned || "").replace(/"/g, "'")}">${label}</span></div>`;
}

function migrationBadgeHtml(t) {
  const mig = t.migrationPath || {};
  const bond = Number(t.bonding_progress ?? mig.bonding_pct ?? 0);
  if (!bond && !mig.score) return "";
  const lane = tokenLane(t);
  const cls = lane === "near_migration" ? "mig-near" : lane === "under_25k" ? "mig-25k" : lane === "migrated" ? "mig-done" : "mig-early";
  const pinned = t._sticky_near_mig || t._pinned_stale;
  const pinSec = t._pinned_sec;
  const label = lane === "near_migration"
    ? `🚀 ${bond.toFixed(0)}% → migration${pinned ? " · pinned" : ""}`
    : lane === "migrated"
      ? "✓ Migrated"
      : lane === "under_25k"
        ? `${bond.toFixed(0)}% under $25k`
        : `${bond.toFixed(0)}% lottery`;
  return `<div class="mig-row"><span class="mig-badge ${cls}${pinned ? " pinned" : ""}">${label}${mig.score ? ` · mig ${mig.score}` : ""}</span>
    ${mig.to_graduation_usd != null && lane === "near_migration" ? `<span class="mig-meta">${fmtUsd(mig.to_graduation_usd)} to grad</span>` : ""}
    ${pinned && pinSec != null ? `<span class="mig-meta">held ${Math.max(1, Math.round(pinSec / 60))}m</span>` : ""}
  </div>`;
}

async function loadFeedPreview(limit, maxAge) {
  try {
    // Hit $6k radar first (fast, no RugCheck) so entry-zone tokens show immediately
    const [sixkRes, feedRes] = await Promise.all([
      fetchWithTimeout(`/api/padre/sixk?limit=${Math.max(24, limit * 3)}&max_age_minutes=${Math.max(40, maxAge)}`, 12000).catch(() => null),
      fetchWithTimeout(
        `/api/padre/trenches/feed?per_column=${limit}&max_age_minutes=${maxAge}`,
        20000
      ).catch(() => null),
    ]);
    let preview = [];
    let sixkN = 0;
    let sweetN = 0;
    if (sixkRes) {
      const sixk = await sixkRes.json();
      const toks = sixk.tokens || [];
      sixkN = toks.length;
      sweetN = (sixk.sweet_zone || []).length;
      preview = toks;
    }
    if (feedRes) {
      const data = await feedRes.json();
      preview = [...preview, ...flattenTrenchesData(data)];
    }
    const seen = new Set();
    preview = purgeDumpedTokens(filterDisplayMcap(preview)).filter((t) => {
      const k = mintOf(t);
      if (!k || seen.has(k) || isHardBlocked(k)) return false;
      seen.add(k);
      return true;
    });
    // Near migration / higher bonding first — not $6k dust
    preview.sort((a, b) => {
      const la = tokenLane(a);
      const lb = tokenLane(b);
      const lr = { near_migration: 0, migrated: 1, under_25k: 2, early_lottery: 3 };
      const ba = Number(a.bonding_progress || 0);
      const bb = Number(b.bonding_progress || 0);
      return (lr[la] ?? 4) - (lr[lb] ?? 4) || bb - ba || (b.mcap_usd || 0) - (a.mcap_usd || 0);
    });
    if (preview.length) {
      lastTokens = purgeDumpedTokens(preview);
      renderGrid(lastTokens);
      const nearN = preview.filter((t) => tokenLane(t) === "near_migration").length;
      setStatus(
        `Preview: ${nearN} near migration · ${sixkN} early lottery ($6k) · ${preview.length} total — safety check…`,
        true
      );
      return true;
    }
  } catch {
    /* feed is optional — full scan still runs */
  }
  return false;
}

async function runScan(force = false, silent = false) {
  if (scanInFlight) {
    if (!silent) setStatus("Scan already running — please wait…", true);
    return;
  }
  const chains = [...selectedChains].join(",");
  if (!chains) {
    setStatus("Select at least one chain");
    return;
  }
  const limit = $("#scanLimit").value;
  const maxAge = $("#maxAge").value;
  scanInFlight = true;
  if (!silent) $("#scanBtn").disabled = true;
  if (!silent || !lastTokens.length) {
    showLoadingGrid("Fetching live pump.fun trenches…");
    await loadFeedPreview(limit, maxAge);
  }
  setStatus(
    silent
      ? `Refreshing RugCheck analysis… (<${maxAge}m)`
      : `Analyzing ${limit * 3} tokens with RugCheck + Padre…`,
    true
  );

  try {
    const url = `/api/padre/trenches?per_column=${limit}&max_age_minutes=${maxAge}&force=${force}`;
    const res = await fetchWithTimeout(url);
    const data = await res.json();
    const isTrenches = Array.isArray(data.safe_picks) || data.columns;
    if (isTrenches) {
      lastScanMeta = data.counts || {};
      // Migration first, then under $25k, then early lottery
      lastTokens = filterDisplayMcap([
        ...(data.migration_picks || []),
        ...(data.under25k_picks || []),
        ...(data.early_lottery || []),
        ...(data.alpha_picks || []),
        ...(data.safe_picks || []),
        ...(data.sixk_picks || []),
        ...flattenTrenchesData(data),
      ]);
      const seen = new Set();
      lastTokens = lastTokens.filter((tok) => {
        const k = tok.tokenAddress;
        if (!k || seen.has(k)) return false;
        seen.add(k);
        return true;
      });
      // Keep near-migration on screen across brief empty / partial polls
      lastTokens = mergeStickyNearMigration(lastTokens);
      lastTokens = purgeDumpedTokens(filterDisplayMcap(lastTokens));
      lastTokens.sort((a, b) => {
        const la = tokenLane(a);
        const lb = tokenLane(b);
        const lr = { near_migration: 0, migrated: 1, under_25k: 2, early_lottery: 3 };
        const sa = a.migrationPath?.score || 0;
        const sb = b.migrationPath?.score || 0;
        const ba = Number(a.bonding_progress || 0);
        const bb = Number(b.bonding_progress || 0);
        return (lr[la] ?? 4) - (lr[lb] ?? 4) || sb - sa || bb - ba;
      });
    } else {
      lastTokens = mergeStickyNearMigration(filterDisplayMcap(data.tokens || []));
    }
    renderGrid(lastTokens);
    const visible = applyClientFilters(lastTokens).length;
    const t = new Date(data.scanned_at * 1000).toLocaleTimeString();
    const total = data.counts?.total ?? lastTokens.length;
    const migN = data.counts?.migration_picks
      ?? lastTokens.filter((x) => tokenLane(x) === "near_migration").length;
    const u25 = data.counts?.under25k_picks
      ?? lastTokens.filter((x) => tokenLane(x) === "under_25k").length;
    const lotN = data.counts?.early_lottery
      ?? lastTokens.filter((x) => tokenLane(x) === "early_lottery").length;
    const chkPass = data.counts?.checker_pass ?? lastTokens.filter((x) => (x.checkerHub || {}).consensus?.verdict === "PASS").length;
    const failNote = data.counts?.analyze_failures ? ` · ${data.counts.analyze_failures} analyze errors` : "";
    const staleNote = data.stale ? " · cached" : "";
    const runN = data.counts?.runner_alerts ?? (data.runner_alerts || []).length;
    if (data.runner_alerts?.length) {
      renderRunnerAlertBar(data.runner_alerts);
      for (const a of data.runner_alerts) {
        if (a.is_new_alert) notifyRunner(a);
      }
    } else {
      pollRunnerRadar(true);
    }
    setStatus(
      `⚡ Runners: ${runN} · Near mig: ${migN} · Under $25k: ${u25} · Lottery: ${lotN} · ${visible}/${total} · ` +
      `${chkPass} PASS${failNote} · ${t}` +
      staleNote +
      `${$("#autoRefresh").checked ? " · auto 10s" : ""}`
    );
    scheduleAutoRefresh();
  } catch (err) {
    const tip =
      getApiModeLabel() === "cloud"
        ? " Cloud may be cold/slow — switch Backend → Local and run start.bat."
        : " Is the local server running? Double-click start.bat then open http://127.0.0.1:8765";
    setStatus(`Scan failed: ${err.message}.${tip}`);
    if (!lastTokens.length) {
      setGridHtml(`<div class="empty-state"><div class="icon">◈</div><p>${err.message}</p>
        <p>${tip}</p>
        <p>Click <strong>Scan Padre Trenches</strong> to retry.</p></div>`);
    }
  } finally {
    scanInFlight = false;
    if (!silent) $("#scanBtn").disabled = false;
  }
}

async function runLookup() {
  const chain = $("#lookupChain").value.trim().toLowerCase();
  const addr = $("#lookupAddress").value.trim();
  if (!chain || !addr) {
    setStatus("Enter chain and token address");
    return;
  }
  $("#lookupBtn").disabled = true;
  setStatus(`Analyzing ${shorten(addr)} on ${chain}…`, true);

  try {
    const res = await fetch(apiUrl(`/api/analyze/${encodeURIComponent(chain)}/${encodeURIComponent(addr)}`));
    if (!res.ok) throw new Error(await res.text());
    const token = await res.json();
    lastTokens = [token];
    renderGrid(lastTokens);
    openModal(token);
    setStatus(`Analysis complete for ${token.market?.baseToken?.symbol || addr}`);
  } catch (err) {
    setStatus(`Analysis failed: ${err.message}`);
  } finally {
    $("#lookupBtn").disabled = false;
  }
}

$("#scanBtn").onclick = () => runScan(true);
$("#autoRefresh").onchange = scheduleAutoRefresh;
$("#maxAge").onchange = () => runScan(true);
$("#checkerPassOnly").onchange = () => renderGrid(lastTokens);
$("#safeOnly").onchange = () => renderGrid(lastTokens);
$("#lookupBtn").onclick = runLookup;
$("#modalClose").onclick = () => $("#modalOverlay").classList.remove("open");
$("#modalOverlay").onclick = (e) => {
  if (e.target === $("#modalOverlay")) $("#modalOverlay").classList.remove("open");
};

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  e.stopPropagation();
  e.preventDefault();
  copyText(btn.dataset.copy, btn);
});

initChains();
initBackendSync();
initSectionNav();
$("#enableNotifBtn")?.addEventListener("click", async () => {
  if (typeof Notification === "undefined") {
    setStatus("Notifications not supported in this browser");
    return;
  }
  const perm = await Notification.requestPermission();
  setStatus(
    perm === "granted"
      ? "🔔 Runner notifications ON — keep this tab open"
      : "Notifications blocked — enable in browser settings"
  );
  if (perm === "granted") playRunnerBeep();
});
$("#runnerAlerts")?.addEventListener("change", () => {
  if ($("#runnerAlerts").checked) {
    pollRunnerRadar(false);
    scheduleRunnerPoll();
  } else {
    renderRunnerAlertBar([]);
  }
});
// Prefer local on first visit from localhost
if (!IS_CLOUD_HOST && !localStorage.getItem("moon_api_mode")) {
  localStorage.setItem("moon_api_mode", "local");
}
if ($("#apiBackend") && !IS_CLOUD_HOST) {
  $("#apiBackend").value = getApiModeLabel();
}
updateBackendPill();
showLoadingGrid(
  getApiModeLabel() === "cloud" && !IS_CLOUD_HOST
    ? "Loading from cloud (same as Render)…"
    : "Starting local scan…"
);
fetchWithTimeout("/api/health", 8000).then(async (res) => {
  try {
    const h = await res.json();
    const mode = getApiModeLabel();
    const backend = h.deploy || mode;
    setStatus(
      `Backend: ${backend}${h.learning ? ` · learned ${h.learning.finalized || 0} tokens` : ""} · ready`,
      true
    );
  } catch { /* ignore */ }
  runScan(false);
}).catch(() => {
  if (!IS_CLOUD_HOST && getApiModeLabel() === "local") {
    setStatus(
      "Local server not running — start start.bat, then refresh. Or switch Backend → Cloud.",
      false
    );
    setGridHtml(`<div class="empty-state"><div class="icon">◈</div>
      <p><strong>Local server is offline</strong></p>
      <p>1. Double-click <code>C:\\Users\\MMghongo\\moon-scanner\\start.bat</code></p>
      <p>2. Open <a href="http://127.0.0.1:8765">http://127.0.0.1:8765</a></p>
      <p>3. Or set Backend → Cloud (synced) if Render is up</p></div>`);
    return;
  }
  if (!IS_CLOUD_HOST && getApiModeLabel() === "cloud") {
    setStatus("Cloud unreachable — falling back to local…", true);
    localStorage.setItem("moon_api_mode", "local");
    const sel = $("#apiBackend");    if (sel) sel.value = "local";
    updateBackendPill();
  } else {
    setStatus("Server not reachable — starting scan anyway…", true);
  }
  runScan(false);
});