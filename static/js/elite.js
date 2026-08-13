/**
 * Elite Copy UI — top-20 smart wallet buy signals + safety.
 */
const CLOUD_API = "https://moon-scanner-9tlz.onrender.com";
const IS_CLOUD = /onrender\.com$/i.test(location.hostname);
const IS_LOCAL_PAGE = /^(localhost|127\.0\.0\.1)$/i.test(location.hostname || "");
const $ = (s) => document.querySelector(s);

function apiBase() {
  if (IS_CLOUD) return "";
  if (IS_LOCAL_PAGE) return "";
  return "";
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

function renderRoster(traders = []) {
  const el = $("#roster");
  if (!el) return;
  if (!traders.length) {
    el.innerHTML = `<div class="roster-card">No elite roster loaded</div>`;
    return;
  }
  el.innerHTML = traders
    .map((t) => {
      const addr = t.address || "";
      const short = addr ? `${addr.slice(0, 4)}…${addr.slice(-4)}` : "—";
      const sol = safeHttpUrl(
        t.solscan || (addr ? `https://solscan.io/account/${addr}` : "#")
      );
      return `<div class="roster-card">
        <span class="tier ${escapeHtml(t.tier || "B")}">${escapeHtml(t.tier || "B")}</span>
        <span class="lbl">${escapeHtml(t.label || "?")}</span>
        <div class="addr"><a href="${sol}" target="_blank" rel="noopener">${escapeHtml(short)}</a></div>
        <div class="meta-line">${escapeHtml(t.style || "")}${
          t.score != null ? ` · score ${Number(t.score).toFixed(0)}` : ""
        }${t.wins != null ? ` · wins ${t.wins}` : ""} · ${escapeHtml(t.source || "seed")}</div>
      </div>`;
    })
    .join("");
}

function cardHtml(t) {
  const elite = t.elite || {};
  const label = t.elite_label || elite.label || "WATCH";
  const score = t.elite_score ?? elite.elite_score ?? "—";
  const conf = t.confidence ?? elite.confidence ?? score;
  const why = elite.why || [];
  const plan = elite.plan || {};
  const hits = t.elite_hits || elite.elite_hits || [];
  const mcap = t.mcap_usd || 0;
  const ath = t.ath_mcap || 0;
  const athPct = t.ath_retention_pct ?? elite.ath_retention_pct;
  const bond = Number(t.bonding_progress || 0);
  const age = t.age_minutes != null ? `${Number(t.age_minutes).toFixed(0)}m` : "—";
  const mint = t.tokenAddress || "";
  const shortMint = mint ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : "";
  const risk = elite.risk_level || t.risk_level || "high";
  const icon = t.icon
    ? `<img class="icon" src="${escapeHtml(safeHttpUrl(t.icon, ""))}" alt="" onerror="this.outerHTML='<div class=\\'icon ph\\'>◎</div>'" />`
    : `<div class="icon ph">◎</div>`;
  const whyHtml = why.length
    ? `<ul class="why">${why.map((w) => `<li>${escapeHtml(String(w))}</li>`).join("")}</ul>`
    : "";
  const hitsHtml = hits.length
    ? `<div class="elite-hits">👑 ${hits
        .map(
          (h) =>
            `${escapeHtml(h.label || "?")}${h.pct != null ? ` ${Number(h.pct).toFixed(1)}%` : ""}`
        )
        .join(" · ")}</div>`
    : "";
  const pump = safeHttpUrl(
    t.pump_url || (mint ? `https://pump.fun/coin/${mint}` : ""),
    "#"
  );
  const padre = safeHttpUrl(
    t.padre_url || (mint ? `https://trade.padre.gg/trade/solana/${mint}` : ""),
    "#"
  );
  const planHtml = plan.size_advice
    ? `<div class="plan-box">${escapeHtml(plan.size_advice)}</div>`
    : "";
  const tp2x = elite.target_2x_usd ?? plan.take_profit_2x_usd;

  return `
    <article class="card ${escapeHtml(label.toLowerCase())}" data-mint="${escapeHtml(mint)}">
      ${icon}
      <div class="body">
        <div class="head">
          <span class="sym">$${escapeHtml(t.symbol || "?")}</span>
          <span class="name">${escapeHtml(t.name || "")}</span>
          <span class="badge ${escapeHtml(label)}">${escapeHtml(label)}</span>
          <span class="risk-tag">${escapeHtml(risk)}</span>
        </div>
        <div class="meta">
          <span>${fmtUsd(mcap)} mcap</span>
          ${ath ? `<span>ATH ${fmtUsd(ath)}${athPct != null ? ` · ${athPct}%` : ""}</span>` : ""}
          <span>bond ${bond.toFixed(0)}%</span>
          <span>${age}</span>
          ${tp2x != null ? `<span class="up">2× ${fmtUsd(tp2x)}</span>` : ""}
          <span>score ${score} · conf ${conf}</span>
        </div>
        ${hitsHtml}
        ${whyHtml}
        ${planHtml}
        <div class="actions">
          <a class="btn sm" href="${padre}" target="_blank" rel="noopener">Padre</a>
          <a class="btn sm" href="${pump}" target="_blank" rel="noopener">Pump</a>
          <button type="button" class="btn sm copy-mint" data-mint="${escapeHtml(mint)}">Copy</button>
          <span class="mint muted">${escapeHtml(shortMint)}</span>
        </div>
      </div>
    </article>`;
}

function render(tokens, counts = {}, traders = []) {
  const list = $("#list");
  if (!list) return;
  renderRoster(traders);
  if (!tokens.length) {
    list.innerHTML = `<div class="empty">
      <strong>No elite buy signals right now</strong>
      <p>Need: elite wallet on the book + $7k+ + full safety. Seed wallets may still be placeholders — paste real KOL addresses into data/elite_traders.json. Learned wallets fill from HEAT/MOON quality holders.</p>
      <p class="muted">Scanned ${counts.candidates_raw ?? "—"} · enriched band ${counts.band_hits ?? "—"} · roster ${counts.roster ?? "—"}</p>
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
  const shown = $("#statShown");
  if (shown) shown.textContent = `${tokens.length} signals`;
  const se = $("#statElite");
  if (se) se.textContent = `${counts.elite ?? 0} elite · ${counts.copy ?? 0} copy`;
}

async function scan() {
  const limit = $("#limit")?.value || 12;
  const maxAge = $("#maxAge")?.value || 120;
  setStatus("Scanning elite wallets…");
  try {
    const res = await fetch(
      apiUrl(`/api/elite?limit=${limit}&max_age_minutes=${maxAge}&force=1`)
    );
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "scan failed");
    render(data.tokens || [], data.counts || {}, data.traders || []);
    const rule = $("#rule");
    if (rule) rule.textContent = data.rule || "";
    setStatus(
      `Updated · ${data.counts?.shown ?? 0} signals · roster ${data.counts?.roster ?? 0}`,
      "ok"
    );
    if (window.MoonAlerts && $("#alertToggle")?.checked) {
      for (const t of data.tokens || []) {
        const lab = t.elite_label || "";
        if (lab === "ELITE" || lab === "COPY") {
          window.MoonAlerts.notify?.(
            `${lab} $${t.symbol || "?"}`,
            (t.elite?.why || []).slice(0, 2).join(" · ") || "Elite buy signal"
          );
        }
      }
    }
  } catch (e) {
    setStatus(String(e.message || e), "err");
  }
}

async function loadRosterOnly() {
  try {
    const res = await fetch(apiUrl("/api/elite/traders"));
    const data = await res.json();
    if (data.ok) renderRoster(data.traders || []);
  } catch {
    /* ignore */
  }
}

function bind() {
  $("#scanBtn")?.addEventListener("click", scan);
  let timer = null;
  const arm = () => {
    if (timer) clearInterval(timer);
    if ($("#autoRefresh")?.checked) {
      timer = setInterval(scan, 15000);
    }
  };
  $("#autoRefresh")?.addEventListener("change", arm);
  arm();
  loadRosterOnly();
  scan();
}

bind();
