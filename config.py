"""Configuration and chain mappings for Moon Scanner."""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Load moon-scanner/.env into os.environ (does not override existing vars)."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


_load_dotenv()

USER_AGENT = "MoonScanner/1.0 (Token Safety & Signal Analyzer)"
IS_RENDER = os.getenv("MOON_SCANNER_DEPLOY", "").lower() == "render"
IS_PRODUCTION = IS_RENDER or os.getenv("MOON_SCANNER_DEPLOY", "").lower() == "production"

# DexScreener chainId -> honeypot.is chainID (integer)
EVM_CHAIN_IDS = {
    "ethereum": 1,
    "bsc": 56,
    "base": 8453,
    "arbitrum": 42161,
    "polygon": 137,
    "avalanche": 43114,
    "optimism": 10,
    "fantom": 250,
    "cronos": 25,
    "pulsechain": 369,
    "blast": 81457,
    "linea": 59144,
    "scroll": 534352,
}

SUPPORTED_CHAINS = list(EVM_CHAIN_IDS.keys()) + ["solana"]

# Safety thresholds
MAX_SELL_TAX_PCT = 5.0
MAX_BUY_TAX_PCT = 10.0
MIN_LIQUIDITY_USD = 3000
MIN_LP_LOCKED_PCT = 50.0
MAX_RISK_LEVEL = 19  # honeypot.is: low risk = 1-19
MAX_SOLANA_RUG_SCORE = 30  # normalized score (lower = safer)

# Moon scoring weights
WEIGHT_SAFETY = 0.35
WEIGHT_MOMENTUM = 0.25
WEIGHT_VOLUME = 0.20
WEIGHT_EARLY = 0.20

# Scan limits
DEFAULT_SCAN_LIMIT = 10
MAX_SCAN_LIMIT = 50
REQUEST_TIMEOUT = 6.0  # fail fast bulk scans (was 12)
REQUEST_TIMEOUT_HEAVY = 10.0  # single-token deep analyze

# Early-entry mode (real-time fresh launches)
PUMPFUN_API_URL = "https://frontend-api-v3.pump.fun"
PADRE_API_URL = "https://backend.padre.gg"
PADRE_TRADE_URL = "https://trade.padre.gg"
DEFAULT_MAX_AGE_MINUTES = 40  # longer window — runners climb for 20–90+ min
MAX_AGE_MINUTES_CAP = 180
CACHE_TTL = 8
# Short cache so we don't re-show tokens that already pumped
# Keep cache short on both local + Render so scans stay aligned with live market
TRENCHES_CACHE_TTL = 8
TRENCHES_CONCURRENCY = 14 if IS_PRODUCTION else 20  # avoid RugCheck 429 + faster
EXCLUDE_GRADUATED_DEFAULT = True
EARLY_MCAP_MIN_USD = 1_500
EARLY_MCAP_MAX_USD = 65_000

# Early / under-$25k band (lottery + mid-curve structure)
SCAN_MCAP_MAX_USD = 25_000
# Prefer analyzing / ranking under this first for early section
SCAN_MCAP_FOCUS_MAX_USD = 12_000
# pump.fun graduation is ~$69k — near-migration tokens live ABOVE $25k
GRADUATION_MCAP_USD = 69_000
MIGRATION_MCAP_MAX_USD = 78_000  # allow almost-bonded through graduation
# Bonding % toward Raydium/PumpSwap migration
MIGRATION_CLIMBING_MIN_PCT = 15.0  # ~$10k+ — mid-curve climb (under $25k lane)
MIGRATION_NEAR_MIN_PCT = 42.0  # ~$29k+ — primary "can migrate" zone
MIGRATION_ALMOST_MIN_PCT = 55.0  # ~$38k+ — almost bonded
# Must stay within this fraction of ATH to appear / recommend (user: no dumps)
DUMP_HIDE_FRAC = 0.80  # hide if mcap < 80% of ATH (−20%+)
DUMP_HARD_FRAC = 0.60  # hard dump −40%+
# Near-migration BUY needs this quality
NEAR_MIG_BUY_MIN_SCORE = 72
NEAR_MIG_BUY_MIN_BOND = 45.0
NEAR_MIG_MIN_MCAP = 18_000
NEAR_ATH_BUY_FRAC = 0.80  # must be ≥80% of ATH to recommend buy

