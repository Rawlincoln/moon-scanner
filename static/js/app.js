const CHAINS = ["solana", "bsc", "base", "ethereum", "arbitrum", "polygon"];
let selectedChains = new Set(["solana"]);
let lastTokens = [];
let refreshTimer = null;
let scanInFlight = false;
const SCAN_TIMEOUT_MS = 120000;

const $ = (sel) => document.querySelector(sel);
const grid = $("#tokenGrid");
const statusBar = $("#statusBar");
const statCount = $("#statCount");

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
  grid.innerHTML = `
    <div class="loading-state">
      <div class="loading-spinner"></div>
      <p>${msg}</p>
      <p class="loading-hint">First load can take 30–90s. Cached results load instantly.</p>
    </div>`;
  statCount.textContent = "…";
}

async function fetchWithTimeout(url, timeoutMs = SCAN_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
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
  const hub = token.checkerHub || {};

  const h1 = parseFloat(pc.h1);
  const h1Class = h1 >= 0 ? "up" : "down";

  const card = document.createElement("article");
  card.className = `token-card${social.highlight ? " token-card--narrative" : ""}${sm.anti_rug_signal ? " token-card--smart-money" : ""}`;
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
    ${smartMoneyBadgesHtml(sm)}
    ${checkerHubHtml(hub, true)}
    <div class="invest-banner ${investSignal}">
      <div class="invest-title">▸ ${investSignal.replace(/_/g, " ")} (${investConf}%)</div>
      <div class="invest-action">${invest.summary || invest.action || entry.action || ""}</div>
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
  const hub = t.checkerHub || t.safetyReport?.checkerHub || {};
  const tier = t.safetyTier || "AVOID";
  const isPreview = t.preview || tier === "SCANNING";
  const card = document.createElement("article");
  card.className = `token-card${social.highlight ? " token-card--narrative" : ""}${isPreview ? " token-card--scanning" : ""}${sm.anti_rug_signal ? " token-card--smart-money" : ""}`;
  card.addEventListener("click", () => openTrenchesModal(t));

  const iconHtml = t.icon
    ? `<img class="token-icon" src="${t.icon}" alt="" onerror="this.style.display='none'" />`
    : `<div class="token-icon placeholder">◎</div>`;

  card.innerHTML = `
    ${sourceBadgesHtml([t.column ? `padre_trenches_${t.column}` : "pump.fun"])}
    ${socialBadgesHtml(social)}
    ${smartMoneyBadgesHtml(sm)}
    <div class="invest-banner ${isPreview ? "WATCH" : tier === "SAFE_ENTRY" ? "STRONG_INVEST" : tier === "WATCH" ? "WATCH" : "AVOID"}">
      <div class="invest-title">▸ ${isPreview ? "SCANNING…" : tier} (${t.safetyScore ?? 0}%)</div>
      <div class="invest-action">${rep.verdict || (isPreview ? "RugCheck + Padre analysis running…" : "")}</div>
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
      <div class="metric"><div class="label">Age</div><div class="value">${t.age_minutes ?? "—"}m</div></div>
      <div class="metric"><div class="label">Bundle</div><div class="value ${bundle.bundled ? "down" : "up"}">${bundle.bundled ? "YES" : "No"}</div></div>
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
  const hub = t.checkerHub || rep.checkerHub || {};
  $("#modalContent").innerHTML = `
    <h2>${t.name || "Token"} ($${t.symbol || "?"})</h2>
    <div class="addr-copy-row">${copyBtnHtml(t.tokenAddress, { full: true, size: "lg" })}</div>
    ${socialBadgesHtml(social)}
    ${social.summary ? `<p style="color:var(--accent);margin-bottom:12px">${social.summary}</p>` : ""}
    ${social.x_url ? `<p style="margin-bottom:8px"><a href="${social.x_url}" target="_blank" rel="noopener" style="color:#5b9fff">X / Twitter →</a></p>` : ""}
    ${social.tiktok_url ? `<p style="margin-bottom:8px"><a href="${social.tiktok_url}" target="_blank" rel="noopener" style="color:#ff6b9d">TikTok →</a></p>` : ""}
    <p style="color:var(--muted);margin-bottom:16px">${rep.verdict || ""}</p>
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
  if (!data?.columns) return data?.tokens || [];
  return [
    ...(data.safe_picks || []),
    ...(data.columns?.new || []),
    ...(data.columns?.almost_bonded || []),
    ...(data.columns?.recently_bonded || []),
  ].filter((tok, i, arr) => {
    const k = tok.tokenAddress;
    return k && arr.findIndex((x) => x.tokenAddress === k) === i;
  });
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
  const visible = applyClientFilters(tokens);
  const hidden = tokens.length - visible.length;
  grid.innerHTML = "";
  if (!visible.length) {
    let msg = "No tokens matched your filters.";
    if (tokens.length && hidden) {
      msg = `${tokens.length} tokens scanned but ${hidden} hidden by filters. Uncheck “Hide UNSAFE” or “Checker PASS only” to see more.`;
    } else if (!tokens.length) {
      msg = $("#checkerPassOnly")?.checked
        ? "No tokens passed all security checkers. Try disabling filters or re-scan."
        : "No trenches tokens found — re-scan in a few seconds.";
    }
    grid.innerHTML = `<div class="empty-state"><div class="icon">◈</div><p>${msg}</p>
      ${tokens.length ? `<button class="btn btn-secondary" id="showAllBtn">Show all ${tokens.length} scanned</button>` : ""}</div>`;
    const showAll = $("#showAllBtn");
    if (showAll) {
      showAll.onclick = () => {
        $("#safeOnly").checked = false;
        $("#checkerPassOnly").checked = false;
        renderGrid(tokens);
      };
    }
    statCount.textContent = "0";
    return;
  }
  visible.forEach((t) => grid.appendChild(t.safetyTier ? renderTrenchesCard(t) : renderCard(t)));
  statCount.textContent = hidden > 0 ? `${visible.length}/${tokens.length}` : String(visible.length);
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
    refreshTimer = setInterval(() => runScan(false, true), 60000);
  }
}

