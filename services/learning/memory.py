"""SQLite store for token lifecycles + learned feature → outcome stats."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class LearningMemory:
    def __init__(self, db_path: Path | str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS tokens (
                        mint TEXT PRIMARY KEY,
                        name TEXT,
                        symbol TEXT,
                        first_seen REAL,
                        last_seen REAL,
                        first_mcap REAL,
                        ath_mcap REAL,
                        ath_ts REAL,
                        low_after_ath REAL,
                        creator_dump_mcap REAL,
                        creator_dump_ts REAL,
                        last_mcap REAL,
                        last_price REAL,
                        outcome TEXT,
                        outcome_ts REAL,
                        max_multiple REAL,
                        entry_features TEXT,
                        notes TEXT,
                        active INTEGER DEFAULT 1
                    );
                    CREATE TABLE IF NOT EXISTS snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mint TEXT,
                        ts REAL,
                        mcap REAL,
                        price REAL,
                        holders INTEGER,
                        creator_pct REAL,
                        creator_balance REAL,
                        quote_sol REAL,
                        buys_m5 INTEGER,
                        sells_m5 INTEGER,
                        replies INTEGER,
                        features TEXT,
                        FOREIGN KEY(mint) REFERENCES tokens(mint)
                    );
                    CREATE INDEX IF NOT EXISTS idx_snap_mint ON snapshots(mint);
                    CREATE TABLE IF NOT EXISTS feature_stats (
                        feature TEXT,
                        outcome TEXT,
                        count INTEGER DEFAULT 0,
                        sum_multiple REAL DEFAULT 0,
                        PRIMARY KEY(feature, outcome)
                    );
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def upsert_token(
        self,
        mint: str,
        *,
        name: str = "",
        symbol: str = "",
        mcap: float = 0.0,
        price: float = 0.0,
        features: dict | None = None,
        force_new_features: bool = False,
    ) -> None:
        now = time.time()
        feats_json = json.dumps(features or {})
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT mint, first_mcap, ath_mcap, entry_features FROM tokens WHERE mint=?",
                    (mint,),
                ).fetchone()
                if row is None:
                    conn.execute(
                        """
                        INSERT INTO tokens (
                            mint, name, symbol, first_seen, last_seen,
                            first_mcap, ath_mcap, ath_ts, low_after_ath,
                            last_mcap, last_price, entry_features, active
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)
                        """,
                        (
                            mint,
                            name,
                            symbol,
                            now,
                            now,
                            mcap,
                            mcap,
                            now if mcap > 0 else 0,
                            mcap,
                            mcap,
                            price,
                            feats_json,
                        ),
                    )
                else:
                    ath = float(row["ath_mcap"] or 0)
                    ath_ts = now if mcap > ath else None
                    low = mcap
                    if mcap > 0:
                        # update low_after_ath if past peak
                        pass
                    entry_f = row["entry_features"]
                    if force_new_features or not entry_f:
                        entry_f = feats_json
                    if mcap > ath:
                        conn.execute(
                            """
                            UPDATE tokens SET
                                name=COALESCE(NULLIF(?,''), name),
                                symbol=COALESCE(NULLIF(?,''), symbol),
                                last_seen=?, last_mcap=?, last_price=?,
                                ath_mcap=?, ath_ts=?, low_after_ath=?,
                                entry_features=?, active=1
                            WHERE mint=?
                            """,
                            (
                                name,
                                symbol,
                                now,
                                mcap,
                                price,
                                mcap,
                                now,
                                mcap,
                                entry_f,
                                mint,
                            ),
                        )
                    else:
                        # track drawdown low after ath
                        conn.execute(
                            """
                            UPDATE tokens SET
                                name=COALESCE(NULLIF(?,''), name),
                                symbol=COALESCE(NULLIF(?,''), symbol),
                                last_seen=?, last_mcap=?, last_price=?,
                                low_after_ath=CASE
                                    WHEN low_after_ath IS NULL OR low_after_ath=0 THEN ?
                                    WHEN ? < low_after_ath THEN ?
                                    ELSE low_after_ath
                                END,
                                entry_features=?,
                                active=1
                            WHERE mint=?
                            """,
                            (
                                name,
                                symbol,
                                now,
                                mcap,
                                price,
                                mcap,
                                mcap,
                                mcap,
                                entry_f,
                                mint,
                            ),
                        )
                conn.commit()
            finally:
                conn.close()

    def add_snapshot(
        self,
        mint: str,
        *,
        mcap: float,
        price: float = 0.0,
        holders: int = 0,
        creator_pct: float = 0.0,
        creator_balance: float = 0.0,
        quote_sol: float = 0.0,
        buys_m5: int = 0,
        sells_m5: int = 0,
        replies: int = 0,
        features: dict | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """
                    INSERT INTO snapshots (
                        mint, ts, mcap, price, holders, creator_pct,
                        creator_balance, quote_sol, buys_m5, sells_m5,
                        replies, features
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        mint,
                        now,
                        mcap,
                        price,
                        holders,
                        creator_pct,
                        creator_balance,
                        quote_sol,
                        buys_m5,
                        sells_m5,
                        replies,
                        json.dumps(features or {}),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_creator_dump(self, mint: str, mcap: float) -> None:
        now = time.time()
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT creator_dump_ts FROM tokens WHERE mint=?", (mint,)
                ).fetchone()
                if row and row["creator_dump_ts"]:
                    return
                conn.execute(
                    """
                    UPDATE tokens SET creator_dump_mcap=?, creator_dump_ts=?
                    WHERE mint=?
                    """,
                    (mcap, now, mint),
                )
                conn.commit()
            finally:
                conn.close()

    def finalize_outcome(
        self,
        mint: str,
        outcome: str,
        *,
        max_multiple: float = 0.0,
        notes: str = "",
        features: dict | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT outcome, entry_features, first_mcap, ath_mcap FROM tokens WHERE mint=?",
                    (mint,),
                ).fetchone()
                if not row:
                    return
                # Learn once; allow upgrades toward better outcomes (NEUTRAL→WINNER→MEGA)
                prev = row["outcome"]
                _rank = {
                    None: 0,
                    "": 0,
                    "NEUTRAL": 1,
                    "DUMP": 2,
                    "SCAM": 3,
                    "RUGGED": 3,
                    "RUNNER": 4,
                    "WINNER": 5,
                    "SUPER": 6,
                    "MEGA": 7,
                }
                if prev == outcome:
                    return
                # Don't downgrade MEGA/SUPER/WINNER to weaker labels
                if _rank.get(prev, 0) >= 5 and _rank.get(outcome, 0) < _rank.get(prev, 0):
                    return
                # Same-tier bad labels already final
                if prev in ("SCAM", "RUGGED", "DUMP") and outcome in ("SCAM", "RUGGED", "DUMP", "NEUTRAL"):
                    if prev == outcome or _rank.get(outcome, 0) <= _rank.get(prev, 0):
                        return
                first = float(row["first_mcap"] or 0)
                ath = float(row["ath_mcap"] or 0)
                mult = max_multiple or (ath / first if first > 0 else 0)
                # Cap multiples for learning so seeded 1000x+ megas don't warp averages
                learn_mult = min(float(mult or 0), 100.0) if mult else 0.0
                conn.execute(
                    """
                    UPDATE tokens SET outcome=?, outcome_ts=?, max_multiple=?,
                        notes=?, active=0
                    WHERE mint=?
                    """,
                    (outcome, now, mult, notes, mint),
                )
                feats = features
                if not feats and row["entry_features"]:
                    try:
                        feats = json.loads(row["entry_features"])
                    except Exception:
                        feats = {}
                # Only inject feature_stats when first finalizing or upgrading rank
                inject = prev is None or prev == "" or _rank.get(outcome, 0) > _rank.get(prev, 0)
                if feats and inject:
                    from services.learning.features import feature_keys_for_learning

                    for fk in feature_keys_for_learning(feats):
                        conn.execute(
                            """
                            INSERT INTO feature_stats(feature, outcome, count, sum_multiple)
                            VALUES(?,?,1,?)
                            ON CONFLICT(feature, outcome) DO UPDATE SET
                                count = count + 1,
                                sum_multiple = sum_multiple + excluded.sum_multiple
                            """,
                            (fk, outcome, learn_mult),
                        )
                conn.commit()
            finally:
                conn.close()

    def outcome_counts(self) -> dict[str, int]:
        """Token counts per outcome (for proper P(feature|outcome))."""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """
                    SELECT outcome, COUNT(*) AS n FROM tokens
                    WHERE outcome IS NOT NULL AND outcome != ''
                    GROUP BY outcome
                    """
                ).fetchall()
                return {r["outcome"]: int(r["n"]) for r in rows}
            finally:
                conn.close()

    def outcome_base_rates(self) -> dict[str, float]:
        """P(outcome) from finalized tokens (not raw feature_stats)."""
        counts = self.outcome_counts()
        total = sum(counts.values()) or 1
        return {k: v / total for k, v in counts.items()}

    def rebuild_feature_stats(self) -> dict[str, int]:
        """Recompute feature_stats from finalized entry_features (cleaner model)."""
        from services.learning.features import feature_keys_for_learning

        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """
                    SELECT entry_features, outcome, first_mcap, ath_mcap, max_multiple
                    FROM tokens
                    WHERE outcome IS NOT NULL AND outcome != ''
                      AND entry_features IS NOT NULL AND entry_features != ''
                    """
                ).fetchall()
                conn.execute("DELETE FROM feature_stats")
                n_tok = 0
                n_keys = 0
                for row in rows:
                    try:
                        feats = json.loads(row["entry_features"])
                    except Exception:
                        continue
                    if not isinstance(feats, dict):
                        continue
                    first = float(row["first_mcap"] or 0)
                    ath = float(row["ath_mcap"] or 0)
                    mult = float(row["max_multiple"] or 0) or (
                        ath / first if first > 0 else 0
                    )
                    learn_mult = min(mult, 100.0)
                    for fk in feature_keys_for_learning(feats):
                        conn.execute(
                            """
                            INSERT INTO feature_stats(feature, outcome, count, sum_multiple)
                            VALUES(?,?,1,?)
                            ON CONFLICT(feature, outcome) DO UPDATE SET
                                count = count + 1,
                                sum_multiple = sum_multiple + excluded.sum_multiple
                            """,
                            (fk, row["outcome"], learn_mult),
                        )
                        n_keys += 1
                    n_tok += 1
                conn.execute(
                    """
                    INSERT INTO meta(key, value) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    ("feature_stats_rebuilt_ts", str(time.time())),
                )
                conn.commit()
                return {"tokens": n_tok, "feature_rows_touched": n_keys}
            finally:
                conn.close()

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key=?", (key,)
                ).fetchone()
                return row["value"] if row else None
            finally:
                conn.close()

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """
                    INSERT INTO meta(key, value) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, value),
                )
                conn.commit()
            finally:
                conn.close()

    def get_active_mints(
        self, max_age_sec: float = 6 * 3600, limit: int = 140
    ) -> list[str]:
        cutoff = time.time() - max_age_sec
        lim = max(5, min(int(limit), 200))
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """
                    SELECT mint FROM tokens
                    WHERE active=1 AND first_seen >= ?
                    ORDER BY last_seen DESC LIMIT ?
                    """,
                    (cutoff, lim),
                ).fetchall()
                return [r["mint"] for r in rows]
            finally:
                conn.close()

    def get_token(self, mint: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM tokens WHERE mint=?", (mint,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def get_feature_stats(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT feature, outcome, count, sum_multiple FROM feature_stats"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_outcomes_summary(self) -> dict[str, Any]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """
                    SELECT outcome, COUNT(*) AS n,
                           AVG(max_multiple) AS avg_mult,
                           AVG(first_mcap) AS avg_entry,
                           AVG(ath_mcap) AS avg_ath,
                           AVG(creator_dump_mcap) AS avg_dump_mcap
                    FROM tokens
                    WHERE outcome IS NOT NULL AND outcome != ''
                    GROUP BY outcome
                    """
                ).fetchall()
                by = {r["outcome"]: dict(r) for r in rows}
                total = conn.execute(
                    "SELECT COUNT(*) AS n FROM tokens"
                ).fetchone()["n"]
                active = conn.execute(
                    "SELECT COUNT(*) AS n FROM tokens WHERE active=1"
                ).fetchone()["n"]
                finalized = sum(v["n"] for v in by.values())
                return {
                    "total_tracked": total,
                    "active": active,
                    "finalized": finalized,
                    "by_outcome": by,
                }
            finally:
                conn.close()

    def recent_finalized(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """
                    SELECT mint, name, symbol, first_mcap, ath_mcap,
                           creator_dump_mcap, last_mcap, outcome, max_multiple,
                           first_seen, outcome_ts, notes
                    FROM tokens
                    WHERE outcome IS NOT NULL AND outcome != ''
                    ORDER BY outcome_ts DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