# --- Moons mode + durable data ---
# balanced: slight ATH/organic relax, still capital-protection (not Heat)
# strict: original post-loss ultra-tight gates
_moon_mode = (os.getenv("MOON_MODE", "balanced") or "balanced").strip().lower()
MOON_MODE = _moon_mode if _moon_mode in ("balanced", "strict") else "balanced"
# Persistent data root (Render disk mount, e.g. /var/data). Falls back to ./data.
_BASE_DIR = Path(__file__).resolve().parent


def _resolve_data_dir() -> Path:
    raw = (os.getenv("DATA_DIR") or os.getenv("RENDER_DISK_PATH") or "").strip()
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw))
    candidates.append(_BASE_DIR / "data")
    for p in candidates:
        try:
            p.mkdir(parents=True, exist_ok=True)
            # Write probe so we don't pick a read-only mount
            probe = p / ".writable"
            probe.write_text("ok", encoding="utf-8")
            try:
                probe.unlink()
            except OSError:
                pass
            return p
        except OSError:
            continue
    fallback = _BASE_DIR / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


DATA_DIR = _resolve_data_dir()
MOON_OUTCOMES_DB = Path(
    os.getenv("MOON_OUTCOMES_DB") or (DATA_DIR / "moon_outcomes.db")
)
LEARNING_DB = Path(os.getenv("LEARNING_DB") or (DATA_DIR / "learning.db"))
# Skip DexScreener smart-money order fetch during bulk trenches (saves ~0.5–1s/token)
FAST_SCAN_SKIP_DEX_ORDERS = True

# Survival floor — most lottery charts die before $7k; do not recommend below it.
# (user: recs dump pre-$7k; real migrators live on the climb path)
SURVIVAL_MCAP_USD = float(os.getenv("SURVIVAL_MCAP_USD", "7000") or "7000")
# Money-grade alerts (MOON/SNIPE) require live mcap at/above this floor
MONEY_ENTRY_MIN_USD = float(os.getenv("MONEY_ENTRY_MIN_USD", "7000") or "7000")
# $7k+ radar — catch climbers that already proved survival (not pure lottery)
SIXK_RADAR_MIN_USD = 7_000
SIXK_RADAR_MAX_USD = 18_000
SIXK_ENTRY_SWEET_MIN = 7_000
SIXK_ENTRY_SWEET_MAX = 14_000
# Heavy trenches warm OFF — saturates event loop; /api/moon is the primary path
BACKGROUND_SCAN_INTERVAL_SEC = 0
BACKGROUND_SCAN_PER_COLUMN = 0
# Runner radar off by default (moon UI does its own fast scan)
RUNNER_RADAR_INTERVAL_SEC = 0
RUNNER_ALERT_TTL_SEC = 45 * 60  # keep sticky alerts 45 min
# Near-migration tokens vanish too fast if only shown when present in the latest
# pump.fun poll — pin them so the UI keeps them visible while they climb/dump.
NEAR_MIGRATION_STICKY_TTL_SEC = 8 * 60  # short pin — dumps must vanish fast
NEAR_MIGRATION_MAX_STICKY = 12

# Pro trencher — entry after survival floor; migration track uses bonding %
TARGET_MCAP_USD = 10_000
MCAP_INVEST_MIN_USD = 7_000  # never flag pure lottery under survival floor
MCAP_INVEST_MAX_USD = 22_000  # climb band still investable pre-migration
# Under-$25k "structure" band (between lottery and near-migration)
UNDER25K_MIN_USD = 8_000
UNDER25K_MAX_USD = 25_000
MIN_SURVIVAL_AGE_MINUTES = 4.0  # under $15k: need age past sniper flash
MIN_TOKEN_HOLDERS = 12
MIN_DEX_VOL_M5_USD = 600
MIN_DEX_BUYS_M5 = 8
MIN_BUY_SELL_RATIO_M5 = 1.05
MIN_PRICE_CHANGE_M5 = 0.0
MAX_SNIPER_WALLET_PCT = 22.0
MAX_DEV_HOLD_PCT = 8.0
MIN_PUMPFUN_REPLIES = 1
REQUIRE_TRENCH_GATE_FOR_INVEST = True

