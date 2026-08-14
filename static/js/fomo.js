/**
 * FOMO aping channel — manage wallets + live buy/exit events.
 */
const $ = (s) => document.querySelector(s);
const ADMIN_KEY_LS = "fomo_admin_key";

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

function setManageMsg(msg, kind = "") {
  const el = $("#manageMsg");
  if (!el) return;
  el.textContent = msg || "";
  el.className = "manage-msg" + (kind ? ` ${kind}` : "");
}

function detailText(data) {
  const d = data?.detail ?? data?.error ?? data?.message;
  if (d == null) return "";
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((x) => (typeof x === "string" ? x : x?.msg || JSON.stringify(x)))
      .join("; ");
  }
  if (typeof d === "object") return d.msg || JSON.stringify(d);
  return String(d);
}

function adminHeaders() {
  const key = ($("#adminKey")?.value || localStorage.getItem(ADMIN_KEY_LS) || "").trim();
  const h = { "Content-Type": "application/json", Accept: "application/json" };
  if (key) h["X-Admin-Key"] = key;
  return h;
}

/** Signed USD for PnL cells */
function fmtPnl(n) {
  if (n == null || n === "" || Number.isNaN(Number(n))) {
    return { text: "—", cls: "na" };
  }
  const v = Number(n);
  const sign = v >= 0 ? "+" : "-";
  const a = Math.abs(v);
  let text;
  if (a >= 1e6) text = `${sign}$${(a / 1e6).toFixed(2)}M`;
  else if (a >= 1e3) text = `${sign}$${(a / 1e3).toFixed(1)}k`;
  else text = `${sign}$${a.toFixed(0)}`;
  return { text, cls: v >= 0 ? "pos" : "neg" };
}

function shortAddr(a) {
  if (!a || a.length < 10) return a || "—";
  return `${a.slice(0, 4)}…${a.slice(-4)}`;
}

function optionLabel(w) {
  const name = w.label || "KOL";
  const addr = shortAddr(w.address);
  const d1 = fmtPnl(w.pnl_1d ?? w.pnl?.["1d"]);
  const d7 = fmtPnl(w.pnl_7d ?? w.pnl?.["7d"]);
  const d30 = fmtPnl(w.pnl_30d ?? w.pnl?.["30d"]);
  return `${name}  ·  ${addr}  ·  1d ${d1.text}  ·  7d ${d7.text}  ·  30d ${d30.text}`;
}

let _walletByAddr = {};

function renderKolSelect(wallets = []) {
  const sel = $("#kolSelect");
  if (!sel) return;
  const prev = sel.value;
  _walletByAddr = {};
  wallets.forEach((w) => {
    if (w.address) _walletByAddr[w.address] = w;
  });
  const opts = [
    `<option value="">— Select KOL (${wallets.length}) — name · wallet · 1d / 7d / 30d PnL —</option>`,
  ];
  wallets.forEach((w) => {
    const a = w.address || "";
    opts.push(
      `<option value="${escapeHtml(a)}">${escapeHtml(optionLabel(w))}</option>`
    );
  });
  sel.innerHTML = opts.join("");
  if (prev && _walletByAddr[prev]) sel.value = prev;
  else if (!prev) showKolDetail(null);
  if (sel.value) showKolDetail(_walletByAddr[sel.value]);
}

