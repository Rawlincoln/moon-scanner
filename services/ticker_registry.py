"""Ticker uniqueness — novel symbols vs reused copycats.

A ticker never (or rarely) seen before is a mild positive for snipe/moon
(fresh brand). Heavily reused tickers (ELON/TRUMP/PEPE clones, farm
re-launches) are often sniper/name-jack bait.

Persistence: DATA_DIR/ticker_registry.db (same free-tier limits as other DBs).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR

# Always-reused meta tickers (not "unique" even if first in our DB)
try:
    from services.social_signals import HOT_TICKERS
except Exception:
    HOT_TICKERS = {}

_DEFAULT = Path(DATA_DIR) / "ticker_registry.db"
_lock = threading.Lock()
_default: "TickerRegistry | None" = None

_SYM_RE = re.compile(r"^[A-Za-z0-9$]{1,20}$")


def normalize_symbol(symbol: str | None) -> str:
    s = (symbol or "").strip().upper()
    if s.startswith("$"):
        s = s[1:]
    # strip zero-width / odd unicode noise
    s = "".join(ch for ch in s if ch.isalnum())
    return s[:20]


class TickerRegistry:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path or _DEFAULT)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        with _lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS tickers (
                        symbol TEXT NOT NULL,
                        mint TEXT NOT NULL,
                        first_seen REAL NOT NULL,
                        last_seen REAL NOT NULL,
                        name TEXT,
                        PRIMARY KEY (symbol, mint)
                    );
                    CREATE INDEX IF NOT EXISTS idx_tickers_sym ON tickers(symbol);
                    CREATE INDEX IF NOT EXISTS idx_tickers_mint ON tickers(mint);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def record(
        self,
        symbol: str,
        mint: str,
        *,
        name: str = "",
    ) -> dict[str, Any]:
        """Record sighting; return uniqueness analysis after upsert."""
        sym = normalize_symbol(symbol)
        mint = (mint or "").strip()
        if not sym or not mint or len(mint) < 20:
            return analyze_ticker_uniqueness(symbol, mint, prior_mints=0)
        now = time.time()
        with _lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT first_seen FROM tickers WHERE symbol=? AND mint=?",
                    (sym, mint),
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE tickers SET last_seen=?, name=? WHERE symbol=? AND mint=?",
                        (now, (name or "")[:80], sym, mint),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO tickers (symbol, mint, first_seen, last_seen, name)
                        VALUES (?,?,?,?,?)
                        """,
                        (sym, mint, now, now, (name or "")[:80]),
                    )
                conn.commit()
                # distinct mints for this symbol
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM tickers WHERE symbol=?",
                    (sym,),
                ).fetchone()["n"]
                others = conn.execute(
                    """
                    SELECT mint FROM tickers
                    WHERE symbol=? AND mint!=?
                    ORDER BY first_seen ASC LIMIT 5
                    """,
                    (sym, mint),
                ).fetchall()
                other_mints = [r["mint"] for r in others]
            finally:
                conn.close()
        return analyze_ticker_uniqueness(
            sym,
            mint,
            prior_mints=max(0, int(n) - 1),
            other_mints=other_mints,
            distinct_mints=int(n),
        )

    def lookup(self, symbol: str, mint: str = "") -> dict[str, Any]:
        sym = normalize_symbol(symbol)
        mint = (mint or "").strip()
        with _lock:
            conn = self._conn()
            try:
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM tickers WHERE symbol=?",
                    (sym,),
                ).fetchone()["n"]
                others = []
                if mint:
                    rows = conn.execute(
                        """
                        SELECT mint FROM tickers
                        WHERE symbol=? AND mint!=?
                        ORDER BY first_seen LIMIT 5
                        """,
                        (sym, mint),
                    ).fetchall()
                    others = [r["mint"] for r in rows]
                    # if this mint not yet recorded, prior = all n
                    has = conn.execute(
                        "SELECT 1 FROM tickers WHERE symbol=? AND mint=?",
                        (sym, mint),
                    ).fetchone()
                    prior = int(n) if not has else max(0, int(n) - 1)
                    distinct = int(n) + (0 if has else 1)
                else:
                    prior = int(n)
                    distinct = int(n)
            finally:
                conn.close()
        return analyze_ticker_uniqueness(
            sym,
            mint,
            prior_mints=prior,
            other_mints=others,
            distinct_mints=distinct,
        )


