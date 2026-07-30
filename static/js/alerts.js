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
  const primed = { moon: false, snipe: false };

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
   * @param {"moon"|"snipe"} kind
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
          : t.snipe_label || t.snipe?.label || "";
      const lab = String(label).toUpperCase();

      const hot =
        kind === "moon"
          ? lab === "MOON" || lab === "WATCH"
          : lab === "SNIPE" || lab === "SETUP";
      if (!hot) continue;

      t.__alertPri = lab === "MOON" || lab === "SNIPE" ? 0 : 1;
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
          : t.snipe_label || t.snipe?.label || "PICK"
      ).toUpperCase();
      const sym = t.symbol || "?";
      const mcap = fmtUsd(t.mcap_usd || t.mcap || 0);
      const title =
        kind === "moon" ? `◈ ${label}: $${sym}` : `⚡ ${label}: $${sym}`;
      const body =
        kind === "moon"
          ? `Moon pick · ${mcap}` +
            (t.moon?.why?.[0] || t.socialSignals?.summary
              ? ` · ${t.moon?.why?.[0] || t.socialSignals?.summary}`
              : "")
          : `Safe snipe 2× · entry ${mcap}` +
            (t.snipe?.target_2x_usd || t.target_2x_usd
              ? ` → TP ${fmtUsd(t.snipe?.target_2x_usd || t.target_2x_usd)}`
              : "");

      notifyOne({
        title: title.slice(0, 80),
        body: String(body).slice(0, 160),
        tag: key,
        url: kind === "moon" ? "/" : "/snipes",
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
        notifyOne({
          title: "Moon Scanner alerts on",
          body: "You’ll get desktop alerts for new Moons & Safe Snipes. Keep this tab open with Auto on.",
          tag: "moon-alerts-test",
        });
      } else {
        setEnabled(false);
      }
      syncLabel();
    });
  }

  global.MoonAlerts = {
    enabled,
    setEnabled,
    ensurePermission,
    alertNewPicks,
    wireToggle,
    supported,
  };
})(window);
