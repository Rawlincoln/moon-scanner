/**
 * Graduated / large runners UI
 */
const $ = (s) => document.querySelector(s);

function apiUrl(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  return p;
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

function cardHtml(t) {
  const grad = t.grad || {};
  const label = t.grad_label || grad.label || "WATCH";
  const score = t.grad_score ?? grad.grad_score ?? "—";
  const conf = t.confidence ?? grad.confidence ?? score;
  const why = grad.why || [];
  const plan = grad.plan || {};
  const mcap = t.mcap_usd || 0;
  const ath = t.ath_mcap || 0;
  const athPct = t.ath_retention_pct ?? grad.ath_retention_pct;
  const ageMin = Number(t.age_minutes || 0);
  const age =
    ageMin >= 1440
      ? `${(ageMin / 1440).toFixed(1)}d`
      : ageMin >= 60
        ? `${(ageMin / 60).toFixed(1)}h`
        : `${ageMin.toFixed(0)}m`;
  const mint = t.tokenAddress || "";
  const shortMint = mint ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : "";
  const risk = grad.risk_level || t.risk_level || "high";
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
  const h1 = Number(pc.h1);
  const m5Html = Number.isFinite(m5)
    ? `<span class="${m5 >= 0 ? "up" : "down"}">m5 ${m5 >= 0 ? "+" : ""}${m5.toFixed(1)}%</span>`
    : "";
  const h1Html = Number.isFinite(h1)
    ? `<span class="${h1 >= 0 ? "up" : "down"}">h1 ${h1 >= 0 ? "+" : ""}${h1.toFixed(1)}%</span>`
    : "";
  const gradTag = grad.graduated || t.complete
    ? `<span class="risk-tag" style="color:#c4b5fd">graduated</span>`
    : "";
  const planHtml = plan.size_advice
    ? `<div class="plan-box">${escapeHtml(plan.size_advice)}</div>`
    : "";

  return `
    <article class="card ${escapeHtml(label.toLowerCase())}" data-mint="${escapeHtml(mint)}">
      ${icon}
      <div class="body">
        <div class="head">
          <span class="sym">$${escapeHtml(t.symbol || "?")}</span>
          <span class="name">${escapeHtml(t.name || "")}</span>
          <span class="badge ${escapeHtml(label)}">${escapeHtml(label)}</span>
          <span class="risk-tag">${escapeHtml(risk)}</span>
          ${gradTag}
        </div>
        <div class="meta">
          <span>${fmtUsd(mcap)} mcap</span>
          ${ath ? `<span>ATH ${fmtUsd(ath)}${athPct != null ? ` · ${athPct}%` : ""}</span>` : ""}
          <span>${age}</span>
          ${m5Html}
          ${h1Html}
          <span>score ${score} · conf ${conf}</span>
        </div>
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
    list.innerHTML = `<div class="empty">
      <strong>No graduated runners right now</strong>
      <p>Looking for ≥~$80k post-migration / large structure. Empty is normal on quiet days.</p>
      <p class="muted">Scanned ${counts.candidates_raw ?? "—"} · band ${counts.band_hits ?? "—"} · rejected ${counts.rejected ?? "—"}</p>
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
  const r = counts.runner ?? tokens.filter((t) => t.grad_label === "RUNNER").length;
  const d = counts.dip ?? tokens.filter((t) => t.grad_label === "DIP").length;
  const w = counts.watch ?? tokens.filter((t) => t.grad_label === "WATCH").length;
  if ($("#statShown")) $("#statShown").textContent = `${shown} shown`;
  if ($("#statGrad"))
    $("#statGrad").textContent = `${r} runner · ${d} dip · ${w} watch`;
}

let scanning = false;
let timer = null;

async function scan(force = false) {
  if (scanning) return;
  scanning = true;
  const btn = $("#scanBtn");
  if (btn) btn.disabled = true;
  const limit = $("#limit")?.value || 16;
  const maxAge = $("#maxAge")?.value || 10080;
  setStatus("Scanning graduated / large runners…", "busy");
  try {
    const url = apiUrl(
      `/api/graduated?limit=${limit}&max_age_minutes=${maxAge}&force=${force ? "true" : "false"}`
    );
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 60000);
    const res = await fetch(url, { signal: ctrl.signal });
    clearTimeout(to);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const tokens = data.tokens || [];
    render(tokens, data.counts || {}, data.near_misses || [], {
      reject_breakdown: data.reject_breakdown,
    });
    try {
      if (window.MoonAlerts) MoonAlerts.alertNewPicks("grad", tokens);
    } catch {
      /* optional */
    }
    const c = data.counts || {};
    setStatus(
      `${c.shown ?? tokens.length} shown · band ${c.band_hits ?? "—"} · ` +
        `${c.rejected ?? 0} rejected` +
        `${data.cached ? " · cached" : ""}`
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
    timer = setInterval(() => scan(false), 20000);
  }
}

function bind() {
  $("#scanBtn")?.addEventListener("click", () => scan(true));
  $("#autoRefresh")?.addEventListener("change", schedule);
  if (window.MoonAlerts) {
    MoonAlerts.wireToggle($("#alertToggle"), $("#alertStatus"));
  }
  schedule();
  scan(false);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bind);
} else {
  bind();
}
