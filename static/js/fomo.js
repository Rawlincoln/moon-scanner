/**
 * FOMO aping channel UI — live elite buy/exit events.
 */
const $ = (s) => document.querySelector(s);

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtUsd(n) {
  const v = Number(n) || 0;
  if (!v) return "—";
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

function fmtTime(ts) {
  if (!ts) return "—";
  try {
    return new Date(Number(ts) * 1000).toLocaleTimeString();
  } catch {
    return "—";
  }
}

function setStatus(msg, kind = "") {
  const el = $("#status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status" + (kind ? ` ${kind}` : "");
}

function renderWallets(wallets = []) {
  const el = $("#wallets");
  if (!el) return;
  if (!wallets.length) {
    el.innerHTML = `<div class="roster-card">No S/A-tier wallets — add elites on /elite</div>`;
    return;
  }
  el.innerHTML = wallets
    .map((w) => {
      const a = w.address || "";
      const short = a ? `${a.slice(0, 4)}…${a.slice(-4)}` : "—";
      const href = a ? `https://solscan.io/account/${a}` : "#";
      return `<div class="roster-card">
        <span class="tier">${escapeHtml(w.tier || "S")}</span>
        <span class="lbl">${escapeHtml(w.label || "?")}</span>
        <div class="addr"><a href="${href}" target="_blank" rel="noopener">${escapeHtml(short)}</a></div>
      </div>`;
    })
    .join("");
}

function eventCard(ev) {
  const side = String(ev.side || "").toLowerCase();
  const isBuy = side === "buy";
  const badge = isBuy ? "BUY" : "EXIT";
  const cls = isBuy ? "buy" : "exit";
  const mint = ev.mint || "";
  const short = mint ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : "";
  const padre = mint
    ? `https://trade.padre.gg/trade/solana/${mint}`
    : "#";
  const pump = mint ? `https://pump.fun/coin/${mint}` : "#";
  const tx = ev.signature ? `https://solscan.io/tx/${ev.signature}` : "#";
  let bag = "";
  if (ev.pre != null && ev.post != null) {
    bag = `${Number(ev.pre).toLocaleString(undefined, { maximumFractionDigits: 0 })} → ${Number(ev.post).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  }
  let hold = "";
  if (ev.hold_sec) {
    hold = `held ${(Number(ev.hold_sec) / 60).toFixed(1)}m`;
  }

  return `<article class="card ${cls}">
    <div class="icon ph">${isBuy ? "🔥" : "🚪"}</div>
    <div class="body">
      <div class="head">
        <span class="sym">$${escapeHtml(ev.symbol || "?")}</span>
        <span class="name">${escapeHtml(ev.name || "")}</span>
        <span class="badge ${badge}">${badge}</span>
        <span class="risk-tag">${escapeHtml(ev.wallet_label || "Elite")}</span>
      </div>
      <div class="meta">
        <span>${fmtTime(ev.ts)}</span>
        <span>${fmtUsd(ev.mcap)} mcap</span>
        ${bag ? `<span>bag ${escapeHtml(bag)}</span>` : ""}
        ${hold ? `<span>${escapeHtml(hold)}</span>` : ""}
        <span class="mint muted">${escapeHtml(short)}</span>
      </div>
      <div class="actions">
        <a class="btn sm" href="${padre}" target="_blank" rel="noopener">Padre</a>
        <a class="btn sm" href="${pump}" target="_blank" rel="noopener">Pump</a>
        <a class="btn sm" href="${tx}" target="_blank" rel="noopener">Tx</a>
        <button type="button" class="btn sm copy-mint" data-mint="${escapeHtml(mint)}">Copy</button>
      </div>
    </div>
  </article>`;
}

function render(data) {
  const events = data.events || [];
  const wallets = data.wallets || [];
  const last = data.last || {};
  renderWallets(wallets);

  const list = $("#list");
  if (list) {
    if (!events.length) {
      list.innerHTML = `<div class="empty">
        <strong>No FOMO events yet</strong>
        <p>When Cupsey / Cented / Cap / … buy or sell a token, it appears here and on Telegram.</p>
        <p class="muted">Poll ~${data.poll_sec ?? "—"}s · Helius ${data.helius ? "ON" : "off (public RPC)"} · cycle ${last.cycle ?? 0}</p>
      </div>`;
    } else {
      list.innerHTML = events.map(eventCard).join("");
      list.querySelectorAll(".copy-mint").forEach((btn) => {
        btn.onclick = async () => {
          try {
            await navigator.clipboard.writeText(btn.dataset.mint || "");
            btn.textContent = "Copied";
            setTimeout(() => (btn.textContent = "Copy"), 1000);
          } catch {
            btn.textContent = "Fail";
          }
        };
      });
    }
  }

  const on = $("#statOn");
  if (on) on.textContent = data.enabled ? "FOMO ON" : "FOMO OFF";
  const w = $("#statWallets");
  if (w) w.textContent = `${wallets.length} wallets`;
  const o = $("#statOpen");
  if (o) o.textContent = `${data.open_positions ?? 0} open`;
  const pi = $("#pollInfo");
  if (pi) {
    pi.textContent = `poll ${data.poll_sec ?? "—"}s · buys ${last.buys ?? 0} · exits ${last.exits ?? 0}`;
  }

  setStatus(
    data.enabled
      ? `Live · ${events.length} recent events · telegram ${data.telegram ? "on" : "off"}`
      : "FOMO disabled (FOMO_ENABLED=0)",
    data.enabled ? "ok" : ""
  );
}

async function load() {
  try {
    const res = await fetch("/api/fomo", { signal: AbortSignal.timeout(20000) });
    const data = await res.json();
    if (!data.ok && data.enabled === false) {
      setStatus("FOMO disabled", "err");
    }
    render(data);
  } catch (e) {
    setStatus(`Load failed: ${e.message || e}`, "err");
  }
}

function bind() {
  $("#refreshBtn")?.addEventListener("click", load);
  let t = null;
  const arm = () => {
    if (t) clearInterval(t);
    if ($("#autoRefresh")?.checked) t = setInterval(load, 8000);
  };
  $("#autoRefresh")?.addEventListener("change", arm);
  arm();
  load();
}

bind();
