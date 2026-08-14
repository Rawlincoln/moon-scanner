/**
 * FOMO aping channel — manage wallets + live buy/exit events.
 * Loads fast without PnL, then enriches 1d/7d/30d in the background.
 */
const $ = (s) => document.querySelector(s);
const ADMIN_KEY_LS = "fomo_admin_key";
/** Browser backup so free-tier redeploys keep your add/remove list */
const WALLETS_LS = "fomo_wallets_v1";

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
let _lastData = null;
let _loading = false;
let _pnlInflight = false;
let _restoring = false;

function readLocalWallets() {
  try {
    const raw = localStorage.getItem(WALLETS_LS);
    if (!raw) return null;
    const o = JSON.parse(raw);
    if (!o || !Array.isArray(o.wallets)) return null;
    return o;
  } catch {
    return null;
  }
}

function saveLocalWallets(wallets, { userTouched = true, updated = 0 } = {}) {
  try {
    const payload = {
      version: 1,
      user_touched: !!userTouched,
      updated: Number(updated) || Date.now() / 1000,
      wallets: (wallets || []).map((w) => ({
        address: w.address,
        label: w.label,
        tier: w.tier,
        note: w.note,
        source: w.source,
        added_at: w.added_at,
        id: w.id,
      })),
    };
    localStorage.setItem(WALLETS_LS, JSON.stringify(payload));
  } catch {
    /* private mode etc. */
  }
}

function addrSet(wallets) {
  return new Set(
    (wallets || []).map((w) => String(w.address || "").trim()).filter(Boolean)
  );
}

