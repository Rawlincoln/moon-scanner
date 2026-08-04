/**
 * Desktop / browser notifications for new Moon + Safe Snipe picks.
 * Keep the Moons or Snipes tab open with Auto-refresh on.
 */
(function (global) {
  const LS_ENABLED = "moon_alerts_on";
  const LS_SEEN = "moon_alerts_seen_v1";
  const SEEN_TTL_MS = 45 * 60 * 1000; // don't re-alert same mint for 45m
  const MAX_PER_SCAN = 4;
  // First scan after open/enable only seeds memory (no spam of current list)
  const primed = { moon: false, snipe: false, heat: false, grad: false };

  function enabled() {
    return localStorage.getItem(LS_ENABLED) === "1";
  }

  function setEnabled(on) {
    localStorage.setItem(LS_ENABLED, on ? "1" : "0");
  }

  function loadSeen() {
    try {
      const raw = JSON.parse(localStorage.getItem(LS_SEEN) || "{}");
      const now = Date.now();
      const out = {};
      for (const [k, ts] of Object.entries(raw || {})) {
        if (now - Number(ts) < SEEN_TTL_MS) out[k] = Number(ts);
      }
      return out;
    } catch {
      return {};
    }
  }

  function saveSeen(map) {
    try {
      localStorage.setItem(LS_SEEN, JSON.stringify(map));
    } catch {
      /* quota */
    }
  }

  function mintKey(kind, mint) {
    return `${kind}:${mint}`;
  }

  function supported() {
    return typeof window !== "undefined" && "Notification" in window;
  }

  async function ensurePermission() {
    if (!supported()) return false;
    if (Notification.permission === "granted") return true;
    if (Notification.permission === "denied") return false;
    const p = await Notification.requestPermission();
    return p === "granted";
  }

  function notifyOne({ title, body, tag, url }) {
    if (!supported() || Notification.permission !== "granted") return;
    try {
      const n = new Notification(title, {
        body,
        tag: tag || title,
        renotify: true,
        requireInteraction: false,
        silent: false,
      });
      n.onclick = () => {
        try {
          window.focus();
          if (url) window.location.href = url;
        } catch {
          /* ignore */
        }
        n.close();
      };
      setTimeout(() => {
        try {
          n.close();
        } catch {
          /* ignore */
        }
      }, 20000);
    } catch {
      /* ignore */
    }
  }

  function fmtUsd(n) {
    const v = Number(n) || 0;
    if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
    return `$${v.toFixed(0)}`;
  }

  function seedSeen(kind, tokens) {
    const seen = loadSeen();
    const now = Date.now();
    for (const t of tokens || []) {
      const mint = (t.tokenAddress || t.mint || "").trim();
      if (mint) seen[mintKey(kind, mint)] = now;
    }
    saveSeen(seen);
  }

  /**
   * @param {"moon"|"snipe"|"heat"|"grad"} kind
   * @param {Array<object>} tokens
   */
  function alertNewPicks(kind, tokens) {
    if (!enabled() || !supported()) return 0;
    if (Notification.permission !== "granted") return 0;
    const list = Array.isArray(tokens) ? tokens : [];

    // First pass after load/enable: remember current list, don't flood
    if (!primed[kind]) {
      seedSeen(kind, list);
      primed[kind] = true;
      return 0;
    }

    const seen = loadSeen();
    const now = Date.now();
    let sent = 0;

    for (const t of list) {
      const mint = (t.tokenAddress || t.mint || "").trim();
      if (!mint) continue;

      const label =
        kind === "moon"
          ? t.moon_label || t.moon?.label || ""
          : kind === "heat"
            ? t.heat_label || t.heat?.label || ""
            : kind === "grad"
              ? t.grad_label || t.grad?.label || ""
              : t.snipe_label || t.snipe?.label || "";
      const lab = String(label).toUpperCase();

      const hot =
        kind === "moon"
          ? lab === "MOON" || lab === "WATCH"
          : kind === "heat"
            ? lab === "HEAT" || lab === "WARM"
            : kind === "grad"
              ? lab === "RUNNER" || lab === "DIP"
              : lab === "SNIPE" || lab === "SETUP";
      if (!hot) continue;

      t.__alertPri =
        lab === "MOON" || lab === "SNIPE" || lab === "HEAT" || lab === "RUNNER"
          ? 0
          : 1;
    }

    const ranked = list
      .filter((t) => t.__alertPri != null)
      .sort((a, b) => a.__alertPri - b.__alertPri);

    for (const t of ranked) {
      if (sent >= MAX_PER_SCAN) break;
      const mint = (t.tokenAddress || t.mint || "").trim();
      const key = mintKey(kind, mint);
      if (seen[key]) continue;

      const label = String(
        kind === "moon"
          ? t.moon_label || t.moon?.label || "PICK"
          : kind === "heat"
            ? t.heat_label || t.heat?.label || "HEAT"
            : kind === "grad"
              ? t.grad_label || t.grad?.label || "RUNNER"
              : t.snipe_label || t.snipe?.label || "PICK"
      ).toUpperCase();
      const sym = t.symbol || "?";
      const mcap = fmtUsd(t.mcap_usd || t.mcap || 0);
      const title =
        kind === "moon"
          ? `◈ ${label}: $${sym}`
          : kind === "heat"
            ? `🔥 ${label}: $${sym}`
            : kind === "grad"
              ? `◆ ${label}: $${sym}`
              : `⚡ ${label}: $${sym}`;
      const body =
        kind === "moon"
          ? `Moon pick · ${mcap}` +
            (t.moon?.why?.[0] || t.socialSignals?.summary
              ? ` · ${t.moon?.why?.[0] || t.socialSignals?.summary}`
              : "")
          : kind === "heat"
            ? `Organic heat (RISKY) · ${mcap}` +
              (t.heat?.why?.[0] ? ` · ${t.heat.why[0]}` : "")
            : kind === "grad"
              ? `Graduated / large · ${mcap}` +
                (t.grad?.why?.[0] ? ` · ${t.grad.why[0]}` : "")
              : `Safe snipe 2× · entry ${mcap}` +
                (t.snipe?.target_2x_usd || t.target_2x_usd
                  ? ` → TP ${fmtUsd(t.snipe?.target_2x_usd || t.target_2x_usd)}`
                  : "");

      notifyOne({
        title: title.slice(0, 80),
        body: String(body).slice(0, 160),
        tag: key,
        url:
          kind === "moon"
            ? "/"
            : kind === "heat"
              ? "/heat"
              : kind === "grad"
                ? "/graduated"
                : "/snipes",
      });
      seen[key] = now;
      sent += 1;
    }

    if (sent) saveSeen(seen);
    return sent;
  }

  function wireToggle(checkboxEl, statusEl) {
    if (!checkboxEl) return;
    checkboxEl.checked = enabled();
    const syncLabel = () => {
      if (!statusEl) return;
      if (!supported()) {
        statusEl.textContent = "Alerts unsupported in this browser";
        return;
      }
      const p = Notification.permission;
      if (!enabled()) statusEl.textContent = "Alerts off";
      else if (p === "granted") statusEl.textContent = "Alerts on";
      else if (p === "denied")
        statusEl.textContent = "Blocked — enable in browser settings";
      else statusEl.textContent = "Click to allow notifications";
    };
    syncLabel();
    checkboxEl.addEventListener("change", async () => {
      if (checkboxEl.checked) {
        const ok = await ensurePermission();
        if (!ok) {
          checkboxEl.checked = false;
          setEnabled(false);
          syncLabel();
          return;
        }
        setEnabled(true);
        // Re-prime so current list is not spammed
        primed.moon = false;
        primed.snipe = false;
        primed.heat = false;
        primed.grad = false;
        notifyOne({
          title: "Moon Scanner alerts on",
          body: "Desktop alerts for Moons, Safe Snipes & Organic Heat. Keep tab open with Auto on.",
          tag: "moon-alerts-test",
        });
      } else {
        setEnabled(false);
      }
      syncLabel();
    });
  }

  // --- Auto-open Padre when token is on BOTH moon + safe snipes ---
  const LS_PADRE_BOTH = "moon_padre_both_on";
  const LS_PADRE_OPENED = "moon_padre_opened_v1";
  const PADRE_TTL_MS = 60 * 60 * 1000; // once per mint per hour
  const pendingPadreUrls = [];
  let padrePrimed = false;

  function padreBothEnabled() {
    // default ON
    const v = localStorage.getItem(LS_PADRE_BOTH);
    return v === null || v === "1";
  }

  function setPadreBothEnabled(on) {
    localStorage.setItem(LS_PADRE_BOTH, on ? "1" : "0");
  }

  function loadPadreOpened() {
    try {
      const raw = JSON.parse(localStorage.getItem(LS_PADRE_OPENED) || "{}");
      const now = Date.now();
      const out = {};
      for (const [k, ts] of Object.entries(raw || {})) {
        if (now - Number(ts) < PADRE_TTL_MS) out[k] = Number(ts);
      }
      return out;
    } catch {
      return {};
    }
  }

  function savePadreOpened(map) {
    try {
      localStorage.setItem(LS_PADRE_OPENED, JSON.stringify(map));
    } catch {
      /* quota */
    }
  }

  const PADRE_HOSTS = new Set(["trade.padre.gg", "padre.gg"]);
  const MAX_PADRE_QUEUE = 5;

  function padreUrl(mint, token) {
    // Always build from mint on allowlisted host — ignore untrusted padre_url hosts
    if (!mint) return "";
    return `https://trade.padre.gg/trade/solana/${encodeURIComponent(mint)}`;
  }

  function isAllowedPadreUrl(url) {
    try {
      const x = new URL(String(url));
      if (x.protocol !== "https:" && x.protocol !== "http:") return false;
      const host = (x.hostname || "").toLowerCase();
      return PADRE_HOSTS.has(host) || host.endsWith(".padre.gg");
    } catch {
      return false;
    }
  }

  function openPadreTab(url) {
    if (!url || !isAllowedPadreUrl(url)) return false;
    try {
      const w = window.open(url, "_blank", "noopener,noreferrer");
      if (w) {
        try {
          w.opener = null;
        } catch {
          /* ignore */
        }
        return true;
      }
    } catch {
      /* blocked */
    }
    // Popup blocked — queue until next user click (capped)
    if (!pendingPadreUrls.includes(url) && pendingPadreUrls.length < MAX_PADRE_QUEUE) {
      pendingPadreUrls.push(url);
    }
    return false;
  }

  // Flush queued Padre tabs on any user click (bypasses popup blocker)
  if (typeof document !== "undefined") {
    document.addEventListener(
      "click",
      () => {
        let n = 0;
        while (pendingPadreUrls.length && n < MAX_PADRE_QUEUE) {
          const u = pendingPadreUrls.shift();
          if (!isAllowedPadreUrl(u)) continue;
          try {
            window.open(u, "_blank", "noopener,noreferrer");
          } catch {
            /* ignore */
          }
          n += 1;
        }
      },
      true
    );
  }

  /**
   * After a moon or snipes scan: if mint is on BOTH feeds, open Padre.
   * @param {"moon"|"snipe"} kind
   * @param {Array<object>} tokens
   * @param {(path:string)=>string} apiUrlFn
   */
  async function openPadreIfOnBoth(kind, tokens, apiUrlFn) {
    if (!padreBothEnabled()) return 0;
    const list = Array.isArray(tokens) ? tokens : [];
    if (!list.length) return 0;

    const urlFn =
      typeof apiUrlFn === "function"
        ? apiUrlFn
        : (p) => p;

    // Fetch the other feed
    const otherPath =
      kind === "moon"
        ? "/api/snipes?limit=24&force=false"
        : "/api/moon?limit=24&force=false";
    let otherTokens = [];
    try {
      const ctrl = new AbortController();
      const to = setTimeout(() => ctrl.abort(), 20000);
      const res = await fetch(urlFn(otherPath), { signal: ctrl.signal });
      clearTimeout(to);
      if (!res.ok) return 0;
      const data = await res.json();
      otherTokens = data.tokens || [];
    } catch {
      return 0;
    }

    const otherByMint = new Map();
    for (const t of otherTokens) {
      const m = (t.tokenAddress || t.mint || "").trim();
      if (m) otherByMint.set(m, t);
    }

    const opened = loadPadreOpened();
    const now = Date.now();

    // First run after load: seed current duals so we don't open a flood
    if (!padrePrimed) {
      for (const t of list) {
        const m = (t.tokenAddress || t.mint || "").trim();
        if (m && otherByMint.has(m)) opened[m] = now;
      }
      savePadreOpened(opened);
      padrePrimed = true;
      return 0;
    }

    let n = 0;
    for (const t of list) {
      const mint = (t.tokenAddress || t.mint || "").trim();
      if (!mint || !otherByMint.has(mint)) continue;
      if (opened[mint]) continue;

      const other = otherByMint.get(mint);
      const url = padreUrl(mint, t.padre_url ? t : other);
      const sym = t.symbol || other.symbol || "?";
      const moonLab = kind === "moon" ? t.moon_label || t.moon?.label : other.moon_label || other.moon?.label;
      const snipeLab = kind === "snipe" ? t.snipe_label || t.snipe?.label : other.snipe_label || other.snipe?.label;

      const ok = openPadreTab(url);
      opened[mint] = now;
      n += 1;

      // Desktop notify even if popup blocked
      if (enabled() && supported() && Notification.permission === "granted") {
        notifyOne({
          title: `◈⚡ BOTH: $${sym}`,
          body: `On Moons (${moonLab || "pick"}) + Safe Snipes (${snipeLab || "pick"}) — Padre ${ok ? "opened" : "queued (click page to open)"}`,
          tag: `both-padre:${mint}`,
          url,
        });
      }
      if (n >= 3) break; // safety cap per scan
    }
    if (n) savePadreOpened(opened);
    return n;
  }

  function wirePadreToggle(checkboxEl, statusEl) {
    if (!checkboxEl) return;
    checkboxEl.checked = padreBothEnabled();
    const sync = () => {
      if (statusEl) {
        statusEl.textContent = checkboxEl.checked
          ? "Padre auto-open when on both"
          : "Padre auto-open off";
      }
    };
    sync();
    checkboxEl.addEventListener("change", () => {
      setPadreBothEnabled(checkboxEl.checked);
      if (checkboxEl.checked) padrePrimed = false; // re-seed, no flood
      sync();
    });
  }

  global.MoonAlerts = {
    enabled,
    setEnabled,
    ensurePermission,
    alertNewPicks,
    wireToggle,
    supported,
    openPadreIfOnBoth,
    wirePadreToggle,
    padreBothEnabled,
  };
})(window);
