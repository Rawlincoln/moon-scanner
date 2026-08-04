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
from services.capital import enrich_plan_with_size
from services.money_plan import build_money_plan, check_invalidation, classify_exit

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
                        last_mcap REAL,
                        plan_json TEXT,
                        mgmt_json TEXT,
                        outcome TEXT,
                        multiple REAL,
                        r_multiple REAL,
                        pnl_usd REAL,
                        size_usd REAL,
                        risk_usd REAL,
                        size_sol REAL,
                        bankroll_usd REAL,
                        notes TEXT,
                        invalid_reason TEXT,
                        alert_sent INTEGER DEFAULT 0
                    );
                    CREATE INDEX IF NOT EXISTS idx_trades_mint ON trades(mint);
                    CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
                    CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_at);
                    """
                )
                cols = {
                    r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()
                }
                for col, typ in (
                    ("last_mcap", "REAL"),
                    ("mgmt_json", "TEXT"),
                    ("pnl_usd", "REAL"),
                    ("size_usd", "REAL"),
                    ("risk_usd", "REAL"),
                    ("size_sol", "REAL"),
                    ("bankroll_usd", "REAL"),
                ):
                    if col not in cols:
                        try:
                            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
                        except Exception:
                            pass
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
        plan: dict[str, Any] | None = None,
    ) -> int | None:
        """Record a new open trade when Telegram fires (dedupe 45m same feed+mint)."""
        mint = str(token.get("tokenAddress") or token.get("mint") or "").strip()
        if not mint:
            return None
        plan = plan or enrich_plan_with_size(kind, token)
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
        sizing = plan.get("sizing") or {}
        with _lock:
            conn = self._conn()
            try:
                recent = conn.execute(
                    """
                    SELECT id FROM trades
                    WHERE mint=? AND feed=? AND opened_at > ? AND status='open'
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
                        opened_at, entry_mcap, peak_mcap, last_mcap, plan_json, mgmt_json,
                        alert_sent, size_usd, risk_usd, size_sol, bankroll_usd
                    ) VALUES (?,?,?,?,?,?, 'open', ?,?,?,?,?,?,?,?,?,?,?)
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
                        float(entry),
                        json.dumps(plan),
                        json.dumps({}),
                        1 if alert_sent else 0,
                        float(sizing.get("size_usd") or plan.get("size_usd") or 0)
                        or None,
                        float(sizing.get("risk_usd") or plan.get("risk_usd") or 0)
                        or None,
                        float(sizing.get("size_sol") or 0) or None,
                        float(sizing.get("bankroll_usd") or 0) or None,
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

    def update_management(
        self,
        trade_id: int,
        *,
        peak_mcap: float | None = None,
        last_mcap: float | None = None,
        mgmt: dict[str, Any] | None = None,
    ) -> None:
        with _lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM trades WHERE id=?", (int(trade_id),)
                ).fetchone()
                if not row or row["status"] != "open":
                    return
                peak = (
                    max(float(row["peak_mcap"] or 0), float(peak_mcap))
                    if peak_mcap is not None
                    else row["peak_mcap"]
                )
                last = last_mcap if last_mcap is not None else row["last_mcap"]
                mgmt_s = (
                    json.dumps(mgmt)
                    if mgmt is not None
                    else row["mgmt_json"]
                )
                conn.execute(
                    """
                    UPDATE trades SET peak_mcap=?, last_mcap=?, mgmt_json=?
                    WHERE id=?
                    """,
                    (peak, last, mgmt_s, int(trade_id)),
                )
                conn.commit()
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
                    pnl = self._pnl_usd(row, float(mcap), cls)
                    conn.execute(
                        """
                        UPDATE trades SET
                            peak_mcap=?, last_mcap=?, exit_mcap=?, closed_at=?,
                            status='invalid', outcome=?, multiple=?, r_multiple=?,
                            pnl_usd=?, invalid_reason=?
                        WHERE id=?
                        """,
                        (
                            peak,
                            float(mcap),
                            float(mcap),
                            time.time(),
                            cls.get("outcome") or "invalid",
                            cls.get("multiple"),
                            cls.get("r_multiple"),
                            pnl,
                            (reason or "")[:300],
                            int(trade_id),
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE trades SET peak_mcap=?, last_mcap=? WHERE id=?",
                        (peak, float(mcap), int(trade_id)),
                    )
                conn.commit()
                row2 = conn.execute(
                    "SELECT * FROM trades WHERE id=?", (int(trade_id),)
                ).fetchone()
                return dict(row2) if row2 else None
            finally:
                conn.close()

    @staticmethod
    def _pnl_usd(row: Any, exit_mcap: float, cls: dict[str, Any]) -> float | None:
        try:
            size = float(row["size_usd"] or 0)
            entry = float(row["entry_mcap"] or 0)
            if size <= 0 or entry <= 0:
                # use R * risk
                risk = float(row["risk_usd"] or 0)
                r = cls.get("r_multiple")
                if risk and r is not None:
                    return round(risk * float(r), 2)
                return None
            mult = exit_mcap / entry
            return round(size * (mult - 1.0), 2)
        except Exception:
            return None

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
                # Adjust R for partial TP1 then exit
                try:
                    mgmt = json.loads(row["mgmt_json"] or "{}")
                except Exception:
                    mgmt = {}
                r_mult = cls.get("r_multiple")
                if mgmt.get("tp1_hit") and outcome in ("tp2", "be_stop", "stop"):
                    # approximate: half at +50% (~+2.8R if 18% stop), half at final
                    stop_pct = float(plan.get("stop_pct") or 18) / 100.0 or 0.18
                    r_tp1 = 0.50 / stop_pct  # +50% on half
                    r_rest = (
                        (float(exit_mcap) / float(row["entry_mcap"] or 1) - 1.0)
                        / stop_pct
                    )
                    r_mult = round(0.5 * r_tp1 + 0.5 * r_rest, 2)
                pnl = self._pnl_usd(row, float(exit_mcap), {**cls, "r_multiple": r_mult})
                conn.execute(
                    """
                    UPDATE trades SET
                        status='closed', closed_at=?, exit_mcap=?, peak_mcap=?,
                        last_mcap=?, outcome=?, multiple=?, r_multiple=?,
                        pnl_usd=?, notes=?
                    WHERE id=?
                    """,
                    (
                        time.time(),
                        float(exit_mcap),
                        peak,
                        float(exit_mcap),
                        outcome,
                        cls.get("multiple"),
                        r_mult,
                        pnl,
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
                    SELECT outcome, COUNT(*) AS n, AVG(multiple) AS avg_m,
                           AVG(r_multiple) AS avg_r, SUM(pnl_usd) AS sum_pnl
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
                        "sum_pnl_usd": round(float(r["sum_pnl"] or 0), 2)
                        if r["sum_pnl"] is not None
                        else None,
                    }
                    for r in closed
                }
                done = conn.execute(
                    """
                    SELECT multiple, r_multiple, outcome, feed, paper, pnl_usd
                    FROM trades WHERE status IN ('closed','invalid') AND multiple IS NOT NULL
                    """
                ).fetchall()
                r_sum = 0.0
                r_n = 0
                pnl_sum = 0.0
                mults: list[float] = []
                for r in done:
                    mults.append(float(r["multiple"] or 0))
                    if r["r_multiple"] is not None:
                        r_sum += float(r["r_multiple"])
                        r_n += 1
                    if r["pnl_usd"] is not None:
                        pnl_sum += float(r["pnl_usd"])
                n_done = len(done)
                wins = sum(
                    1
                    for r in done
                    if str(r["outcome"] or "") in ("tp1", "tp2", "win_small")
                    or float(r["multiple"] or 0) >= 1.15
                )
                losses = sum(
                    1
                    for r in done
                    if str(r["outcome"] or "") in ("stop", "loss", "invalid")
                    or (
                        float(r["multiple"] or 0) < 0.95
                        and str(r["outcome"] or "")
                        not in ("tp1", "tp2", "win_small")
                    )
                )
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
                    "total_pnl_usd": round(pnl_sum, 2) if n_done else None,
                    "sample_n": n_done,
                    "paper_default": MONEY_PAPER_DEFAULT,
                    "db_path": str(self.path),
                    "note": (
                        "Paper until expectancy_r > 0 over ≥20 samples. "
                        "Then size real with BANKROLL_USD + RISK_PER_TRADE_PCT."
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
