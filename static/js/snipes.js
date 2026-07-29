/**
 * Safe Snipes UI — 2× take-profit capital-protection feed.
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
  const snipe = t.snipe || {};
  const label = t.snipe_label || snipe.label || "SETUP";
  const score = t.snipe_score ?? snipe.snipe_score ?? "—";
  const conf = t.confidence ?? snipe.confidence ?? score;
  const why = snipe.why || [];
  const plan = snipe.plan || {};
  const mcap = t.mcap_usd || 0;
  const ath = t.ath_mcap || 0;
  const athPct = t.ath_retention_pct ?? snipe.ath_retention_pct;
  const bond = Number(t.bonding_progress || 0);
  const age = t.age_minutes != null ? `${Number(t.age_minutes).toFixed(0)}m` : "—";
  const mint = t.tokenAddress || "";
  const shortMint = mint ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : "";
  const target = t.target_2x_usd ?? snipe.target_2x_usd;
  const inv = snipe.invalidation_usd ?? plan.invalidation_usd;
  const icon = t.icon
    ? `<img class="icon" src="${escapeHtml(safeHttpUrl(t.icon, ""))}" alt="" onerror="this.outerHTML='<div class=\\'icon ph\\'>◎</div>'" />`
    : `<div class="icon ph">◎</div>`;
  const whyHtml = why.length
    ? `<ul class="why">${why.map((w) => `<li>${escapeHtml(String(w))}</li>`).join("")}</ul>`
    : "";
  const bs = t.bundleSniper || {};
  const sn = t.snipers || bs.snipers || {};
  const bun = t.bundle || bs.bundle || {};
  const bunPct =
    bun.bundled_pct != null
      ? Number(bun.bundled_pct)
      : bs.bundled_pct != null
        ? Number(bs.bundled_pct)
        : null;
  const snLv = sn.risk_level || snipe.sniper_level || "—";
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
  const planHtml = plan.size_advice
    ? `<div class="plan-box">${escapeHtml(plan.size_advice)} ${
        plan.rule ? `· ${escapeHtml(plan.rule)}` : ""
      }</div>`
    : "";

  return `
    <article class="card ${escapeHtml(label.toLowerCase())}" data-mint="${escapeHtml(mint)}">
      ${icon}
      <div class="body">
        <div class="head">
          <span class="sym">$${escapeHtml(t.symbol || "?")}</span>
          <span class="name">${escapeHtml(t.name || "")}</span>
          <span class="badge ${escapeHtml(label)}">${escapeHtml(label)}</span>
          ${t.realtime ? `<span class="badge stage">live</span>` : ""}
        </div>
        <div class="tp-row">
          <span>entry <strong>${fmtUsd(mcap)}</strong></span>
          <span class="tp">2× TP <strong>${target != null ? fmtUsd(target) : "—"}</strong></span>
          <span class="inv">cut &lt; <strong>${inv != null ? fmtUsd(inv) : "—"}</strong></span>
        </div>
        <div class="meta">
          ${ath ? `<span>ATH <strong>${fmtUsd(ath)}</strong>${athPct != null ? ` <em>${athPct}%</em>` : ""}</span>` : ""}
          <span>bond <strong>${bond.toFixed(0)}%</strong></span>
          <span>age <strong>${age}</strong></span>
          ${m5Html}
        </div>
        <div class="meta sniper-row">
          <span>bundled <strong>${bunPct != null ? `${bunPct.toFixed(0)}%` : "—"}</strong></span>
          <span>snipers <strong class="rl-${escapeHtml(String(snLv))}">${escapeHtml(String(snLv))}</strong>
            ${sn.max_wallet_pct != null ? `· max ${Number(sn.max_wallet_pct).toFixed(1)}%` : ""}</span>
        </div>
        ${whyHtml}
        ${planHtml}
        <div class="mint-row">
          <code title="${escapeHtml(mint)}">${escapeHtml(shortMint)}</code>
          <button type="button" class="btn tiny copy-mint" data-mint="${escapeHtml(mint)}">Copy</button>
        </div>
      </div>
      <div class="side">
        <div class="score-block">
          <div class="score">${score}<span>snipe</span></div>
          <div class="conf">
            <div class="conf-track"><i style="width:${Math.min(100, conf)}%"></i></div>
            <span>${conf}% conf</span>
          </div>
        </div>
        <div class="links">
          <a class="btn ghost" href="${escapeHtml(pump)}" target="_blank" rel="noopener">Pump</a>
          <a class="btn ghost" href="${escapeHtml(padre)}" target="_blank" rel="noopener">Trade</a>
          ${dex ? `<a class="btn ghost" href="${escapeHtml(dex)}" target="_blank" rel="noopener">Dex</a>` : ""}
        </div>
      </div>
    </article>
  `;
}

function nearMissHtml(misses = []) {
  if (!misses.length) return "";
  const rows = misses
    .slice(0, 6)
    .map((m) => {
      const mcap = m.mcap_usd != null ? fmtUsd(m.mcap_usd) : "—";
      const age =
        m.age_minutes != null ? `${Number(m.age_minutes).toFixed(0)}m` : "—";
      return `<li><strong>${escapeHtml(m.symbol || "?")}</strong>
        <span class="muted">${mcap} · ${age}</span>
        — ${escapeHtml(m.reject || m.reject_key || "filtered")}</li>`;
    })
    .join("");
  return `<div class="near-miss">
    <strong>Checked in band but filtered</strong>
    <ul>${rows}</ul>
  </div>`;
}

function render(tokens, counts = {}, nearMisses = []) {
  const list = $("#list");
  if (!list) return;
  if (!tokens.length) {
    const band = counts.band_hits != null ? counts.band_hits : "—";
    list.innerHTML = `<div class="empty">
      <strong>No safe 2× snipes right now</strong>
      <p>We only show $3.5k–$16k climbers. SNIPE needs bundled ≤5% + clean snipers; SETUP allows up to ~8% (small size). Near-ATH, room to 2×. Empty is normal — most charts are sniper traps or dumps.</p>
      <p class="muted">Scanned ${counts.candidates_raw ?? "—"} · band hits ${band} · rejected ${counts.rejected ?? "—"}</p>
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
  const sn = counts.snipe ?? tokens.filter((t) => t.snipe_label === "SNIPE").length;
  const su = counts.setup ?? tokens.filter((t) => t.snipe_label === "SETUP").length;
  $("#statShown").textContent = `${shown} shown`;
  $("#statSnipe").textContent = `${sn} snipe · ${su} setup`;
}

let scanning = false;
let timer = null;

async function scan(force = false) {
  if (scanning) return;
  scanning = true;
  const btn = $("#scanBtn");
  if (btn) btn.disabled = true;
  const limit = $("#limit")?.value || 12;
  const maxAge = $("#maxAge")?.value || 60;
  setStatus("Scanning safe 2× snipe band…", "busy");

  try {
    const url = apiUrl(
      `/api/snipes?limit=${limit}&max_age_minutes=${maxAge}&force=${force ? "true" : "false"}`
    );
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 45000);
    const res = await fetch(url, { signal: ctrl.signal });
    clearTimeout(to);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const tokens = data.tokens || [];
    render(tokens, data.counts || {}, data.near_misses || []);
    const c = data.counts || {};
    const t = data.scanned_at
      ? new Date(data.scanned_at * 1000).toLocaleTimeString()
      : "";
    setStatus(
      `${c.shown ?? tokens.length} setups · band ${c.band_hits ?? "—"} · ` +
        `${c.rejected ?? 0} rejected · ${c.enriched ?? "—"} checked` +
        `${data.cached ? " · cached" : ""} · ${data.mode || "snipes"} · ${t}`
    );
    if ($("#rule") && data.rule) $("#rule").textContent = data.rule;
  } catch (e) {
    const msg = e?.name === "AbortError" ? "Timed out" : e?.message || String(e);
    setStatus(`Failed: ${msg}`, "err");
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
  const sel = $("#apiBackend");
  if (sel && !IS_LOCAL_PAGE && !IS_CLOUD) {
    sel.value = localStorage.getItem("moon_api_mode") || "local";
  }
  updateBackendPill();
  scan(true);
  schedule();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bind);
} else {
  bind();
}