function setsEqual(a, b) {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

/**
 * After free-tier wipe the server re-seeds elite defaults.
 * If this browser has a newer customized list, push it back.
 */
async function maybeRestoreFromLocal(serverData) {
  if (_restoring) return false;
  const local = readLocalWallets();
  if (!local || !local.user_touched) return false;

  const serverTouched = !!serverData.user_touched;
  const serverUpd = Number(serverData.wallets_updated || serverData.updated || 0);
  const localUpd = Number(local.updated || 0);
  const serverW = serverData.wallets || [];
  const localW = local.wallets || [];

  // Server already has our (or newer) custom list
  if (serverTouched && serverUpd >= localUpd && setsEqual(addrSet(serverW), addrSet(localW))) {
    return false;
  }
  // Prefer newer server customization from another device/session
  if (serverTouched && serverUpd > localUpd) {
    saveLocalWallets(serverW, { userTouched: true, updated: serverUpd });
    return false;
  }
  // Only restore when local is customized and differs / is newer
  if (!local.user_touched) return false;
  if (serverTouched && serverUpd >= localUpd) return false;
  if (setsEqual(addrSet(serverW), addrSet(localW)) && serverTouched) return false;

  _restoring = true;
  try {
    setManageMsg("Restoring your saved watchlist after restart…");
    const res = await fetch("/api/fomo/wallets/import", {
      method: "POST",
      headers: adminHeaders(),
      body: JSON.stringify({
        wallets: localW,
        user_touched: true,
        updated: localUpd || undefined,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(detailText(data) || "Restore failed");
    }
    setManageMsg(
      `Restored ${data.count ?? localW.length} wallets from your browser backup`,
      "ok"
    );
    return true;
  } catch (e) {
    setManageMsg(`Watchlist restore skipped: ${e.message || e}`, "err");
    return false;
  } finally {
    _restoring = false;
  }
}

function renderKolSelect(wallets = []) {
  const sel = $("#kolSelect");
  if (!sel) return;
  const prev = sel.value;
  _walletByAddr = {};
  wallets.forEach((w) => {
    if (w.address) _walletByAddr[w.address] = w;
  });
  const n = wallets.length;
  const opts = [
    n
      ? `<option value="">— Click to open watchlist (${n}) — name · wallet · 1d / 7d / 30d —</option>`
      : `<option value="">— No wallets yet — add one below —</option>`,
  ];
  wallets.forEach((w) => {
    const a = w.address || "";
    opts.push(
      `<option value="${escapeHtml(a)}">${escapeHtml(optionLabel(w))}</option>`
    );
  });
  sel.innerHTML = opts.join("");
  if (prev && _walletByAddr[prev]) {
    sel.value = prev;
    showKolDetail(_walletByAddr[prev]);
  } else {
    showKolDetail(null);
  }
}

function showKolDetail(w) {
  const el = $("#kolDetail");
  if (!el) return;
  if (!w) {
    el.hidden = true;
    el.innerHTML = `<div class="kol-detail-empty">Select a wallet from the dropdown</div>`;
    return;
  }
  el.hidden = false;
  const a = w.address || "";
  const d1 = fmtPnl(w.pnl_1d ?? w.pnl?.["1d"]);
  const d7 = fmtPnl(w.pnl_7d ?? w.pnl?.["7d"]);
  const d30 = fmtPnl(w.pnl_30d ?? w.pnl?.["30d"]);
  const src = w.pnl_source || w.pnl?.source || "n/a";
  const note = w.pnl?.note || w.note || "";
  const sol = a ? `https://solscan.io/account/${a}` : "#";
  el.innerHTML = `
    <div class="kol-detail-grid">
      <div>
        <div class="name">${escapeHtml(w.label || "?")} <span class="tier-pill">${escapeHtml(w.tier || "S")}</span></div>
        <div class="addr">${escapeHtml(a)}</div>
        <div style="margin-top:0.35rem;font-size:0.72rem;color:#94a3b8">PnL source: ${escapeHtml(src)}${note ? ` · ${escapeHtml(String(note).slice(0, 80))}` : ""}</div>
      </div>
      <div class="pnl-cell"><div class="lbl">1 day</div><div class="val ${d1.cls}">${d1.text}</div></div>
      <div class="pnl-cell"><div class="lbl">7 day</div><div class="val ${d7.cls}">${d7.text}</div></div>
      <div class="pnl-cell"><div class="lbl">30 day</div><div class="val ${d30.cls}">${d30.text}</div></div>
    </div>
    <div class="kol-actions">
      <a class="btn sm" href="${sol}" target="_blank" rel="noopener">Solscan</a>
      <button type="button" class="btn sm" id="copyAddrBtn">Copy address</button>
      <button type="button" class="btn sm danger" id="detailRmBtn">Remove from FOMO</button>
    </div>`;
  $("#copyAddrBtn")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(a);
      const b = $("#copyAddrBtn");
      if (b) {
        b.textContent = "Copied";
        setTimeout(() => (b.textContent = "Copy address"), 1000);
      }
    } catch {
      /* ignore */
    }
  });
  $("#detailRmBtn")?.addEventListener("click", () => removeWallet(a));
}

function renderWallets(wallets = []) {
  renderKolSelect(wallets);
  const count = $("#walletCount");
  if (count) count.textContent = `(${wallets.length})`;
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
  _lastData = data;
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

  // Keep browser backup when server holds a customized list
  if (data.user_touched) {
    saveLocalWallets(wallets, {
      userTouched: true,
      updated: data.wallets_updated || data.updated || Date.now() / 1000,
    });
  }
}

async function enrichPnl() {
  if (_pnlInflight) return;
  _pnlInflight = true;
  try {
    const res = await fetch("/api/fomo/wallets?with_pnl=1", {
      signal: AbortSignal.timeout(60000),
    });
    if (!res.ok) return;
    const data = await res.json();
    const wallets = data.wallets || [];
    if (!wallets.length) return;
    // Merge into last status payload so events stay put
    if (_lastData) {
      _lastData.wallets = wallets;
      renderWallets(wallets);
      const w = $("#statWallets");
      if (w) w.textContent = `${wallets.length} wallets`;
    }
  } catch {
    /* PnL is optional — table already shows names/addresses */
  } finally {
    _pnlInflight = false;
  }
}

async function load({ withPnl = false } = {}) {
  if (_loading) return;
  _loading = true;
  try {
    // Fast path first so add form + dropdown always appear
    const res = await fetch("/api/fomo?with_pnl=0", {
      signal: AbortSignal.timeout(20000),
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    let data = await res.json();
    // Free-tier wipe: push browser backup back to server if needed
    const restored = await maybeRestoreFromLocal(data);
    if (restored) {
      const res2 = await fetch("/api/fomo?with_pnl=0", {
        signal: AbortSignal.timeout(20000),
      });
      if (res2.ok) data = await res2.json();
    }
    render(data);
    // Optional PnL pass (does not block UI)
    if (withPnl !== false) {
      enrichPnl();
    }
  } catch (e) {
    setStatus(`Load failed: ${e.message || e}`, "err");
  } finally {
    _loading = false;
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
    $("#wAddress")?.focus();
    return;
  }
  if (address.length < 32 || address.length > 44) {
    setManageMsg("Invalid Solana address length (expect 32–44 base58 chars)", "err");
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
      `Added ${data.wallet?.label || label || "wallet"} — saved (survives restarts)` +
        (data.seeded_sigs ? ` · seeded ${data.seeded_sigs} old txs` : ""),
      "ok"
    );
    if ($("#wAddress")) $("#wAddress").value = "";
    if ($("#wLabel")) $("#wLabel").value = "";
    // Optimistic local backup immediately
    const cur = (_lastData?.wallets || []).filter((w) => w.address !== address);
    cur.unshift(data.wallet || { address, label, tier, source: "manual" });
    saveLocalWallets(cur, { userTouched: true });
    await load({ withPnl: true });
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
    setManageMsg("Removed — saved (survives restarts)", "ok");
    const cur = (_lastData?.wallets || []).filter((w) => w.address !== address);
    saveLocalWallets(cur, { userTouched: true });
    await load({ withPnl: true });
  } catch (e) {
    setManageMsg(String(e.message || e), "err");
  }
}

function bind() {
  const ak = $("#adminKey");
  if (ak) ak.value = localStorage.getItem(ADMIN_KEY_LS) || "";
  $("#refreshBtn")?.addEventListener("click", () => load({ withPnl: true }));
  $("#addForm")?.addEventListener("submit", addWallet);
  $("#kolSelect")?.addEventListener("change", (e) => {
    const addr = e.target.value;
    const w = addr ? _walletByAddr[addr] : null;
    showKolDetail(w || null);
  });
  let t = null;
  const arm = () => {
    if (t) clearInterval(t);
    if ($("#autoRefresh")?.checked) t = setInterval(() => load({ withPnl: false }), 12000);
  };
  $("#autoRefresh")?.addEventListener("change", arm);
  arm();
  load({ withPnl: true });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bind);
} else {
  bind();
}
