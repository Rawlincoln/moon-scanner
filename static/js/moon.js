/**
 * Moon Scanner UI — multi-pillar confidence, accuracy-first.
 */
const CLOUD_API = "https://moon-scanner-9tlz.onrender.com";
const IS_CLOUD = /onrender\.com$/i.test(location.hostname);
const IS_LOCAL_PAGE = /^(localhost|127\.0\.0\.1)$/i.test(location.hostname || "");
const $ = (s) => document.querySelector(s);

function apiBase() {
  // Page served from local server → always same-origin (ignore stale cloud mode)
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

/** Only allow http(s) for href/src — blocks javascript: and other schemes. */
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

function stageLabel(stage) {
  if (stage === "near_migration") return "Near migration";
  if (stage === "climb") return "Climbing";
  return "Early";
}

function pillarBar(name, val) {
  const v = Math.max(0, Math.min(100, Number(val) || 0));
  const cls = v >= 70 ? "hi" : v >= 45 ? "mid" : "lo";
  return `<div class="pillar"><span class="p-name">${escapeHtml(name)}</span>
    <div class="p-track"><i class="${cls}" style="width:${v}%"></i></div>
    <span class="p-val">${v}</span></div>`;
}

function cardHtml(t) {
  const moon = t.moon || {};
  const social = t.socialSignals || {};
  const label = t.moon_label || moon.label || "WATCH";
  const score = t.moon_score ?? moon.moon_score ?? "—";
  const conf = t.confidence ?? moon.confidence ?? score;
  const stage = t.stage || moon.stage || "early";
  const pillars = moon.pillars || {};
  const why = moon.why || [];
  const badges = moon.badges || social.badges || [];
  const mcap = t.mcap_usd || 0;
  const ath = t.ath_mcap || 0;
  const athPct = t.ath_retention_pct ?? moon.ath_retention_pct;
  const bond = Number(t.bonding_progress || 0);
  const age = t.age_minutes != null ? `${Number(t.age_minutes).toFixed(0)}m` : "—";
  const mint = t.tokenAddress || "";
  const shortMint = mint ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : "";
  const infTweet = moon.influencer_tweet || social.influencer_tweet;
  const tweetBy = moon.tweet_by || social.tweet_by;
  const tweetUrl = moon.tweet_url || social.tweet_url || "";
  const icon = t.icon
    ? `<img class="icon" src="${escapeHtml(safeHttpUrl(t.icon, ""))}" alt="" onerror="this.outerHTML='<div class=\\'icon ph\\'>◎</div>'" />`
    : `<div class="icon ph">◎</div>`;
  const whyHtml = why.length
    ? `<ul class="why">${why.map((w) => `<li>${escapeHtml(String(w))}</li>`).join("")}</ul>`
    : "";
  const badgesHtml = badges.length
    ? `<div class="narr-badges">${badges
        .slice(0, 6)
        .map(
          (b) =>
            `<span class="nb ${escapeHtml(b.type || "")}">${escapeHtml(b.label || "")}</span>`
        )
        .join("")}</div>`
    : "";
  const safeTweet = tweetUrl ? safeHttpUrl(tweetUrl) : "";
  const infBanner =
    infTweet && tweetBy
      ? `<div class="inf-banner">🔥 ${escapeHtml(tweetBy)} tweet linked${
          safeTweet && safeTweet !== "#"
            ? ` · <a href="${escapeHtml(safeTweet)}" target="_blank" rel="noopener">view</a>`
            : ""
        }</div>`
      : "";
  const bs = t.bundleSniper || {};
  const sn = t.snipers || bs.snipers || moon.snipers || {};
  const bun = t.bundle || bs.bundle || moon.bundle || {};
  const snLv = sn.risk_level || "—";
  const bunLv = bun.risk_level || (bun.bundled ? "high" : "clean");
  const bunPct =
    bun.bundled_pct != null
      ? Number(bun.bundled_pct)
      : bs.bundled_pct != null
        ? Number(bs.bundled_pct)
        : null;
  const sameBlock = bun.same_block_wallets ?? bs.same_block_wallets;
  const decision = bun.decision || bs.decision || "";
  const redN = bun.red_flags?.length ?? bs.red_flag_count ?? 0;
  const patterns = (bun.patterns || bs.patterns || []).slice(0, 2);
  const sniperRow = `
    <div class="meta sniper-row">
      <span>bundled <strong class="rl-${escapeHtml(String(bunLv))}">${
        bunPct != null ? `${bunPct.toFixed(0)}%` : escapeHtml(String(bunLv))
      }</strong>
        ${bunLv && bunPct != null ? `· ${escapeHtml(String(bunLv))}` : ""}
        ${sameBlock != null ? `· ${sameBlock} early wallets` : ""}
        ${redN ? `· ${redN}/4 red flags` : ""}</span>
      <span>snipers <strong class="rl-${escapeHtml(String(snLv))}">${escapeHtml(String(snLv))}</strong>
        ${sn.max_wallet_pct != null ? `· max ${Number(sn.max_wallet_pct).toFixed(1)}%` : ""}
        ${sn.insider_count ? `· ${sn.insider_count} insider` : ""}</span>
    </div>
    ${
      decision
        ? `<div class="bundle-decision dec-${escapeHtml(String(decision))}">${escapeHtml(
            String(decision).replace(/_/g, " ")
          )}</div>`
        : ""
    }
    ${
      patterns.length
        ? `<div class="bundle-patterns">${patterns
            .map((p) => `<span class="nb warn">${escapeHtml(String(p).replace(/_/g, " "))}</span>`)
            .join("")}</div>`
        : ""
    }`;
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

  const pillarsHtml =
    pillars.momentum != null
      ? `<div class="pillars">
        ${pillarBar("Momentum", pillars.momentum)}
        ${pillarBar("Structure", pillars.structure)}
        ${pillarBar("Narrative", pillars.narrative)}
        ${pillarBar("Interest", pillars.interest)}
        ${pillarBar("Safety", pillars.safety)}
      </div>`
      : "";

  return `
    <article class="card ${escapeHtml(label.toLowerCase())}${infTweet ? " has-inf" : ""}" data-mint="${escapeHtml(mint)}">
      ${icon}
      <div class="body">
        ${infBanner}
        <div class="head">
          <span class="sym">$${escapeHtml(t.symbol || "?")}</span>
          <span class="name">${escapeHtml(t.name || "")}</span>
          <span class="badge ${escapeHtml(label)}">${escapeHtml(label)}</span>
          <span class="badge stage">${escapeHtml(stageLabel(stage))}</span>
        </div>
        ${badgesHtml}
        <div class="meta">
          <span>mcap <strong>${fmtUsd(mcap)}</strong></span>
          ${ath ? `<span>ATH <strong>${fmtUsd(ath)}</strong>${athPct != null ? ` <em>${athPct}%</em>` : ""}</span>` : ""}
          <span>bond <strong>${bond.toFixed(0)}%</strong></span>
          <span>age <strong>${age}</strong></span>
          ${m5Html}
        </div>
        ${sniperRow}
        ${pillarsHtml}
        ${whyHtml}
        <div class="mint-row">
          <code title="${escapeHtml(mint)}">${escapeHtml(shortMint)}</code>
          <button type="button" class="btn tiny copy-mint" data-mint="${escapeHtml(mint)}">Copy</button>
        </div>
      </div>
      <div class="side">
        <div class="score-block">
          <div class="score">${score}<span>score</span></div>
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
    <strong>Checked but filtered (not shown)</strong>
    <ul>${rows}</ul>
  </div>`;
}

function render(tokens, counts = {}, nearMisses = []) {
  const list = $("#list");
  if (!list) return;
  if (!tokens.length) {
    const band = counts.band_hits != null ? counts.band_hits : "—";
    list.innerHTML = `<div class="empty">
      <strong>No narrative-backed climbers right now</strong>
      <p>We only show near-ATH tokens with real edge: influencer tweets (Elon/CZ/Trump…), trending tickers, or strong community. Random green charts are hidden — they almost always dump.</p>
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
  const moonN = counts.moon ?? tokens.filter((t) => t.moon_label === "MOON").length;
  const watchN = counts.watch ?? tokens.filter((t) => t.moon_label === "WATCH").length;
  $("#statShown").textContent = `${shown} shown`;
  const infN = counts.influencer ?? tokens.filter((t) => t.moon?.influencer_tweet || t.socialSignals?.influencer_tweet).length;
  $("#statMoon").textContent = `${moonN} moon · ${watchN} watch${infN ? ` · ${infN} inf` : ""}`;
}

let scanning = false;
let timer = null;
let lastTokens = [];

async function scan(force = false) {
  if (scanning) return;
  scanning = true;
  const btn = $("#scanBtn");
  if (btn) btn.disabled = true;
  const limit = $("#limit")?.value || 16;
  const maxAge = $("#maxAge")?.value || 120;
  setStatus("Scanning pump.fun + validating with DexScreener…", "busy");

  try {
    const url = apiUrl(
      `/api/moon?limit=${limit}&max_age_minutes=${maxAge}&force=${force ? "true" : "false"}`
    );
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 45000);
    const res = await fetch(url, { signal: ctrl.signal });
    clearTimeout(to);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    lastTokens = data.tokens || [];
    render(lastTokens, data.counts || {}, data.near_misses || []);
    try {
      if (window.MoonAlerts) MoonAlerts.alertNewPicks("moon", lastTokens);
    } catch {
      /* optional */
    }
    const c = data.counts || {};
    const t = data.scanned_at
      ? new Date(data.scanned_at * 1000).toLocaleTimeString()
      : "";
    let rtNote = "";
    try {
      const rs = await fetch(apiUrl("/api/realtime/status"), { signal: AbortSignal.timeout(4000) });
      if (rs.ok) {
        const rj = await rs.json();
        const fm = rj.bus?.feed || {};
        const m = fm.mode || "poll";
        const n = rj.bus?.unique_mints ?? 0;
        rtNote = ` · feed ${m}${n ? ` (${n} mints)` : ""}`;
      }
    } catch {
      /* optional */
    }
    const oc = data.outcomes || {};
    const gates = data.gates || oc.gates || {};
    let ocNote = "";
    if (oc.finalized > 0 && oc.dump_rate_pct != null) {
      ocNote = ` · hist dump ${oc.dump_rate_pct}%` +
        (oc.win_rate_pct != null ? ` / win ${oc.win_rate_pct}%` : "") +
        ` (n=${oc.finalized})`;
    } else if (oc.total_recs > 0) {
      ocNote = ` · tracking ${oc.active ?? 0}/${oc.total_recs} recs`;
    }
    if (gates.min_score != null) {
      ocNote += ` · gates ≥${gates.min_score}/${gates.min_confidence}`;
      if (gates.adapted) ocNote += " adaptive";
    }
    setStatus(
      `${c.shown ?? lastTokens.length} candidates · band ${c.band_hits ?? "—"} · ` +
        `${c.rejected ?? 0} rejected · ${c.enriched ?? "—"} dex-checked` +
        `${data.cached ? " · cached" : ""} · ${data.mode || "moon"}${rtNote}${ocNote} · ${t}`
    );
    if ($("#rule") && data.rule) $("#rule").textContent = data.rule;
    if ($("#outcomeStats")) {
      const parts = [];
      if (oc.finalized > 0) {
        parts.push(
          `Outcomes: ${oc.finalized} done · dump ${oc.dump_rate_pct ?? "—"}% · win ${oc.win_rate_pct ?? "—"}% · active ${oc.active ?? 0}`
        );
        const bl = oc.by_label || {};
        const segs = [];
        for (const lab of ["MOON", "WATCH"]) {
          const s = bl[lab];
          if (s && s.n > 0) {
            segs.push(`${lab} dump ${s.dump_rate_pct ?? "—"}% (n=${s.n})`);
          }
        }
        const bi = oc.by_influencer || {};
        if (bi.yes?.n > 0) segs.push(`inf dump ${bi.yes.dump_rate_pct ?? "—"}%`);
        if (bi.no?.n > 0) segs.push(`no-inf dump ${bi.no.dump_rate_pct ?? "—"}%`);
        if (segs.length) parts.push(segs.join(" · "));
      } else if (oc.total_recs > 0) {
        parts.push(`Tracking ${oc.total_recs} recommendations (15m / 1h / 6h)…`);
      } else {
        parts.push("Outcomes: no recommendations logged yet — gates at defaults");
      }
      if (gates.min_score != null) {
        const why = (gates.reasons || []).slice(0, 2).join("; ");
        parts.push(
          `Gates: score≥${gates.min_score} conf≥${gates.min_confidence} bundled≤${gates.max_bundled_pct}%` +
            (gates.require_influencer ? " +influencer" : "") +
            (gates.adapted ? " (adaptive)" : " (default)") +
            (why ? ` — ${why}` : "")
        );
      }
      $("#outcomeStats").textContent = parts.join(" | ");
    }
  } catch (e) {
    const msg = e?.name === "AbortError" ? "Scan timed out" : e?.message || String(e);
    const mode = localStorage.getItem("moon_api_mode") || "local";
    let hint = "Double-click start.bat and leave the window open. Then open http://127.0.0.1:8765";
    if (!IS_LOCAL_PAGE && mode === "cloud") {
      hint = "API mode is Cloud — switch Backend to Local, or wait for Render to wake.";
    } else if (msg === "Failed to fetch" || /NetworkError|Load failed/i.test(msg)) {
      hint =
        "Server is OFF. Run C:\\Users\\MMghongo\\moon-scanner\\start.bat and keep that window open.";
    }
    setStatus(`Scan failed: ${msg}. ${hint}`, "err");
    // Auto-retry when server was briefly down
    if (/Failed to fetch|NetworkError|Load failed/i.test(msg)) {
      setTimeout(() => {
        if (!scanning) scan(false);
      }, 5000);
    }
  } finally {
    scanning = false;
    if (btn) btn.disabled = false;
  }
}

function setupAuto() {
  if (timer) clearInterval(timer);
  timer = null;
  if (!$("#autoRefresh")?.checked) return;
  timer = setInterval(() => scan(false), 10000);
}

function init() {
  // Prefer local when page is local (fixes stale localStorage moon_api_mode=cloud)
  if (IS_LOCAL_PAGE) {
    localStorage.setItem("moon_api_mode", "local");
  }
  updateBackendPill();
  const backend = $("#apiBackend");
  if (backend) {
    backend.value = localStorage.getItem("moon_api_mode") || "local";
    if (IS_LOCAL_PAGE) backend.value = "local";
    backend.onchange = () => {
      localStorage.setItem("moon_api_mode", backend.value);
      updateBackendPill();
      scan(true);
    };
  }
  $("#scanBtn")?.addEventListener("click", () => scan(true));
  $("#autoRefresh")?.addEventListener("change", setupAuto);
  $("#limit")?.addEventListener("change", () => scan(true));
  $("#maxAge")?.addEventListener("change", () => scan(true));
  try {
    if (window.MoonAlerts) {
      MoonAlerts.wireToggle($("#alertToggle"), $("#alertStatus"));
    }
  } catch {
    /* optional */
  }
  setupAuto();
  scan(true);
}

init();
