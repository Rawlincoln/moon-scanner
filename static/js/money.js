/**
 * Money Desk — pending Take/Skip, bankroll, open positions, size calc.
 */
const $ = (s) => document.querySelector(s);

function apiUrl(path) {
  return path.startsWith("/") ? path : `/${path}`;
}

function fmtUsd(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(2)}`;
}

function metric(k, v, cls = "") {
  return `<div class="metric"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
}

function setStatus(msg, kind = "") {
  const el = $("#status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status" + (kind ? ` ${kind}` : "");
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function ageMin(ts) {
  if (!ts) return "—";
  const m = (Date.now() / 1000 - Number(ts)) / 60;
  if (m < 1) return "<1m";
  if (m < 60) return `${m.toFixed(0)}m ago`;
  return `${(m / 60).toFixed(1)}h ago`;
}

async function loadPending() {
  const el = $("#pendingList");
  if (!el) return;
  try {
    const res = await fetch(apiUrl("/api/journal/pending?limit=15"), {
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const rows = data.pending || [];
    if (!rows.length) {
      el.innerHTML = `<p class="pending-empty">No pending alerts. When Telegram fires MOON/SNIPE, they show up here for Take / Skip.</p>`;
      return;
    }
    el.innerHTML = rows
      .map((p) => {
        const padre = `https://trade.padre.gg/trade/solana/${p.mint || ""}`;
        return `<div class="pending-row" data-id="${escapeHtml(p.id)}">
          <span class="tag open">${escapeHtml(p.label || p.feed || "?")}</span>
          <span class="sym">$${escapeHtml(p.symbol || "?")}</span>
          <span class="muted">${escapeHtml((p.feed || "").toUpperCase())}</span>
          <span class="muted">entry ${fmtUsd(p.entry_mcap)}</span>
          <span class="muted">size ${fmtUsd(p.size_usd)}</span>
          <span class="muted">${escapeHtml(ageMin(p.ts))}</span>
          <div class="actions">
            <a class="btn sm" href="${padre}" target="_blank" rel="noopener">Padre</a>
            <button type="button" class="btn take" data-act="take" data-id="${escapeHtml(p.id)}">I took this</button>
            <button type="button" class="btn skip" data-act="skip" data-id="${escapeHtml(p.id)}">Skip</button>
          </div>
        </div>`;
      })
      .join("");

    el.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.onclick = () => onPendingAction(btn.dataset.act, btn.dataset.id, btn);
    });
  } catch (e) {
    el.innerHTML = `<p class="pending-empty">Could not load pending: ${escapeHtml(e.message || e)}</p>`;
  }
}

async function onPendingAction(act, id, btn) {
  if (!id) return;
  const take = act === "take";
  if (take && !confirm("Confirm you entered this trade?\nIt will open a journal position and use a risk slot.")) {
    return;
  }
  if (btn) btn.disabled = true;
  setStatus(take ? "Opening position…" : "Skipping…", "busy");
  try {
    const res = await fetch(apiUrl(take ? "/api/journal/take" : "/api/journal/skip"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ id }),
      signal: AbortSignal.timeout(20000),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.message || res.statusText || "failed");
    }
    setStatus(
      take
        ? `Took it — position #${data.id} open (risk slot used)`
        : "Skipped — no risk slot",
      "ok"
    );
    await loadDesk();
  } catch (e) {
    setStatus(String(e.message || e), "err");
    if (btn) btn.disabled = false;
  }
}

