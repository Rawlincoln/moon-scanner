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
# Skip DexScreener smart-money order fetch during bulk trenches (saves ~0.5–1s/token)
FAST_SCAN_SKIP_DEX_ORDERS = True

# $6k entry radar — catch climbers BEFORE they leave the entry zone
# (user missed GLourz… at $6k; scanner only saw it later at $30k+)
SIXK_RADAR_MIN_USD = 2_000
SIXK_RADAR_MAX_USD = 9_000
SIXK_ENTRY_SWEET_MIN = 3_500
SIXK_ENTRY_SWEET_MAX = 7_500
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

# Pro trencher — early band still uses ~$6k; migration track uses bonding %
TARGET_MCAP_USD = 6_000
MCAP_INVEST_MIN_USD = 3_500  # start flagging earlier than $4k
MCAP_INVEST_MAX_USD = 8_500  # still "entry" slightly past 6k
# Under-$25k "structure" band (between lottery and near-migration)
UNDER25K_MIN_USD = 8_000
UNDER25K_MAX_USD = 25_000
MIN_SURVIVAL_AGE_MINUTES = 0.75
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

# Admin / security
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
# Comma-separated origins; empty = safe defaults (local hosts or known Render URLs)
_cors_raw = os.getenv("CORS_ORIGINS", "").strip()
CORS_ORIGINS_LIST: list[str] = (
    [o.strip() for o in _cors_raw.split(",") if o.strip()] if _cors_raw else []
)
# Expensive API routes — per IP sliding windows
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30") or "30")
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "8") or "8")  # short 10s window
# Stricter for deep analyze / checkers (third-party amplification)
RATE_LIMIT_ANALYZE_PER_MIN = int(os.getenv("RATE_LIMIT_ANALYZE_PER_MIN", "12") or "12")
# force=true costs this many tokens (anti-amp)
RATE_LIMIT_FORCE_COST = int(os.getenv("RATE_LIMIT_FORCE_COST", "4") or "4")
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
_tg_feeds = os.getenv("TELEGRAM_ALERT_FEEDS", "moon,snipe,heat").strip()
TELEGRAM_ALERT_FEEDS: list[str] = [
    f.strip().lower() for f in _tg_feeds.split(",") if f.strip()
] or ["moon", "snipe", "heat"]
TELEGRAM_ALERT_INTERVAL_SEC = float(
    os.getenv("TELEGRAM_ALERT_INTERVAL_SEC", "45") or "45"
)
TELEGRAM_ALERT_DEDUPE_SEC = float(
    os.getenv("TELEGRAM_ALERT_DEDUPE_SEC", str(45 * 60)) or str(45 * 60)
)
TELEGRAM_ALERT_MAX_PER_CYCLE = int(
    os.getenv("TELEGRAM_ALERT_MAX_PER_CYCLE", "6") or "6"
)
TELEGRAM_ALERT_MOON_LABELS = {
    x.strip().upper()
    for x in (os.getenv("TELEGRAM_ALERT_MOON_LABELS", "MOON,WATCH") or "MOON,WATCH").split(
        ","
    )
    if x.strip()
}
TELEGRAM_ALERT_SNIPE_LABELS = {
    x.strip().upper()
    for x in (
        os.getenv("TELEGRAM_ALERT_SNIPE_LABELS", "SNIPE,SETUP") or "SNIPE,SETUP"
    ).split(",")
    if x.strip()
}
TELEGRAM_ALERT_HEAT_LABELS = {
    x.strip().upper()
    for x in (
        os.getenv("TELEGRAM_ALERT_HEAT_LABELS", "HEAT,WARM") or "HEAT,WARM"
    ).split(",")
    if x.strip()
}
# Optional shared secret for GET /api/alerts/telegram/tick (external cron 24/7)
TELEGRAM_CRON_SECRET = os.getenv("TELEGRAM_CRON_SECRET", "").strip()


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