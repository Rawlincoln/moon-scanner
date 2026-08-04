/**
 * Lab — paste CA, cockpit facts, public archive (Germanus-inspired).
 */
const $ = (s) => document.querySelector(s);
let filter = "all";
let lastMint = "";

function setStatus(msg, kind = "") {
  const el = $("#status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status" + (kind ? ` ${kind}` : "");
}

function fmtUsd(n) {
  if (n == null || n === "") return "n/a";
  const v = Number(n);
  if (!Number.isFinite(v)) return "n/a";
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fact(k, v, cls = "") {
  const isNa = v == null || v === "" || v === "n/a";
  const display = isNa ? "n/a" : v;
  return `<div class="fact"><div class="k">${esc(k)}</div><div class="v ${isNa ? "na" : cls}">${esc(display)}</div></div>`;
}

function authCls(s) {
  if (s === "revoked") return "ok";
  if (s === "present") return "bad";
  return "";
}

function renderCockpit(data) {
  const c = data.cockpit || (data.scan && data.scan.cockpit) || {};
  const delta = data.delta || (data.archive && data.archive.delta) || {};
  const mint = c.mint || lastMint;
  const panel = $("#cockpit");
  panel.hidden = false;

  let deltaHtml = "";
  if (delta.has_prev && delta.changes) {
    const bits = Object.entries(delta.changes)
      .slice(0, 8)
      .map(([k, ch]) => {
        const pct = ch.pct != null ? ` (${ch.pct > 0 ? "+" : ""}${ch.pct}%)` : "";
        return `<strong>${esc(k)}</strong>: ${esc(ch.from)} → ${esc(ch.to)}${esc(pct)}`;
      });
    deltaHtml = bits.length
      ? `<div class="delta">Δ vs previous scan: ${bits.join(" · ")}</div>`
      : `<div class="delta">Δ vs previous: no numeric change recorded</div>`;
  } else if (data.served_from === "archive") {
    deltaHtml = `<div class="delta">Served from archive (${esc(data.freshness?.reason || "fresh")}). Force rescan if you need a new snapshot.</div>`;
  } else {
    deltaHtml = `<div class="delta">First snapshot for this mint (nothing to compare yet).</div>`;
  }

  const served = data.served_from ? ` · ${data.served_from}` : "";
  panel.innerHTML = `
    <div class="cockpit-card">
      <h2>$${esc(c.symbol || "?")} <span style="font-weight:400;color:var(--muted)">${esc(c.name || "")}</span></h2>
      <div class="sub">${esc(mint)}${esc(served)} · coverage ${esc(c.coverage_pct ?? "—")}%</div>
      <div class="fact-grid">
        ${fact("Mint auth", c.mint_authority, authCls(c.mint_authority))}
        ${fact("Freeze", c.freeze_authority, authCls(c.freeze_authority))}
        ${fact("LP status", c.lp_status)}
        ${fact("Liquidity", fmtUsd(c.liquidity_usd))}
        ${fact("Mcap", fmtUsd(c.mcap_usd))}
        ${fact("Vol 24h", fmtUsd(c.volume_24h_usd))}
        ${fact("Top 1", c.top1_pct != null ? c.top1_pct + "%" : "n/a", c.top1_pct > 20 ? "bad" : "")}
        ${fact("Top 10", c.top10_pct != null ? c.top10_pct + "%" : "n/a")}
        ${fact("Holders", c.holders ?? "n/a")}
        ${fact("Pools", c.pools ?? "n/a")}
        ${fact("Bundled", c.bundled_pct != null ? c.bundled_pct + "%" : "n/a")}
        ${fact("Snipers", c.sniper_risk || "n/a")}
      </div>
      ${deltaHtml}
      <div class="actions">
        <button type="button" class="btn" id="starBtn">☆ Watchlist</button>
        <a class="btn" href="https://solscan.io/token/${encodeURIComponent(mint)}" target="_blank" rel="noopener">Solscan</a>
        <a class="btn" href="https://trade.padre.gg/trade/solana/${encodeURIComponent(mint)}" target="_blank" rel="noopener">Padre</a>
        <a class="btn" href="/money">Money desk</a>
      </div>
      <p class="delta" style="margin-top:10px">No buy/sell verdict here — use Money mode alerts for sized entries.</p>
    </div>`;

  $("#starBtn")?.addEventListener("click", async () => {
    await fetch("/api/lab/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mint, symbol: c.symbol, name: c.name }),
    });
    setStatus("Starred on watchlist", "");
    loadArchive();
  });
}

async function analyze(force = false) {
  const mint = ($("#mintInput")?.value || "").trim();
  if (mint.length < 32) {
    setStatus("Paste a valid mint", "err");
    return;
  }
  lastMint = mint;
  setStatus(force ? "Force rescanning…" : "Analyzing…", "busy");
  $("#analyzeBtn").disabled = true;
  try {
    const res = await fetch("/api/lab/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mint, force }),
      signal: AbortSignal.timeout(90000),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    renderCockpit(data);
    setStatus(
      data.served_from === "archive"
        ? "Archive hit (liquidity stable)"
        : "Live scan archived",
      ""
    );
    loadArchive();
  } catch (e) {
    setStatus(String(e.message || e), "err");
  } finally {
    $("#analyzeBtn").disabled = false;
  }
}

async function loadArchive() {
  try {
    const res = await fetch(`/api/lab/archive?filter=${encodeURIComponent(filter)}&limit=80`);
    const data = await res.json();
    const rows = data.rows || [];
    const body = $("#archBody");
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="11" style="color:var(--muted);font-family:var(--font)">No scans yet — paste a mint above.</td></tr>`;
      return;
    }
    body.innerHTML = rows
      .map((r) => {
        const c = r.cockpit || {};
        const age =
          r.age_hours != null
            ? r.age_hours < 1
              ? `${Math.round(r.age_hours * 60)}m`
              : r.age_hours < 48
                ? `${r.age_hours.toFixed(1)}h`
                : `${(r.age_hours / 24).toFixed(1)}d`
            : "—";
        const star = r.on_watchlist ? "★" : "☆";
        const u = r.unresolved ? `<span class="tag-u">unresolved</span>` : "";
        return `<tr data-mint="${esc(r.mint)}">
          <td><button type="button" class="star ${r.on_watchlist ? "on" : ""}" data-star="${esc(r.mint)}">${star}</button></td>
          <td class="sym">$${esc(c.symbol || r.symbol || "?")}${u}</td>
          <td>${esc(c.mint_authority || "n/a")}</td>
          <td>${esc(c.freeze_authority || "n/a")}</td>
          <td>${esc(c.lp_status || "n/a")}</td>
          <td>${fmtUsd(c.liquidity_usd ?? r.liquidity_usd)}</td>
          <td>${c.top1_pct != null ? c.top1_pct + "%" : "n/a"}</td>
          <td>${c.holders ?? r.holders ?? "n/a"}</td>
          <td>${c.pools ?? "n/a"}</td>
          <td>${r.scan_count ?? "—"}×</td>
          <td>${esc(age)}</td>
        </tr>`;
      })
      .join("");

    body.querySelectorAll("tr[data-mint]").forEach((tr) => {
      tr.addEventListener("click", (e) => {
        if (e.target.closest("[data-star]")) return;
        const m = tr.getAttribute("data-mint");
        $("#mintInput").value = m;
        analyze(false);
      });
    });
    body.querySelectorAll("[data-star]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const m = btn.getAttribute("data-star");
        const on = btn.classList.contains("on");
        if (on) {
          await fetch(`/api/lab/watchlist/${encodeURIComponent(m)}`, { method: "DELETE" });
        } else {
          await fetch("/api/lab/watchlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mint: m }),
          });
        }
        loadArchive();
      });
    });
  } catch (e) {
    setStatus("Archive load failed: " + (e.message || e), "err");
  }
}

$("#analyzeBtn")?.addEventListener("click", () => analyze(false));
$("#forceBtn")?.addEventListener("click", () => analyze(true));
$("#mintInput")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") analyze(false);
});
$("#refreshArch")?.addEventListener("click", loadArchive);
document.querySelectorAll(".filt").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".filt").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    filter = b.getAttribute("data-f") || "all";
    loadArchive();
  });
});

loadArchive();