# --- Realtime (Geyser / Yellowstone / ShredStream + paid WSS) ---
# Without paid WSS/gRPC, app uses public logsSubscribe (rate-limited) + pump poll.
# Recommended: HELIUS_API_KEY or SOLANA_RPC_WSS from Helius / QuickNode / Triton.
YELLOWSTONE_GRPC_ENDPOINT = os.getenv("YELLOWSTONE_GRPC_ENDPOINT", "").strip()
YELLOWSTONE_GRPC_TOKEN = os.getenv("YELLOWSTONE_GRPC_TOKEN", "").strip()
YELLOWSTONE_COMMITMENT = os.getenv("YELLOWSTONE_COMMITMENT", "processed").strip() or "processed"
# Optional earliest layer (UDP shreds). Jito official deprecating ~2026-09-05.
SHREDSTREAM_ENDPOINT = os.getenv("SHREDSTREAM_ENDPOINT", "").strip()
# Default 4s on free/public (less load); set REALTIME_PUMP_POLL_SEC=2 when paid RPC
_poll_default = "4.0" if not (
    os.getenv("HELIUS_API_KEY", "").strip()
    or (os.getenv("SOLANA_RPC_HTTP") or os.getenv("SOLANA_RPC_URL") or "").strip()
) else "2.0"
REALTIME_PUMP_POLL_SEC = float(
    os.getenv("REALTIME_PUMP_POLL_SEC", _poll_default) or _poll_default
)
# logs | transaction | auto (transaction on paid WSS, else logs)
SOLANA_WS_MODE = (os.getenv("SOLANA_WS_MODE", "auto") or "auto").strip().lower()
# Default OFF on public/free RPC (avoids noisy WS + process thrash). Set
# DISABLE_SOLANA_WS=0 to force-enable when you have Helius/Alchemy WSS.
_disable_ws_raw = os.getenv("DISABLE_SOLANA_WS", "").strip().lower()
if _disable_ws_raw in ("0", "false", "no", "off"):
    DISABLE_SOLANA_WS = False
elif _disable_ws_raw in ("1", "true", "yes", "on"):
    DISABLE_SOLANA_WS = True
else:
    # Auto: disable WS when no paid RPC key/endpoint is configured
    _has_helius = bool(os.getenv("HELIUS_API_KEY", "").strip())
    _has_custom = bool(
        (os.getenv("SOLANA_RPC_WSS") or os.getenv("YELLOWSTONE_WSS") or "").strip()
    )
    DISABLE_SOLANA_WS = not (_has_helius or _has_custom)
YELLOWSTONE_ONLY = os.getenv("YELLOWSTONE_ONLY", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
# Optional wallet PnL providers for FOMO KOL dropdown (1d/7d/30d)
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "").strip()
CIELO_API_KEY = (
    os.getenv("CIELO_API_KEY") or os.getenv("CIELO_API_TOKEN") or ""
).strip()

