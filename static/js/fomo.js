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

function adminHeaders() {
  const key = ($("#adminKey")?.value || localStorage.getItem(ADMIN_KEY_LS) || "").trim();
  const h = { "Content-Type": "application/json" };
  if (key) h["X-Admin-Key"] = key;
  return h;
}

function renderWallets(wallets = []) {
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
      const short = a ? `${a.slice(0, 4)}…${a.slice(-4)}` : "—";
      const href = a ? `https://solscan.io/account/${a}` : "#";
      return `<div class="roster-card" data-addr="${escapeHtml(a)}">
        <div class="card-top">
          <span class="tier">${escapeHtml(w.tier || "S")}</span>
          <span class="lbl">${escapeHtml(w.label || "?")}</span>
          <button type="button" class="btn sm danger rm-btn" data-addr="${escapeHtml(a)}" title="Stop watching">Remove</button>
        </div>
        <div class="addr"><a href="${href}" target="_blank" rel="noopener">${escapeHtml(short)}</a></div>
        <div class="meta-line">${escapeHtml(w.source || "manual")}${w.note ? " · " + escapeHtml(w.note) : ""}</div>
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

  setStatus(
    data.enabled
      ? `Live · ${wallets.length} watched · ${events.length} events · TG ${data.telegram ? "on" : "off"}`
      : "FOMO disabled (FOMO_ENABLED=0)",
    data.enabled ? "ok" : ""
  );
}

async function load() {
  try {
    const res = await fetch("/api/fomo", { signal: AbortSignal.timeout(20000) });
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
      throw new Error(data.detail || data.error || res.statusText || "Add failed");
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
      msg.includes("401") || /Admin-Key|required/i.test(msg)
        ? "Need Admin key — paste ADMIN_API_KEY below and try again"
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
      throw new Error(data.detail || data.error || "Remove failed");
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
