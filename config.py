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
REQUEST_TIMEOUT = 20.0

# Early-entry mode (real-time fresh launches)
PUMPFUN_API_URL = "https://frontend-api-v3.pump.fun"
PADRE_API_URL = "https://backend.padre.gg"
PADRE_TRADE_URL = "https://trade.padre.gg"
DEFAULT_MAX_AGE_MINUTES = 30
MAX_AGE_MINUTES_CAP = 180
CACHE_TTL = 15
TRENCHES_CACHE_TTL = 300 if IS_PRODUCTION else 90
# Background scan off on Render until service is stable (enable after first deploy)
BACKGROUND_SCAN_INTERVAL_SEC = 0
BACKGROUND_SCAN_PER_COLUMN = 0
TRENCHES_CONCURRENCY = 4 if IS_PRODUCTION else 12
EXCLUDE_GRADUATED_DEFAULT = True
EARLY_MCAP_MIN_USD = 1_500
EARLY_MCAP_MAX_USD = 65_000

# Pro trencher — only recommend tokens approaching $6k with real momentum
TARGET_MCAP_USD = 6_000
MCAP_INVEST_MIN_USD = 4_000
MCAP_INVEST_MAX_USD = 7_500
MIN_SURVIVAL_AGE_MINUTES = 2.0
MIN_TOKEN_HOLDERS = 12
MIN_DEX_VOL_M5_USD = 600
MIN_DEX_BUYS_M5 = 8
MIN_BUY_SELL_RATIO_M5 = 1.05
MIN_PRICE_CHANGE_M5 = 0.0
MAX_SNIPER_WALLET_PCT = 22.0
MAX_DEV_HOLD_PCT = 8.0
MIN_PUMPFUN_REPLIES = 1
REQUIRE_TRENCH_GATE_FOR_INVEST = True