function showKolDetail(w) {
  const el = $("#kolDetail");
  if (!el) return;
  if (!w) {
    el.innerHTML = `<div class="kol-detail-empty">Select a KOL to see wallet + PnL breakdown</div>`;
    return;
  }
  const a = w.address || "";
  const d1 = fmtPnl(w.pnl_1d ?? w.pnl?.["1d"]);
  const d7 = fmtPnl(w.pnl_7d ?? w.pnl?.["7d"]);
  const d30 = fmtPnl(w.pnl_30d ?? w.pnl?.["30d"]);
  const src = w.pnl_source || w.pnl?.source || "n/a";
  const sol = a ? `https://solscan.io/account/${a}` : "#";
  el.innerHTML = `
    <div class="kol-detail-grid">
      <div>
        <div class="name">${escapeHtml(w.label || "?")} <span class="tier">${escapeHtml(w.tier || "S")}</span></div>
        <div class="addr">${escapeHtml(a)}</div>
        <div class="meta-line" style="margin-top:0.35rem;font-size:0.72rem;color:#94a3b8">PnL source: ${escapeHtml(src)}</div>
      </div>
      <div class="pnl-cell"><div class="lbl">1 day</div><div class="val ${d1.cls}">${d1.text}</div></div>
      <div class="pnl-cell"><div class="lbl">7 day</div><div class="val ${d7.cls}">${d7.text}</div></div>
      <div class="pnl-cell"><div class="lbl">30 day</div><div class="val ${d30.cls}">${d30.text}</div></div>
    </div>
    <div class="kol-actions">
      <a class="btn sm" href="${sol}" target="_blank" rel="noopener">Solscan</a>
      <button type="button" class="btn sm" id="fillFormBtn">Fill add form</button>
      <button type="button" class="btn sm danger" id="detailRmBtn">Remove from FOMO</button>
    </div>`;
  $("#fillFormBtn")?.addEventListener("click", () => {
    if ($("#wLabel")) $("#wLabel").value = w.label || "";
    if ($("#wAddress")) $("#wAddress").value = a;
    if ($("#wTier")) $("#wTier").value = w.tier || "S";
  });
  $("#detailRmBtn")?.addEventListener("click", () => removeWallet(a));
}

function renderWallets(wallets = []) {
  renderKolSelect(wallets);
  const el = $("#wallets");
  if (!el) return;
  const count = $("#walletCount");
  if (count) count.textContent = `(${wallets.length})`;
  if (!wallets.length) {
    el.innerHTML = `<div class="roster-card">No wallets yet — add one above. Alerts start after the first new buy/sell.</div>`;
    return;
  }
  el.innerHTML = wallets
    .map((w) => {
      const a = w.address || "";
      const short = shortAddr(a);
      const href = a ? `https://solscan.io/account/${a}` : "#";
      const d1 = fmtPnl(w.pnl_1d ?? w.pnl?.["1d"]);
      const d7 = fmtPnl(w.pnl_7d ?? w.pnl?.["7d"]);
      const d30 = fmtPnl(w.pnl_30d ?? w.pnl?.["30d"]);
      return `<div class="roster-card" data-addr="${escapeHtml(a)}">
        <div class="card-top">
          <span class="tier">${escapeHtml(w.tier || "S")}</span>
          <span class="lbl">${escapeHtml(w.label || "?")}</span>
          <button type="button" class="btn sm danger rm-btn" data-addr="${escapeHtml(a)}" title="Stop watching">Remove</button>
        </div>
        <div class="addr"><a href="${href}" target="_blank" rel="noopener">${escapeHtml(short)}</a></div>
        <div class="pnl-row">
          <span class="${d1.cls}">1d ${d1.text}</span>
          <span class="${d7.cls}">7d ${d7.text}</span>
          <span class="${d30.cls}">30d ${d30.text}</span>
        </div>
      </div>`;
    })
    .join("");

  el.querySelectorAll(".rm-btn").forEach((btn) => {
    btn.onclick = () => removeWallet(btn.dataset.addr);
  });
}

function eventCard(ev) {
  const side = String(ev.side || "").toLowerCase();
  const isBuy = side === "buy";
  const badge = isBuy ? "BUY" : "EXIT";
  const cls = isBuy ? "buy" : "exit";
  const mint = ev.mint || "";
  const short = mint ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : "";
  const padre = mint ? `https://trade.padre.gg/trade/solana/${mint}` : "#";
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
        <p>Add wallets above. When they buy or sell after being added, alerts show here and on Telegram.</p>
        <p class="muted">Poll ~${data.poll_sec ?? "—"}s · Helius ${data.helius ? "ON" : "off"} · cycle ${last.cycle ?? 0}</p>
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

  const errs = (data.last && data.last.errors) || [];
  const rpcWarn = errs.some((e) => /429|rate-limit/i.test(String(e)));
  let statusMsg = data.enabled
    ? `Live · ${wallets.length} watched · ${events.length} events · TG ${data.telegram ? "on" : "off"}`
    : "FOMO disabled (FOMO_ENABLED=0)";
  if (rpcWarn) {
    statusMsg +=
      " · ⚠ Public RPC rate-limited — set HELIUS_API_KEY on Render for FOMO to fire reliably";
  } else if (errs.length) {
    statusMsg += ` · last error: ${String(errs[0]).slice(0, 80)}`;
  }
  setStatus(statusMsg, rpcWarn ? "err" : data.enabled ? "ok" : "");

  // Hide admin key if open manage
  const ak = document.querySelector(".admin-key-field");
  if (ak) ak.style.display = data.open_manage === false ? "" : "none";
}