async function loadDesk() {
  setStatus("Loading money desk…", "busy");
  try {
    const res = await fetch(apiUrl("/api/money"), { signal: AbortSignal.timeout(20000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    renderDesk(d);
    await loadPending();
    setStatus(`Updated ${new Date().toLocaleTimeString()}`, "");
  } catch (e) {
    setStatus(`Failed: ${e.message || e}`, "err");
  }
}

function renderDesk(d) {
  $("#pillArmed").textContent = d.armed ? "ARMED" : "DISARMED";
  $("#pillArmed").className = "pill" + (d.armed ? "" : " muted");
  $("#pillGate").textContent = d.can_open ? "CAN OPEN" : "BLOCKED";
  $("#pillGate").className = "pill" + (d.can_open ? "" : " muted");
  $("#pillBankroll").textContent = `bankroll ${fmtUsd(d.bankroll_usd)}`;

  const s = d.session || {};
  const dayR = Number(s.day_r) || 0;
  const rCls = dayR > 0 ? "good" : dayR < 0 ? "bad" : "";
  $("#sessionMetrics").innerHTML = [
    metric("Day R", `${dayR}R`, rCls),
    metric("Open", s.open_count ?? "—"),
    metric("Opened today", s.opened_today ?? "—"),
    metric("W / L today", `${s.wins_today ?? 0} / ${s.losses_today ?? 0}`),
    metric("Risk/trade", fmtUsd(d.risk_per_trade_usd)),
    metric("Gate", d.can_open ? "OK" : "STOP", d.can_open ? "good" : "bad"),
  ].join("");

  const e = d.expectancy || d.journal || {};
  const er = e.expectancy_r;
  $("#expectMetrics").innerHTML = [
    metric("E[R]", er != null ? er : "—", er > 0 ? "good" : er < 0 ? "bad" : ""),
    metric("Win rate", e.win_rate_pct != null ? `${e.win_rate_pct}%` : "—"),
    metric("Sample n", e.sample_n ?? "—"),
    metric("Wins", e.wins ?? "—", "good"),
    metric("Losses", e.losses ?? "—", "bad"),
    metric(
      "PnL $",
      e.total_pnl_usd != null
        ? fmtUsd(e.total_pnl_usd)
        : d.journal?.total_pnl_usd != null
          ? fmtUsd(d.journal.total_pnl_usd)
          : "—"
    ),
  ].join("");

  const open = s.open_trades || [];
  $("#openList").innerHTML = open.length
    ? open
        .map(
          (t) => `<div class="row">
            <span class="tag open">OPEN</span>
            <span class="sym">$${escapeHtml(t.symbol || "?")}</span>
            <span class="muted">${escapeHtml(t.feed || "")} · ${escapeHtml(t.label || "")}</span>
            <span class="muted">entry ${fmtUsd(t.entry_mcap)}</span>
            <span class="muted">last ${fmtUsd(t.last_mcap || t.peak_mcap)}</span>
            <span class="muted">size ${fmtUsd(t.size_usd)}</span>
            <span class="muted">#${t.id}</span>
          </div>`
        )
        .join("")
    : `<p class="muted">No open positions. Take a pending alert after you enter.</p>`;

  const pb = d.playbook || [];
  const extra = [
    "After Telegram: Money desk → I took this (only then risk slot counts)",
    "Skip if you did not enter — never auto-open positions",
  ];
  $("#playbook").innerHTML = [...extra, ...pb]
    .map((x) => `<li>${escapeHtml(x)}</li>`)
    .join("");

  loadTrades();
}

async function loadTrades() {
  try {
    const res = await fetch(apiUrl("/api/journal/trades?limit=15"), {
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return;
    const data = await res.json();
    const rows = data.trades || [];
    $("#tradeList").innerHTML = rows.length
      ? rows
          .map((t) => {
            const st = t.status || "";
            const tag =
              st === "open" ? "open" : st === "invalid" ? "invalid" : "closed";
            return `<div class="row">
              <span class="tag ${tag}">${escapeHtml(st)}</span>
              <span class="sym">$${escapeHtml(t.symbol || "?")}</span>
              <span class="muted">${escapeHtml(t.feed)} · ${escapeHtml(t.label || "")}</span>
              <span class="muted">${fmtUsd(t.entry_mcap)} → ${fmtUsd(t.exit_mcap || t.last_mcap || t.peak_mcap)}</span>
              <span class="muted">${t.outcome || "—"} · R=${t.r_multiple ?? "—"} · ${fmtUsd(t.pnl_usd)}</span>
            </div>`;
          })
          .join("")
      : `<p class="muted">No trades yet. Confirm with I took this after an alert.</p>`;
  } catch {
    /* ignore */
  }
}

async function calcSize() {
  const entry = $("#sizeEntry")?.value || 15000;
  const stop = $("#sizeStop")?.value || 18;
  const bank = $("#sizeBank")?.value || 500;
  const risk = $("#sizeRisk")?.value || 1;
  const url = apiUrl(
    `/api/money/size?entry_mcap=${entry}&stop_pct=${stop}&bankroll=${bank}&risk_pct=${risk}`
  );
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
    const d = await res.json();
    $("#sizeOut").textContent = [
      d.rule,
      `Size USD: ${d.size_usd}`,
      `Size SOL: ${d.size_sol ?? "—"} @ $${d.sol_usd}/SOL`,
      `Max loss if stopped: $${d.max_loss_if_stopped_usd}`,
    ].join("\n");
  } catch (e) {
    $("#sizeOut").textContent = String(e.message || e);
  }
}

$("#refreshBtn")?.addEventListener("click", () => loadDesk());
$("#sizeBtn")?.addEventListener("click", () => calcSize());
loadDesk();
setInterval(loadDesk, 45000);