async function loadFeedPreview(limit, maxAge) {
  try {
    const res = await fetchWithTimeout(
      `/api/padre/trenches/feed?per_column=${limit}&max_age_minutes=${maxAge}`,
      20000
    );
    const data = await res.json();
    const preview = flattenTrenchesData(data);
    if (preview.length) {
      lastTokens = preview;
      renderGrid(lastTokens);
      setStatus(`Showing ${preview.length} tokens — RugCheck analysis in progress…`, true);
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
      lastTokens = flattenTrenchesData(data);
      lastTokens.sort((a, b) => {
        const tier = { SAFE_ENTRY: 0, WATCH: 1, CAUTION: 2, HIGH_RISK: 3, AVOID: 4, UNSAFE: 5 };
        const chk = { PASS: 0, WARN: 1, FAIL: 2 };
        const smRank = (t) => {
          const s = (t.smartMoney || {}).signal;
          if (s === "MAJOR_TRADER") return 0;
          if (s === "WHALE_BUY") return 1;
          if (s === "DISTRIBUTED_WHALES") return 2;
          if (s === "PAID_INTEREST") return 3;
          return 4;
        };
        const av = chk[(a.checkerHub || {}).consensus?.verdict] ?? 3;
        const bv = chk[(b.checkerHub || {}).consensus?.verdict] ?? 3;
        return (
          smRank(a) - smRank(b)
          || av - bv
          || (tier[a.safetyTier] ?? 9) - (tier[b.safetyTier] ?? 9)
          || (b.safetyScore || 0) - (a.safetyScore || 0)
          || ((b.checkerHub || {}).consensus?.score || 0) - ((a.checkerHub || {}).consensus?.score || 0)
        );
      });
    } else {
      lastTokens = data.tokens || [];
    }
    renderGrid(lastTokens);
    const visible = applyClientFilters(lastTokens).length;
    const t = new Date(data.scanned_at * 1000).toLocaleTimeString();
    const total = data.counts?.total ?? lastTokens.length;
    const safeCount = isTrenches ? (data.safe_picks || []).length : lastTokens.filter((x) => ["STRONG_INVEST", "INVEST"].includes(x.investSignal?.signal)).length;
    const narrCount = isTrenches ? (data.counts?.narrative_picks ?? 0) : lastTokens.filter((x) => x.socialSignals?.highlight).length;
    const chkPass = data.counts?.checker_pass ?? lastTokens.filter((x) => (x.checkerHub || {}).consensus?.verdict === "PASS").length;
    const chkFail = data.counts?.checker_fail ?? lastTokens.filter((x) => (x.checkerHub || {}).consensus?.verdict === "FAIL").length;
    const failNote = data.counts?.analyze_failures ? ` · ${data.counts.analyze_failures} analyze errors` : "";
    const staleNote = data.stale ? " · cached" : "";
    const filterNote = visible < total ? ` · showing ${visible}/${total}` : "";
    setStatus(
      `${total} scanned · showing ${visible} · ${safeCount} safe entry · ` +
      `${chkPass} checker PASS · ${chkFail} FAIL · ${narrCount} social${failNote} · ${t}` +
      staleNote + filterNote +
      `${$("#autoRefresh").checked ? " · auto-refresh on" : ""}`
    );
    scheduleAutoRefresh();
  } catch (err) {
    setStatus(`Scan failed: ${err.message}`);
    if (!lastTokens.length) {
      grid.innerHTML = `<div class="empty-state"><div class="icon">◈</div><p>${err.message}</p><p>Click <strong>Scan Padre Trenches</strong> to retry.</p></div>`;
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
    const res = await fetch(`/api/analyze/${encodeURIComponent(chain)}/${encodeURIComponent(addr)}`);
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
showLoadingGrid();
fetchWithTimeout("/api/health", 5000).then(() => {
  runScan(false);
}).catch(() => {
  setStatus("Server not reachable — starting scan anyway…", true);
  runScan(false);
});