# Admin / security
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
# Comma-separated origins; empty = safe defaults (local hosts or known Render URLs)
_cors_raw = os.getenv("CORS_ORIGINS", "").strip()
CORS_ORIGINS_LIST: list[str] = (
    [o.strip() for o in _cors_raw.split(",") if o.strip()] if _cors_raw else []
)
# Expensive API routes — per IP sliding windows
# Defaults raised for solo desk + multi-tab auto-refresh (override via env if abused)
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "90") or "90")
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "24") or "24")  # short 10s window
# Stricter for deep analyze / checkers (third-party amplification)
RATE_LIMIT_ANALYZE_PER_MIN = int(os.getenv("RATE_LIMIT_ANALYZE_PER_MIN", "20") or "20")
# force=true costs this many tokens (anti-amp) — keep modest for manual rescans
RATE_LIMIT_FORCE_COST = int(os.getenv("RATE_LIMIT_FORCE_COST", "2") or "2")
# Client IP: when True (default on production), use rightmost X-Forwarded-For hop
# (proxy-appended). When False, ignore XFF — prevents client spoofing.
_trust_xff_raw = os.getenv("TRUST_X_FORWARDED_FOR", "").strip().lower()
TRUST_X_FORWARDED_FOR = (
    _trust_xff_raw in ("1", "true", "yes")
    if _trust_xff_raw
    else IS_PRODUCTION
)
# Learning poll cap (unpaid RPC / free host should stay low)
LEARNING_ACTIVE_CAP_PAID = int(os.getenv("LEARNING_ACTIVE_CAP_PAID", "80") or "80")
LEARNING_ACTIVE_CAP_PUBLIC = int(os.getenv("LEARNING_ACTIVE_CAP_PUBLIC", "40") or "40")
# Concurrent deep analyzes (global process cap)
ANALYZE_CONCURRENCY = int(os.getenv("ANALYZE_CONCURRENCY", "4") or "4")

# --- Telegram push alerts (works without browser open) ---
# Create bot via @BotFather → token; message the bot → get chat id via getUpdates
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
_tg_en = os.getenv("TELEGRAM_ALERTS", "").strip().lower()
if _tg_en in ("0", "false", "no", "off"):
    TELEGRAM_ALERTS_ENABLED = False
elif _tg_en in ("1", "true", "yes", "on"):
    TELEGRAM_ALERTS_ENABLED = True
else:
    # Auto-on when both token + chat are set
    TELEGRAM_ALERTS_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
# Money-mode: only highest-grade alerts + stop/TP + invalidation + trade journal.
# Set TELEGRAM_MONEY_MODE=0 to restore full multi-feed (heat/grad/WATCH/SETUP) spam.
_money_raw = (os.getenv("TELEGRAM_MONEY_MODE", "1") or "1").strip().lower()
TELEGRAM_MONEY_MODE = _money_raw not in ("0", "false", "no", "off")

if TELEGRAM_MONEY_MODE:
    # Capital book: MOON + SNIPE only (heat/elite are noisy — opt-in via TELEGRAM_ALERT_FEEDS)
    _default_feeds = "moon,snipe"
    _default_moon_labels = "MOON"  # WATCH opt-in only
    _default_snipe_labels = "SNIPE"
    _default_heat_labels = "HEAT"
    _default_elite_labels = "ELITE"
else:
    _default_feeds = "moon,snipe,heat,elite,grad"
    _default_moon_labels = "MOON,WATCH"
    _default_snipe_labels = "SNIPE,SETUP"
    _default_heat_labels = "HEAT,WARM"
    _default_elite_labels = "ELITE,COPY,WATCH"

