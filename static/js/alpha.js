/**
 * Alpha Buy desk — group mentions → pro BUY / WATCH cards.
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

function setStatus(msg, kind = "") {
  const el = $("#status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status" + (kind ? ` ${kind}` : "");
}

function cardHtml(t) {
  const a = t.alpha || {};
  const label = t.alpha_label || a.label || "WATCH";
  const score = t.alpha_score ?? a.score ?? "—";
  const why = a.why || [];
  const groups = a.groups || [];
  const sources = a.sources || [];
  const mint = t.tokenAddress || t.mint || "";
  const short = mint ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : "";
  const mcap = t.mcap_usd || 0;
  const liq = t.liquidity_usd || 0;
  const age =
    t.age_minutes != null ? `${Number(t.age_minutes).toFixed(0)}m` : "—";
  const buys = t.buys_m5 ?? "—";
  const sells = t.sells_m5 ?? "—";
  const padre =
    t.padre_url ||
    (mint ? `https://trade.padre.gg/trade/solana/${mint}` : "#");
  const pump = mint ? `https://pump.fun/coin/${mint}` : "#";
  const cls = label === "BUY" ? "buy" : "watch";
  const emoji = label === "BUY" ? "📣" : "👁";

  const groupHtml = groups.length
    ? groups
        .slice(0, 5)
        .map((g) => `<span class="group-chip">${escapeHtml(String(g))}</span>`)
        .join("")
    : "";
  const whyHtml = why.length
    ? `<ul class="alpha-why">${why
        .map((w) => `<li>${escapeHtml(String(w))}</li>`)
        .join("")}</ul>`
    : "";
  const srcLine = sources.length
    ? escapeHtml(sources.join(" · "))
    : "—";

  return `<article class="card ${cls}">
    <div class="icon ph">${emoji}</div>
    <div class="body">
      <div class="head">
        <span class="sym">$${escapeHtml(t.symbol || "?")}</span>
        <span class="name">${escapeHtml(t.name || "")}</span>
        <span class="badge ${escapeHtml(label)}">${escapeHtml(label)}</span>
        <span class="risk-tag">score ${escapeHtml(String(score))}</span>
      </div>
      <div class="alpha-meta">
        <span>${fmtUsd(mcap)} mcap</span>
        <span>liq ${fmtUsd(liq)}</span>
        <span>age ${escapeHtml(age)}</span>
        <span>flow ${escapeHtml(String(buys))}b/${escapeHtml(String(sells))}s</span>
        <span>👥 ${escapeHtml(String(a.group_count ?? groups.length ?? 0))}</span>
        <span class="mint muted">${escapeHtml(short)}</span>
      </div>
      ${groupHtml ? `<div style="margin-top:0.4rem">${groupHtml}</div>` : ""}
      ${whyHtml}
      <div class="meta" style="margin-top:0.35rem">📡 ${srcLine}</div>
      <div class="actions">
        <a class="btn sm" href="${padre}" target="_blank" rel="noopener">Padre</a>
        <a class="btn sm" href="${pump}" target="_blank" rel="noopener">Pump</a>
        <button type="button" class="btn sm copy-mint" data-mint="${escapeHtml(mint)}">Copy</button>
      </div>
    </div>
  </article>`;
}

function emptyHtml(msg) {
  return `<div class="empty-alpha">${escapeHtml(msg)}</div>`;
}

function bindCopies(root) {
  root?.querySelectorAll(".copy-mint").forEach((btn) => {
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

function render(data) {
  const buys = data.buys || [];
  const watch = data.watch || [];
  const last = data.last || {};

  const buyList = $("#buyList");
  const watchList = $("#watchList");
  if (buyList) {
    buyList.innerHTML = buys.length
      ? buys.map(cardHtml).join("")
      : emptyHtml(
          "No BUY signals right now — dumps and late mcaps are filtered. Scan again shortly."
        );
    bindCopies(buyList);
  }
  if (watchList) {
    watchList.innerHTML = watch.length
      ? watch.map(cardHtml).join("")
      : emptyHtml("No watchlist items.");
    bindCopies(watchList);
  }

  const bc = $("#buyCount");
  if (bc) bc.textContent = `(${buys.length})`;
  const wc = $("#watchCount");
  if (wc) wc.textContent = `(${watch.length})`;

  const on = $("#statOn");
  if (on) on.textContent = data.enabled === false ? "OFF" : "ALPHA ON";
  const sb = $("#statBuys");
  if (sb) sb.textContent = `${buys.length} BUY`;
  const sw = $("#statWatch");
  if (sw) sw.textContent = `${watch.length} watch`;
  const src = $("#statSource");
  if (src) {
    src.textContent =
      data.source ||
      last.source ||
      (data.padre_token_set ? "padre" : "proxy");
  }
  const pi = $("#pollInfo");
  if (pi) {
    pi.textContent = `poll ${data.poll_sec ?? last.poll_sec ?? "—"}s · score≥${
      data.min_score ?? "—"
    } · TG ${data.telegram ? "on" : "off"}`;
  }

  const errs = data.errors || last.errors || [];
  let msg = data.enabled === false
    ? "Alpha Tracker disabled (ALPHA_TRACKER_ENABLED=0)"
    : `Live · ${buys.length} BUY · ${watch.length} watch · analyzed ${
        data.analyzed ?? last.analyzed ?? "—"
      }`;
  if (!data.padre_token_set) {
    msg += " · public group-heat proxy (set PADRE_AUTH_TOKEN for live Alpha Tracker)";
  } else {
    msg += " · Padre token set";
  }
  if (errs.length) msg += ` · ${String(errs[0]).slice(0, 60)}`;
  setStatus(msg, data.enabled === false ? "" : "ok");
}

let _loading = false;

async function load({ scan = false, sendTg = false } = {}) {
  if (_loading) return;
  _loading = true;
  const btn = $("#scanBtn");
  if (btn) btn.disabled = true;
  try {
    let data;
    if (scan) {
      setStatus("Scanning group mentions…", "busy");
      const q = sendTg ? "?send=true" : "?send=false";
      const res = await fetch(`/api/alpha/scan${q}`, {
        method: "POST",
        headers: { Accept: "application/json" },
        signal: AbortSignal.timeout(90000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      if (sendTg && data.sent != null) {
        setStatus(`Scan done · Telegram sent ${data.sent}`, "ok");
      }
    } else {
      const res = await fetch("/api/alpha", {
        signal: AbortSignal.timeout(20000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      // Empty cache → auto-scan once
      if (
        !(data.buys || []).length &&
        !(data.watch || []).length &&
        data.enabled !== false
      ) {
        _loading = false;
        if (btn) btn.disabled = false;
        return load({ scan: true, sendTg: false });
      }
    }
    render(data);
  } catch (e) {
    setStatus(`Load failed: ${e.message || e}`, "err");
  } finally {
    _loading = false;
    if (btn) btn.disabled = false;
  }
}

function bind() {
  $("#scanBtn")?.addEventListener("click", () =>
    load({ scan: true, sendTg: !!$("#tgOnScan")?.checked })
  );
  let t = null;
  const arm = () => {
    if (t) clearInterval(t);
    if ($("#autoRefresh")?.checked) {
      t = setInterval(() => load({ scan: true, sendTg: false }), 55000);
    }
  };
  $("#autoRefresh")?.addEventListener("change", arm);
  arm();
  load({ scan: false });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bind);
} else {
  bind();
}
