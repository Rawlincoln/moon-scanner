"""Trade journal — open alerts, invalidations, closed P&L for real EV.

SQLite under DATA_DIR so free-tier can still restore via export if needed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR, MONEY_PAPER_DEFAULT
from services.money_plan import build_money_plan, check_invalidation, classify_exit

# Share parent dir with other durable DBs
_DEFAULT_DB = Path(DATA_DIR) / "trade_journal.db"

_lock = threading.Lock()
_default: "TradeJournal | None" = None


class TradeJournal:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path or _DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with _lock:
            conn = self._conn()
            try:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=30000")
                except Exception:
                    pass
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mint TEXT NOT NULL,
                        symbol TEXT,
                        name TEXT,
                        feed TEXT NOT NULL,
                        label TEXT,
                        paper INTEGER DEFAULT 1,
                        status TEXT NOT NULL DEFAULT 'open',
                        opened_at REAL NOT NULL,
                        closed_at REAL,
                        entry_mcap REAL,
                        exit_mcap REAL,
                        peak_mcap REAL,
                        plan_json TEXT,
                        outcome TEXT,
                        multiple REAL,
                        r_multiple REAL,
                        notes TEXT,
                        invalid_reason TEXT,
                        alert_sent INTEGER DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS idx_trades_mint ON trades(mint);
                    CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
                    CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def db_path(self) -> str:
        return str(self.path)

    def open_from_alert(
        self,
        kind: str,
        token: dict[str, Any],
        *,
        paper: bool | None = None,
        alert_sent: bool = True,
    ) -> int | None:
        """Record a new open trade when Telegram fires (dedupe 45m same feed+mint)."""
        mint = str(token.get("tokenAddress") or token.get("mint") or "").strip()
        if not mint:
            return None
        plan = build_money_plan(kind, token)
        entry = plan.get("entry_mcap") or 0
        if not entry:
            return None
        now = time.time()
        is_paper = MONEY_PAPER_DEFAULT if paper is None else bool(paper)
        label = (
            token.get("moon_label")
            or token.get("snipe_label")
            or token.get("heat_label")
            or token.get("grad_label")
            or ""
        )
        with _lock:
            conn = self._conn()
            try:
                recent = conn.execute(
                    """
                    SELECT id FROM trades
                    WHERE mint=? AND feed=? AND opened_at > ? AND status IN ('open','invalid')
                    ORDER BY opened_at DESC LIMIT 1
                    """,
                    (mint, kind.lower(), now - 45 * 60),
                ).fetchone()
                if recent:
                    return int(recent["id"])
                cur = conn.execute(
                    """
                    INSERT INTO trades (
                        mint, symbol, name, feed, label, paper, status,
                        opened_at, entry_mcap, peak_mcap, plan_json, alert_sent
                    ) VALUES (?,?,?,?,?,?, 'open', ?,?,?,?,?)
                    """,
                    (
                        mint,
                        token.get("symbol") or "",
                        token.get("name") or "",
                        kind.lower(),
                        str(label).upper(),
                        1 if is_paper else 0,
                        now,
                        float(entry),
                        float(entry),
                        json.dumps(plan),
                        1 if alert_sent else 0,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()

    def active_open(self) -> list[dict[str, Any]]:
        with _lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE status='open' ORDER BY opened_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get(self, trade_id: int) -> dict[str, Any] | None:
        with _lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM trades WHERE id=?", (int(trade_id),)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def apply_mcap(self, trade_id: int, mcap: float) -> dict[str, Any] | None:
        """Update peak; if invalid rules hit → mark invalid. Returns trade or None."""
        if mcap is None or mcap <= 0:
            return None
        with _lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM trades WHERE id=? AND status='open'",
                    (int(trade_id),),
                ).fetchone()
                if not row:
                    return None
                plan = {}
                try:
                    plan = json.loads(row["plan_json"] or "{}")
                except Exception:
                    plan = {"entry_mcap": row["entry_mcap"]}
                peak = max(float(row["peak_mcap"] or 0), float(mcap))
                age_min = (time.time() - float(row["opened_at"])) / 60.0
                invalid, reason = check_invalidation(
                    plan, current_mcap=float(mcap), alert_age_min=age_min
                )
                if invalid:
                    cls = classify_exit(plan, exit_mcap=float(mcap), peak_mcap=peak)
                    conn.execute(
                        """
                        UPDATE trades SET
                            peak_mcap=?, exit_mcap=?, closed_at=?, status='invalid',
                            outcome=?, multiple=?, r_multiple=?, invalid_reason=?
                        WHERE id=?
                        """,
                        (
                            peak,
                            float(mcap),
                            time.time(),
                            cls.get("outcome") or "invalid",
                            cls.get("multiple"),
                            cls.get("r_multiple"),
                            (reason or "")[:300],
                            int(trade_id),
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE trades SET peak_mcap=? WHERE id=?",
                        (peak, int(trade_id)),
                    )
                conn.commit()
                row2 = conn.execute(
                    "SELECT * FROM trades WHERE id=?", (int(trade_id),)
                ).fetchone()
                return dict(row2) if row2 else None
            finally:
                conn.close()

    def close_trade(
        self,
        trade_id: int,
        *,
        exit_mcap: float,
        notes: str = "",
        force_outcome: str | None = None,
    ) -> dict[str, Any] | None:
        with _lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM trades WHERE id=?", (int(trade_id),)
                ).fetchone()
                if not row:
                    return None
                if row["status"] not in ("open", "invalid"):
                    return dict(row)
                plan = {}
                try:
                    plan = json.loads(row["plan_json"] or "{}")
                except Exception:
                    plan = {"entry_mcap": row["entry_mcap"]}
                peak = max(float(row["peak_mcap"] or 0), float(exit_mcap))
                cls = classify_exit(
                    plan, exit_mcap=float(exit_mcap), peak_mcap=peak
                )
                outcome = force_outcome or cls.get("outcome")
                conn.execute(
                    """
                    UPDATE trades SET
                        status='closed', closed_at=?, exit_mcap=?, peak_mcap=?,
                        outcome=?, multiple=?, r_multiple=?, notes=?
                    WHERE id=?
                    """,
                    (
                        time.time(),
                        float(exit_mcap),
                        peak,
                        outcome,
                        cls.get("multiple"),
                        cls.get("r_multiple"),
                        (notes or "")[:500],
                        int(trade_id),
                    ),
                )
                conn.commit()
                row2 = conn.execute(
                    "SELECT * FROM trades WHERE id=?", (int(trade_id),)
                ).fetchone()
                return dict(row2) if row2 else None
            finally:
                conn.close()

    def list_trades(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with _lock:
            conn = self._conn()
            try:
                if status:
                    rows = conn.execute(
                        """
                        SELECT * FROM trades WHERE status=?
                        ORDER BY opened_at DESC LIMIT ?
                        """,
                        (status, int(limit)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?",
                        (int(limit),),
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def summary(self) -> dict[str, Any]:
        with _lock:
            conn = self._conn()
            try:
                total = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
                open_n = conn.execute(
                    "SELECT COUNT(*) AS n FROM trades WHERE status='open'"
                ).fetchone()["n"]
                closed = conn.execute(
                    """
                    SELECT outcome, COUNT(*) AS n, AVG(multiple) AS avg_m, AVG(r_multiple) AS avg_r
                    FROM trades
                    WHERE status IN ('closed','invalid') AND outcome IS NOT NULL
                    GROUP BY outcome
                    """
                ).fetchall()
                by_out = {
                    r["outcome"]: {
                        "n": int(r["n"]),
                        "avg_multiple": round(float(r["avg_m"] or 0), 3),
                        "avg_r": round(float(r["avg_r"] or 0), 2)
                        if r["avg_r"] is not None
                        else None,
                    }
                    for r in closed
                }
                done = conn.execute(
                    """
                    SELECT multiple, r_multiple, outcome, feed, paper
                    FROM trades WHERE status IN ('closed','invalid') AND multiple IS NOT NULL
                    """
                ).fetchall()
                wins = 0
                losses = 0
                r_sum = 0.0
                r_n = 0
                mults: list[float] = []
                for r in done:
                    m = float(r["multiple"] or 0)
                    mults.append(m)
                    oc = str(r["outcome"] or "")
                    if oc in ("tp1", "tp2", "win_small") or m >= 1.15:
                        wins += 1
                    elif oc in ("stop", "loss", "invalid") or m < 0.95:
                        losses += 1
                    if r["r_multiple"] is not None:
                        r_sum += float(r["r_multiple"])
                        r_n += 1
                n_done = len(done)
                expect = (sum(mults) / n_done) if n_done else None
                return {
                    "total": total,
                    "open": open_n,
                    "closed_or_invalid": n_done,
                    "by_outcome": by_out,
                    "wins": wins,
                    "losses": losses,
                    "win_rate_pct": round(100 * wins / n_done, 1) if n_done else None,
                    "avg_multiple": round(expect, 3) if expect else None,
                    "expectancy_r": round(r_sum / r_n, 2) if r_n else None,
                    "sample_n": n_done,
                    "paper_default": MONEY_PAPER_DEFAULT,
                    "db_path": str(self.path),
                    "note": (
                        "Paper until you close trades with real exit_mcap. "
                        "Positive expectancy_r over ≥20 samples before sizing up."
                    ),
                }
            finally:
                conn.close()


def get_journal(db_path: Path | str | None = None) -> TradeJournal:
    global _default
    if db_path is not None:
        return TradeJournal(db_path)
    if _default is None:
        _default = TradeJournal()
    return _default


def reset_journal_singleton() -> None:
    global _default
    _default = None