_tg_feeds = os.getenv("TELEGRAM_ALERT_FEEDS", _default_feeds).strip()
TELEGRAM_ALERT_FEEDS: list[str] = [
    f.strip().lower() for f in _tg_feeds.split(",") if f.strip()
] or (
    ["moon", "snipe", "heat", "elite"]
    if TELEGRAM_MONEY_MODE
    else ["moon", "snipe", "heat", "elite", "grad"]
)
TELEGRAM_ALERT_INTERVAL_SEC = float(
    os.getenv("TELEGRAM_ALERT_INTERVAL_SEC", "45") or "45"
)
TELEGRAM_ALERT_DEDUPE_SEC = float(
    os.getenv("TELEGRAM_ALERT_DEDUPE_SEC", str(45 * 60)) or str(45 * 60)
)
TELEGRAM_ALERT_MAX_PER_CYCLE = int(
    os.getenv("TELEGRAM_ALERT_MAX_PER_CYCLE", "6" if TELEGRAM_MONEY_MODE else "8")
    or ("6" if TELEGRAM_MONEY_MODE else "8")
)
TELEGRAM_ALERT_MOON_LABELS = {
    x.strip().upper()
    for x in (
        os.getenv("TELEGRAM_ALERT_MOON_LABELS", _default_moon_labels)
        or _default_moon_labels
    ).split(",")
    if x.strip()
}
TELEGRAM_ALERT_SNIPE_LABELS = {
    x.strip().upper()
    for x in (
        os.getenv("TELEGRAM_ALERT_SNIPE_LABELS", _default_snipe_labels)
        or _default_snipe_labels
    ).split(",")
    if x.strip()
}
TELEGRAM_ALERT_HEAT_LABELS = {
    x.strip().upper()
    for x in (
        os.getenv("TELEGRAM_ALERT_HEAT_LABELS", _default_heat_labels)
        or _default_heat_labels
    ).split(",")
    if x.strip()
}
TELEGRAM_ALERT_GRAD_LABELS = {
    x.strip().upper()
    for x in (
        os.getenv("TELEGRAM_ALERT_GRAD_LABELS", "RUNNER,DIP") or "RUNNER,DIP"
    ).split(",")
    if x.strip()
}
TELEGRAM_ALERT_ELITE_LABELS = {
    x.strip().upper()
    for x in (
        os.getenv("TELEGRAM_ALERT_ELITE_LABELS", _default_elite_labels)
        or _default_elite_labels
    ).split(",")
    if x.strip()
}
# Optional shared secret for GET /api/alerts/telegram/tick (external cron 24/7)
TELEGRAM_CRON_SECRET = os.getenv("TELEGRAM_CRON_SECRET", "").strip()

# --- FOMO aping channel (elite wallet buy/exit alerts) ---
# Poll managed FOMO wallets for SPL buys & sells; Telegram FOMO alerts.
_fomo_en = (os.getenv("FOMO_ENABLED", "1") or "1").strip().lower()
FOMO_ENABLED = _fomo_en not in ("0", "false", "no", "off")
# Public RPC needs slower polls; Helius can use 8–12s
FOMO_POLL_SEC = float(os.getenv("FOMO_POLL_SEC", "18") or "18")
FOMO_MAX_WALLETS = int(os.getenv("FOMO_MAX_WALLETS", "20") or "20")
FOMO_SIGS_PER_WALLET = int(os.getenv("FOMO_SIGS_PER_WALLET", "5") or "5")
# How many wallets to hit per cycle (round-robin) — avoids public RPC 429
FOMO_WALLETS_PER_CYCLE = int(os.getenv("FOMO_WALLETS_PER_CYCLE", "3") or "3")
_fomo_tg = (os.getenv("FOMO_ALERT_TELEGRAM", "1") or "1").strip().lower()
FOMO_ALERT_TELEGRAM = _fomo_tg not in ("0", "false", "no", "off")
# Optional dedicated Telegram chat/channel for FOMO (else main TELEGRAM_CHAT_ID)
TELEGRAM_FOMO_CHAT_ID = os.getenv("TELEGRAM_FOMO_CHAT_ID", "").strip()
# Allow add/remove FOMO wallets from the UI without Admin key (private desk).
# Set FOMO_OPEN_MANAGE=0 to require X-Admin-Key again.
_fomo_open = (os.getenv("FOMO_OPEN_MANAGE", "1") or "1").strip().lower()
FOMO_OPEN_MANAGE = _fomo_open not in ("0", "false", "no", "off")

