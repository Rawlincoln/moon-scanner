"""Configuration and chain mappings for Moon Scanner."""

import os

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
REQUEST_TIMEOUT = 12.0  # fail fast — don't wait on slow rugcheck/padre

# Early-entry mode (real-time fresh launches)
PUMPFUN_API_URL = "https://frontend-api-v3.pump.fun"
PADRE_API_URL = "https://backend.padre.gg"
PADRE_TRADE_URL = "https://trade.padre.gg"
DEFAULT_MAX_AGE_MINUTES = 15  # tighter window = catch tokens earlier
MAX_AGE_MINUTES_CAP = 180
CACHE_TTL = 10
# Short cache so we don't re-show tokens that already pumped
# Keep cache short on both local + Render so scans stay aligned with live market
TRENCHES_CACHE_TTL = 15
TRENCHES_CONCURRENCY = 12 if IS_PRODUCTION else 24
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
MIGRATION_NEAR_MIN_PCT = 40.0  # ~$28k+ — primary "can migrate" zone
MIGRATION_ALMOST_MIN_PCT = 55.0  # ~$38k+ — almost bonded
# Skip DexScreener smart-money order fetch during bulk trenches (saves ~0.5–1s/token)
FAST_SCAN_SKIP_DEX_ORDERS = True

# $6k entry radar — catch climbers BEFORE they leave the entry zone
# (user missed GLourz… at $6k; scanner only saw it later at $30k+)
SIXK_RADAR_MIN_USD = 2_000
SIXK_RADAR_MAX_USD = 9_000
SIXK_ENTRY_SWEET_MIN = 3_500
SIXK_ENTRY_SWEET_MAX = 7_500
# Continuous local warm of the $6k band so UI isn't minutes behind
BACKGROUND_SCAN_INTERVAL_SEC = 18
BACKGROUND_SCAN_PER_COLUMN = 8

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