def analyze_ticker_uniqueness(
    symbol: str,
    mint: str = "",
    *,
    prior_mints: int = 0,
    other_mints: list[str] | None = None,
    distinct_mints: int | None = None,
) -> dict[str, Any]:
    """Classify ticker novelty for snipe/moon scoring.

    unique: first time we see this symbol (and not global hot meta)
    reused: 2+ mints share ticker
    hot_meta: ELON/TRUMP/etc — narrative play, not unique brand
    junk: garbage symbols
    """
    sym = normalize_symbol(symbol)
    other_mints = other_mints or []
    distinct = distinct_mints if distinct_mints is not None else prior_mints + (
        1 if mint else 0
    )

    is_hot = sym in {k.upper() for k in HOT_TICKERS.keys()}
    junk = bool(
        not sym
        or len(sym) < 2
        or len(sym) > 12
        or re.match(
            r"^(test|asd|qwe|zzz|xxx|aaa|bbb|ccc|null|token|coin|moon|rug|scam)$",
            sym,
            re.I,
        )
    )

    status = "unknown"
    score_boost = 0  # moon composite soft
    priority_boost = 0.0
    risk = "low"
    reasons: list[str] = []
    flags: list[str] = []

    if junk:
        status = "junk"
        risk = "medium"
        score_boost = -8
        flags.append("junk_ticker")
        reasons.append(f"Junk/gibberish ticker ${sym or '?'}")
    elif is_hot:
        status = "hot_meta"
        # Not unique — copycat risk unless real narrative edge elsewhere
        risk = "medium"
        score_boost = -2  # slight demote; narrative edge handled separately
        flags.append("hot_meta_ticker")
        reasons.append(
            f"${sym} is a heavily reused meta ticker — not a unique brand"
        )
        if prior_mints >= 1:
            score_boost = -6
            risk = "high"
            flags.append("reused_hot_ticker")
            reasons.append(
                f"${sym} seen on {prior_mints}+ other mint(s) — name-jack/snipe farm risk"
            )
    elif prior_mints >= 5:
        status = "heavily_reused"
        risk = "high"
        score_boost = -10
        priority_boost = -8
        flags.append("heavily_reused_ticker")
        reasons.append(
            f"${sym} reused across {prior_mints}+ mints — serial relaunch/copycat"
        )
    elif prior_mints >= 2:
        status = "reused"
        risk = "medium"
        score_boost = -5
        priority_boost = -4
        flags.append("reused_ticker")
        reasons.append(f"${sym} already used on {prior_mints} other mint(s)")
    elif prior_mints == 1:
        status = "once_reused"
        risk = "medium"
        score_boost = -2
        flags.append("once_reused_ticker")
        reasons.append(f"${sym} seen on 1 other mint — possible relaunch")
    else:
        # Novel in our registry
        status = "unique"
        risk = "low"
        score_boost = 5
        priority_boost = 8
        flags.append("unique_ticker")
        reasons.append(
            f"${sym} looks unique (first mint in scanner registry) — mild snipe/moon brand signal"
        )
        # Extra boost for clean brand-like length 3–8 alpha
        if 3 <= len(sym) <= 8 and sym.isalpha():
            score_boost = 7
            priority_boost = 11
            flags.append("clean_unique_brand")
            reasons.append(f"Clean unique brand ticker ${sym}")

    return {
        "symbol": sym,
        "mint": mint,
        "status": status,
        "unique": status == "unique",
        "prior_mints": prior_mints,
        "distinct_mints": distinct,
        "other_mints": other_mints[:5],
        "is_hot_meta": is_hot,
        "junk": junk,
        "risk": risk,
        "flags": flags,
        "reasons": reasons,
        "score_boost": score_boost,
        "priority_boost": priority_boost,
        "summary": reasons[0] if reasons else f"${sym}",
    }


def get_ticker_registry(db_path: Path | str | None = None) -> TickerRegistry:
    global _default
    if db_path is not None:
        return TickerRegistry(db_path)
    if _default is None:
        _default = TickerRegistry()
    return _default


def attach_ticker_uniqueness(token: dict[str, Any], *, record: bool = True) -> dict[str, Any]:
    """Analyze (+ optionally record) ticker uniqueness on a feed card."""
    sym = token.get("symbol") or (token.get("pumpfun") or {}).get("symbol") or ""
    mint = str(token.get("tokenAddress") or token.get("mint") or "").strip()
    name = token.get("name") or (token.get("pumpfun") or {}).get("name") or ""
    reg = get_ticker_registry()
    if record and mint:
        info = reg.record(sym, mint, name=str(name))
    else:
        info = reg.lookup(sym, mint)
    token["tickerUniqueness"] = info
    return info


def ticker_score_boost(token: dict[str, Any]) -> int:
    tu = token.get("tickerUniqueness")
    if not isinstance(tu, dict):
        try:
            tu = attach_ticker_uniqueness(token, record=False)
        except Exception:
            return 0
    try:
        return int(tu.get("score_boost") or 0)
    except (TypeError, ValueError):
        return 0


def ticker_priority_boost(token: dict[str, Any]) -> float:
    tu = token.get("tickerUniqueness")
    if not isinstance(tu, dict):
        return 0.0
    try:
        return float(tu.get("priority_boost") or 0)
    except (TypeError, ValueError):
        return 0.0