# --- Padre Alpha Tracker (group mentions → pro BUY alerts) ---
# Real Alpha Tracker feed needs PADRE_AUTH_TOKEN (Bearer from trade.padre.gg session).
# Without it we use Dex boosts + social profiles as group-heat proxies.
_alpha_en = (os.getenv("ALPHA_TRACKER_ENABLED", "1") or "1").strip().lower()
ALPHA_TRACKER_ENABLED = _alpha_en not in ("0", "false", "no", "off")
_alpha_tg = (os.getenv("ALPHA_TRACKER_TELEGRAM", "1") or "1").strip().lower()
ALPHA_TRACKER_TELEGRAM = _alpha_tg not in ("0", "false", "no", "off")
# Also push strong WATCH cards (near-miss / slightly late) — set 0 for BUY-only
_alpha_watch_tg = (os.getenv("ALPHA_TRACKER_WATCH_TELEGRAM", "1") or "1").strip().lower()
ALPHA_TRACKER_WATCH_TELEGRAM = _alpha_watch_tg not in ("0", "false", "no", "off")
ALPHA_TRACKER_POLL_SEC = float(os.getenv("ALPHA_TRACKER_POLL_SEC", "55") or "55")
ALPHA_TRACKER_MIN_GROUPS = int(os.getenv("ALPHA_TRACKER_MIN_GROUPS", "1") or "1")
ALPHA_TRACKER_MIN_SCORE = int(os.getenv("ALPHA_TRACKER_MIN_SCORE", "68") or "68")
# Align with money survival floor — no sub-$7k lottery BUY
ALPHA_TRACKER_MCAP_MIN = float(
    os.getenv("ALPHA_TRACKER_MCAP_MIN", str(MONEY_ENTRY_MIN_USD))
    or str(MONEY_ENTRY_MIN_USD)
)
ALPHA_TRACKER_MCAP_MAX = float(os.getenv("ALPHA_TRACKER_MCAP_MAX", "55000") or "55000")
ALPHA_TRACKER_MAX_AGE_MIN = float(
    os.getenv("ALPHA_TRACKER_MAX_AGE_MIN", "180") or "180"
)
ALPHA_TRACKER_MAX_PER_CYCLE = int(
    os.getenv("ALPHA_TRACKER_MAX_PER_CYCLE", "4") or "4"
)
ALPHA_TRACKER_DEDUPE_SEC = float(
    os.getenv("ALPHA_TRACKER_DEDUPE_SEC", str(45 * 60)) or str(45 * 60)
)
# Firebase/session JWT from trade.padre.gg (Application → Local Storage / network Bearer)
PADRE_AUTH_TOKEN = (
    os.getenv("PADRE_AUTH_TOKEN") or os.getenv("PADRE_SESSION_TOKEN") or ""
).strip()
TELEGRAM_ALPHA_CHAT_ID = os.getenv("TELEGRAM_ALPHA_CHAT_ID", "").strip()

# --- Money plan (entry / stop / TP / invalidation) ---
MONEY_STOP_PCT = float(os.getenv("MONEY_STOP_PCT", "0.18") or "0.18")  # −18%
MONEY_TP1_PCT = float(os.getenv("MONEY_TP1_PCT", "0.50") or "0.50")  # +50%
MONEY_TP2_PCT = float(os.getenv("MONEY_TP2_PCT", "1.00") or "1.00")  # +100% (2×)
# Invalid must not fire before stop (was 0.15 vs stop 0.18 → dead stop path)
MONEY_INVALID_DROP_PCT = float(
    os.getenv("MONEY_INVALID_DROP_PCT", "0.20") or "0.20"
)  # −20% hard cancel floor (≥ stop)
if MONEY_INVALID_DROP_PCT < MONEY_STOP_PCT:
    MONEY_INVALID_DROP_PCT = MONEY_STOP_PCT
