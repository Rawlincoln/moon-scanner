/**
 * Organic Heat UI — high-recall companion to strict Moons.
 */
const CLOUD_API = "https://moon-scanner-9tlz.onrender.com";
const IS_CLOUD = /onrender\.com$/i.test(location.hostname);
const IS_LOCAL_PAGE = /^(localhost|127\.0\.0\.1)$/i.test(location.hostname || "");
const $ = (s) => document.querySelector(s);

function apiBase() {
  if (IS_CLOUD) return "";
  if (IS_LOCAL_PAGE) return "";
  const mode = localStorage.getItem("moon_api_mode") || "local";
  return mode === "cloud" ? CLOUD_API : "";
}

function apiUrl(path) {
  const base = apiBase().replace(/\/$/, "");
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}

function fmtUsd(n) {
  const v = Number(n) || 0;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function safeHttpUrl(u, fallback = "#") {
  if (u == null || u === "") return fallback;
  try {
    const x = new URL(String(u), location.origin);
    if (x.protocol === "http:" || x.protocol === "https:") return x.href;
  } catch {
    /* ignore */
  }
  return fallback;
}

function setStatus(msg, kind = "") {
  const el = $("#status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status" + (kind ? ` ${kind}` : "");
}

function updateBackendPill() {
  const el = $("#statBackend");
  if (!el) return;
  el.textContent = IS_CLOUD ? "cloud" : localStorage.getItem("moon_api_mode") || "local";
}

function cardHtml(t) {
  const heat = t.heat || {};
  const label = t.heat_label || heat.label || "RISKY";
  const score = t.heat_score ?? heat.heat_score ?? "—";
  const conf = t.confidence ?? heat.confidence ?? score;
  const why = heat.why || [];
  const plan = heat.plan || {};
  const mcap = t.mcap_usd || 0;
  const ath = t.ath_mcap || 0;
  const athPct = t.ath_retention_pct ?? heat.ath_retention_pct;
  const bond = Number(t.bonding_progress || 0);
  const age = t.age_minutes != null ? `${Number(t.age_minutes).toFixed(0)}m` : "—";
  const mint = t.tokenAddress || "";
  const shortMint = mint ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : "";
  const risk = heat.risk_level || t.risk_level || "high";
  const replies = heat.replies ?? heat.meta?.replies;
  const dev = heat.dev || t.dev || {};
  const launched = dev.tokens_launched;
  const migrated = dev.tokens_migrated;
  const devSold = dev.creator_sold;
  const thisStatus = dev.this_status || "";
  const icon = t.icon
    ? `<img class="icon" src="${escapeHtml(safeHttpUrl(t.icon, ""))}" alt="" onerror="this.outerHTML='<div class=\\'icon ph\\'>◎</div>'" />`
    : `<div class="icon ph">◎</div>`;
  const whyHtml = why.length
    ? `<ul class="why">${why.map((w) => `<li>${escapeHtml(String(w))}</li>`).join("")}</ul>`
    : "";
  const pump = safeHttpUrl(
    t.pump_url || (mint ? `https://pump.fun/coin/${mint}` : ""),
    "#"
  );
  const padre = safeHttpUrl(
    t.padre_url || (mint ? `https://trade.padre.gg/trade/solana/${mint}` : ""),
    "#"
  );
  const dex = t.dex_url ? safeHttpUrl(t.dex_url) : "";
  const pc = t.priceChange || t.market?.priceChange || {};
  const m5 = Number(pc.m5);
  const m5Html = Number.isFinite(m5)
    ? `<span class="${m5 >= 0 ? "up" : "down"}">m5 ${m5 >= 0 ? "+" : ""}${m5.toFixed(1)}%</span>`
    : "";
  const tp2x = heat.target_2x_usd ?? plan.take_profit_2x_usd ?? t.target_2x_usd;
  const zone = heat.target_zone_usd || plan.target_zone_usd || [12000, 21000];
  const tpHtml =
    tp2x != null
      ? `<span class="up">2× ${fmtUsd(tp2x)}</span>`
      : "";
  const zoneHtml = zone
    ? `<span>target ${fmtUsd(zone[0])}–${fmtUsd(zone[1])}</span>`
    : "";
  const planHtml = plan.size_advice
    ? `<div class="plan-box">${escapeHtml(plan.size_advice)}</div>`
    : "";
  const enrichWarn =
    t.enrich_ok !== true
      ? `<span class="risk-tag">partial safety</span>`
      : "";
  const holdersWarn =
    heat.holders_known === false
      ? `<span class="risk-tag">holders unknown</span>`
      : "";
  const devSoldTag = devSold
    ? `<span class="risk-tag">dev sold</span>`
    : "";
  const serialTag =
    launched != null && launched >= 6 && (migrated == null || migrated === 0)
      ? `<span class="risk-tag">serial farm?</span>`
      : "";
  const devLine = [
    launched != null ? `dev tokens: ${launched}` : "dev tokens: ?",
    migrated != null ? `migrated: ${migrated}` : null,
    thisStatus ? `now: ${thisStatus.replace(/_/g, " ")}` : null,
    devSold ? "dev SOLD" : dev.creator_pct != null && dev.creator_pct > 0
      ? `dev holds ${Number(dev.creator_pct).toFixed(1)}%`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return `
    <article class="card ${escapeHtml(label.toLowerCase())}" data-mint="${escapeHtml(mint)}">
      ${icon}
      <div class="body">
        <div class="head">
          <span class="sym">$${escapeHtml(t.symbol || "?")}</span>
          <span class="name">${escapeHtml(t.name || "")}</span>
          <span class="badge ${escapeHtml(label)}">${escapeHtml(label)}</span>
          <span class="risk-tag">${escapeHtml(risk)}</span>
          ${enrichWarn}
          ${holdersWarn}
          ${devSoldTag}
          ${serialTag}
        </div>
        <div class="meta">
          <span>${fmtUsd(mcap)} mcap</span>
          ${ath ? `<span>ATH ${fmtUsd(ath)}${athPct != null ? ` · ${athPct}%` : ""}</span>` : ""}
          <span>bond ${bond.toFixed(0)}%</span>
          <span>${age}</span>
          ${replies != null ? `<span>${replies} replies</span>` : ""}
          ${m5Html}
          ${tpHtml}
          ${zoneHtml}
          <span>score ${score} · conf ${conf}</span>
        </div>
        <div class="meta dev-line"><strong>Dev:</strong> ${escapeHtml(devLine)}</div>
        ${whyHtml}
        ${planHtml}
        <div class="actions">
          <a class="btn sm" href="${padre}" target="_blank" rel="noopener">Padre</a>
          <a class="btn sm" href="${pump}" target="_blank" rel="noopener">Pump</a>
          ${dex ? `<a class="btn sm" href="${dex}" target="_blank" rel="noopener">Dex</a>` : ""}
          <button type="button" class="btn sm copy-mint" data-mint="${escapeHtml(mint)}">Copy</button>
          <span class="mint muted">${escapeHtml(shortMint)}</span>
        </div>
      </div>
    </article>`;
}

function nearMissHtml(misses = []) {
  if (!misses.length) return "";
  const rows = misses
    .slice(0, 8)
    .map(
      (m) =>
        `<li><strong>$${escapeHtml(m.symbol || "?")}</strong> — ${escapeHtml(
          m.reject || "?"
        )}</li>`
    )
    .join("");
  return `<div class="near-miss"><strong>Near misses</strong><ul>${rows}</ul></div>`;
}

function rejectBreakdownHtml(rb = {}) {
  const entries = Object.entries(rb || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);
  if (!entries.length) return "";
  return `<p class="muted">Why empty: ${entries
    .map(([k, v]) => `${escapeHtml(k)}:${v}`)
    .join(" · ")}</p>`;
}

function render(tokens, counts = {}, nearMisses = [], extra = {}) {
  const list = $("#list");
  if (!list) return;
  if (!tokens.length) {
    const band = counts.band_hits != null ? counts.band_hits : "—";
    list.innerHTML = `<div class="empty">
      <strong>No organic heat right now</strong>
      <p>Even high-recall mode needs replies/volume structure and not a hard dump. Empty is rare — check server / pump.fun reachability.</p>
      <p class="muted">Scanned ${counts.candidates_raw ?? "—"} · band hits ${band} · rejected ${counts.rejected ?? "—"}</p>
      ${rejectBreakdownHtml(extra.reject_breakdown)}
      ${nearMissHtml(nearMisses)}
    </div>`;
  } else {
    list.innerHTML = tokens.map(cardHtml).join("");
    list.querySelectorAll(".copy-mint").forEach((btn) => {
      btn.onclick = async () => {
        const m = btn.dataset.mint;
        if (!m) return;
        try {
          await navigator.clipboard.writeText(m);
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 1200);
        } catch {
          btn.textContent = "Fail";
        }
      };
    });
  }
  const shown = counts.shown ?? tokens.length;
  const h = counts.heat ?? tokens.filter((t) => t.heat_label === "HEAT").length;
  const w = counts.warm ?? tokens.filter((t) => t.heat_label === "WARM").length;
  const r = counts.risky ?? tokens.filter((t) => t.heat_label === "RISKY").length;
  if ($("#statShown")) $("#statShown").textContent = `${shown} shown`;
  if ($("#statHeat")) $("#statHeat").textContent = `${h} heat · ${w} warm · ${r} risky`;
}

let scanning = false;
let timer = null;

async function scan(force = false) {
  if (scanning) return;
  scanning = true;
  const btn = $("#scanBtn");
  if (btn) btn.disabled = true;
  const limit = $("#limit")?.value || 16;
  const maxAge = $("#maxAge")?.value || 120;
  setStatus("Scanning organic heat (high recall)…", "busy");

  try {
    const url = apiUrl(
      `/api/heat?limit=${limit}&max_age_minutes=${maxAge}&force=${force ? "true" : "false"}`
    );
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 50000);
    const res = await fetch(url, { signal: ctrl.signal });
    clearTimeout(to);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const tokens = data.tokens || [];
    render(tokens, data.counts || {}, data.near_misses || [], {
      reject_breakdown: data.reject_breakdown,
    });
    try {
      if (window.MoonAlerts) {
        MoonAlerts.alertNewPicks("heat", tokens);
      }
    } catch {
      /* optional */
    }
    const c = data.counts || {};
    const t = data.scanned_at
      ? new Date(data.scanned_at * 1000).toLocaleTimeString()
      : "";
    setStatus(
      `${c.shown ?? tokens.length} heat · band ${c.band_hits ?? "—"} · ` +
        `${c.rejected ?? 0} rejected · ${c.enriched ?? "—"} checked` +
        `${data.cached ? " · cached" : ""} · ${data.mode || "heat"} · ${t}`
    );
    if ($("#rule") && data.rule) $("#rule").textContent = data.rule;
  } catch (e) {
    const msg = e?.name === "AbortError" ? "Timed out" : e?.message || String(e);
    let hint = "";
    if (/Failed to fetch|NetworkError|Load failed/i.test(msg)) {
      hint =
        " — Server is OFF. Run start.bat and open http://127.0.0.1:8765/heat";
      setTimeout(() => {
        if (!scanning) scan(false);
      }, 5000);
    }
    setStatus(`Failed: ${msg}${hint}`, "err");
  } finally {
    scanning = false;
    if (btn) btn.disabled = false;
  }
}

function schedule() {
  if (timer) clearInterval(timer);
  timer = null;
  if ($("#autoRefresh")?.checked) {
    timer = setInterval(() => scan(false), 12000);
  }
}

function bind() {
  $("#scanBtn")?.addEventListener("click", () => scan(true));
  $("#autoRefresh")?.addEventListener("change", schedule);
  $("#apiBackend")?.addEventListener("change", (e) => {
    localStorage.setItem("moon_api_mode", e.target.value);
    updateBackendPill();
    scan(true);
  });
  if (window.MoonAlerts) {
    MoonAlerts.wireToggle($("#alertToggle"), $("#alertStatus"));
  }
  updateBackendPill();
  const sel = $("#apiBackend");
  if (sel && !IS_CLOUD) {
    sel.value = localStorage.getItem("moon_api_mode") || "local";
  }
  schedule();
  scan(false);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bind);
} else {
  bind();
}