async function load() {
  try {
    // with_pnl loads KOL 1d/7d/30d for dropdown (may take a few seconds)
    const res = await fetch("/api/fomo?with_pnl=1", {
      signal: AbortSignal.timeout(45000),
    });
    const data = await res.json();
    render(data);
  } catch (e) {
    setStatus(`Load failed: ${e.message || e}`, "err");
  }
}

async function addWallet(ev) {
  ev?.preventDefault?.();
  const label = ($("#wLabel")?.value || "").trim();
  const address = ($("#wAddress")?.value || "").trim();
  const tier = ($("#wTier")?.value || "S").trim();
  const key = ($("#adminKey")?.value || "").trim();
  if (key) localStorage.setItem(ADMIN_KEY_LS, key);

  if (!address) {
    setManageMsg("Wallet address required", "err");
    return;
  }
  setManageMsg("Adding…");
  const btn = $("#addBtn");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/fomo/wallets", {
      method: "POST",
      headers: adminHeaders(),
      body: JSON.stringify({ address, label: label || undefined, tier }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(detailText(data) || res.statusText || "Add failed");
    }
    setManageMsg(
      `Added ${data.wallet?.label || label || "wallet"} — watching for new buys/exits` +
        (data.seeded_sigs ? ` (seeded ${data.seeded_sigs} old txs)` : ""),
      "ok"
    );
    if ($("#wAddress")) $("#wAddress").value = "";
    if ($("#wLabel")) $("#wLabel").value = "";
    await load();
  } catch (e) {
    const msg = String(e.message || e);
    setManageMsg(
      /Admin-Key|required|401/i.test(msg)
        ? "Need Admin key — paste ADMIN_API_KEY from Render env, or set FOMO_OPEN_MANAGE=1"
        : msg,
      "err"
    );
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function removeWallet(address) {
  if (!address) return;
  if (!confirm(`Stop watching this wallet?\n${address}`)) return;
  const key = ($("#adminKey")?.value || "").trim();
  if (key) localStorage.setItem(ADMIN_KEY_LS, key);
  setManageMsg("Removing…");
  try {
    const res = await fetch(`/api/fomo/wallets/${encodeURIComponent(address)}`, {
      method: "DELETE",
      headers: adminHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(detailText(data) || "Remove failed");
    }
    setManageMsg("Removed — no more alerts for that wallet", "ok");
    await load();
  } catch (e) {
    setManageMsg(String(e.message || e), "err");
  }
}

function bind() {
  const ak = $("#adminKey");
  if (ak) ak.value = localStorage.getItem(ADMIN_KEY_LS) || "";
  $("#refreshBtn")?.addEventListener("click", load);
  $("#addForm")?.addEventListener("submit", addWallet);
  $("#kolSelect")?.addEventListener("change", (e) => {
    const addr = e.target.value;
    showKolDetail(addr ? _walletByAddr[addr] : null);
    if (addr && _walletByAddr[addr]) {
      // Filter events list highlight — optional scroll
      const w = _walletByAddr[addr];
      if ($("#wLabel")) $("#wLabel").value = w.label || "";
      if ($("#wAddress")) $("#wAddress").value = w.address || "";
    }
  });
  let t = null;
  const arm = () => {
    if (t) clearInterval(t);
    // PnL is cached server-side; refresh UI every 20s to avoid spam
    if ($("#autoRefresh")?.checked) t = setInterval(load, 20000);
  };
  $("#autoRefresh")?.addEventListener("change", arm);
  arm();
  load();
}

bind();