MONEY_MAX_HOLD_MIN = float(os.getenv("MONEY_MAX_HOLD_MIN", "45") or "45")
MONEY_INVALID_NO_MOVE_PCT = float(
    os.getenv("MONEY_INVALID_NO_MOVE_PCT", "0.08") or "0.08"
)  # need +8% within hold window or time-stop
MONEY_RISK_PCT_HINT = float(os.getenv("MONEY_RISK_PCT_HINT", "1.0") or "1.0")  # bankroll %
MONEY_INVALIDATE_INTERVAL_SEC = float(
    os.getenv("MONEY_INVALIDATE_INTERVAL_SEC", "60") or "60"
)
# Auto-open journal when Telegram fires. Default OFF — use Money desk "I took this".
_auto_j = (os.getenv("MONEY_AUTO_JOURNAL", "0") or "0").strip().lower()
MONEY_AUTO_JOURNAL = _auto_j not in ("0", "false", "no", "off")
# Allow Take/Skip without Admin key (private desk). Set 0 to require X-Admin-Key.
_money_open = (os.getenv("MONEY_OPEN_MANAGE", "1") or "1").strip().lower()
MONEY_OPEN_MANAGE = _money_open not in ("0", "false", "no", "off")
MONEY_PAPER_DEFAULT = (os.getenv("MONEY_PAPER_DEFAULT", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# --- Complete money system (bankroll / session / sizing) ---
# Bankroll is the reference equity for risk % (paper or real). Update as you grow.
BANKROLL_USD = float(os.getenv("BANKROLL_USD", "500") or "500")
# Max risk per trade as % of bankroll (1.0 = 1%)
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0") or "1.0")
# Hard daily stop: halt new alerts after this much R lost today
MAX_DAILY_LOSS_R = float(os.getenv("MAX_DAILY_LOSS_R", "3.0") or "3.0")
# Optional profit lock: after +N R day, only SNIPE or pause (soft)
MAX_DAILY_PROFIT_R = float(os.getenv("MAX_DAILY_PROFIT_R", "6.0") or "6.0")
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "2") or "2")
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "6") or "6")
# SOL price for size display (override when you want accuracy)
SOL_USD = float(os.getenv("SOL_USD", "150") or "150")
# After TP1, trail stop to breakeven (entry) then optional trail %
TRAIL_AFTER_TP1 = (os.getenv("TRAIL_AFTER_TP1", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# Position manager poll (seconds)
POSITION_MANAGER_INTERVAL_SEC = float(
    os.getenv("POSITION_MANAGER_INTERVAL_SEC", "45") or "45"
)
# Daily session report hour UTC (send once per day when hour matches)
MONEY_DAILY_REPORT_UTC_HOUR = int(os.getenv("MONEY_DAILY_REPORT_UTC_HOUR", "0") or "0")
# System armed — when 0, scan only, no new money alerts
MONEY_SYSTEM_ARMED = (os.getenv("MONEY_SYSTEM_ARMED", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# Money alerts: require mint+freeze revoked (fail-closed on present or n/a)
MONEY_REQUIRE_CONTROL_SURFACE = (
    os.getenv("MONEY_REQUIRE_CONTROL_SURFACE", "1") or "1"
).strip().lower() not in ("0", "false", "no", "off")
# Attach Lab cockpit facts to every money Telegram alert + archive snapshot
MONEY_AUTO_LAB = (os.getenv("MONEY_AUTO_LAB", "1") or "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)


def _solana_rpc_http() -> str:
    explicit = (
        os.getenv("SOLANA_RPC_HTTP") or os.getenv("SOLANA_RPC_URL") or ""
    ).strip()
    if explicit:
        return explicit
    if HELIUS_API_KEY:
        return f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    return "https://api.mainnet-beta.solana.com"


def _solana_rpc_wss() -> str:
    explicit = (
        os.getenv("SOLANA_RPC_WSS") or os.getenv("YELLOWSTONE_WSS") or ""
    ).strip()
    if explicit:
        return explicit
    if HELIUS_API_KEY:
        return f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    return "wss://api.mainnet-beta.solana.com"


SOLANA_RPC_HTTP = _solana_rpc_http()
SOLANA_RPC_WSS = _solana_rpc_wss()


def rpc_is_paid() -> bool:
    """True when HELIUS key or non-public RPC endpoint is configured."""
    if HELIUS_API_KEY:
        return True
    u = (SOLANA_RPC_HTTP + " " + SOLANA_RPC_WSS).lower()
    if "api-key=" in u or "apikey=" in u:
        return True
    public = ("api.mainnet-beta.solana.com",)
    return not any(p in u for p in public)


def rpc_provider_label() -> str:
    if HELIUS_API_KEY or "helius" in SOLANA_RPC_HTTP.lower():
        return "helius"
    if rpc_is_paid():
        return "paid"
    return "public"