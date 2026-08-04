"""Permanent-ish scan archive (Germanus-inspired).

Every full lab analysis is stored as a snapshot. Re-scans compare deltas.
Free-tier disk is ephemeral; GHA/DATA_DIR can persist when available.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from config import DATA_DIR
from services.cockpit import cockpit_delta, extract_cockpit, liquidity_drift_pct

_DEFAULT = Path(DATA_DIR) / "scan_archive.db"
_lock = threading.Lock()
_default: "ScanArchive | None" = None

# Germanus freshness: <10% liquidity drift → serve archive
FRESHNESS_LIQ_DRIFT_PCT = 10.0


class ScanArchive:
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
                    CREATE TABLE IF NOT EXISTS scans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mint TEXT NOT NULL,
                        symbol TEXT,
                        name TEXT,
                        scanned_at REAL NOT NULL,
                        cockpit_json TEXT NOT NULL,
                        raw_json TEXT,
                        liquidity_usd REAL,
                        mcap_usd REAL,
                        top1_pct REAL,
                        holders INTEGER,
                        unresolved INTEGER DEFAULT 0,
                        coverage_pct REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_scans_mint ON scans(mint);
                    CREATE INDEX IF NOT EXISTS idx_scans_at ON scans(scanned_at);
                    CREATE TABLE IF NOT EXISTS watchlist (
                        mint TEXT PRIMARY KEY,
                        symbol TEXT,
                        name TEXT,
                        starred_at REAL NOT NULL,
                        notes TEXT
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def store(self, result: dict[str, Any], *, store_raw: bool = False) -> dict[str, Any]:
        cockpit = extract_cockpit(result)
        mint = (cockpit.get("mint") or result.get("tokenAddress") or "").strip()
        if not mint:
            return {"ok": False, "error": "missing mint"}
        now = time.time()
        prev = self.latest(mint)
        delta = cockpit_delta(prev.get("cockpit") if prev else None, cockpit)
        with _lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO scans (
                        mint, symbol, name, scanned_at, cockpit_json, raw_json,
                        liquidity_usd, mcap_usd, top1_pct, holders, unresolved, coverage_pct
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        mint,
                        cockpit.get("symbol") or "",
                        cockpit.get("name") or "",
                        now,
                        json.dumps(cockpit),
                        json.dumps(result) if store_raw else None,
                        cockpit.get("liquidity_usd"),
                        cockpit.get("mcap_usd"),
                        cockpit.get("top1_pct"),
                        cockpit.get("holders"),
                        1 if cockpit.get("unresolved") else 0,
                        cockpit.get("coverage_pct"),
                    ),
                )
                conn.commit()
                sid = int(cur.lastrowid)
            finally:
                conn.close()
        return {
            "ok": True,
            "id": sid,
            "mint": mint,
            "scanned_at": now,
            "cockpit": cockpit,
            "delta": delta,
            "scan_count": self.scan_count(mint),
        }

    def latest(self, mint: str) -> dict[str, Any] | None:
        with _lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM scans WHERE mint=? ORDER BY scanned_at DESC LIMIT 1
                    """,
                    (mint.strip(),),
                ).fetchone()
                if not row:
                    return None
                return self._row(row)
            finally:
                conn.close()

    def history(self, mint: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with _lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM scans WHERE mint=?
                    ORDER BY scanned_at DESC LIMIT ?
                    """,
                    (mint.strip(), int(limit)),
                ).fetchall()
                return [self._row(r) for r in rows]
            finally:
                conn.close()

    def scan_count(self, mint: str) -> int:
        with _lock:
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT COUNT(*) AS n FROM scans WHERE mint=?", (mint.strip(),)
                ).fetchone()
                return int(r["n"]) if r else 0
            finally:
                conn.close()

    def list_archive(
        self,
        *,
        filter_mode: str = "all",
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        """Latest scan per mint, Germanus-style table."""
        with _lock:
            conn = self._conn()
            try:
                # one row per mint (latest)
                rows = conn.execute(
                    """
                    SELECT s.* FROM scans s
                    INNER JOIN (
                        SELECT mint, MAX(scanned_at) AS mx FROM scans GROUP BY mint
                    ) t ON s.mint = t.mint AND s.scanned_at = t.mx
                    ORDER BY s.scanned_at DESC
                    LIMIT ?
                    """,
                    (max(int(limit), 1),),
                ).fetchall()
                out = [self._row(r) for r in rows]
            finally:
                conn.close()

        now = time.time()
        watched = {w["mint"] for w in self.watchlist()}
        filtered: list[dict[str, Any]] = []
        for item in out:
            age_h = (now - float(item.get("scanned_at") or 0)) / 3600.0
            item["age_hours"] = round(age_h, 2)
            item["on_watchlist"] = item.get("mint") in watched
            item["scan_count"] = self.scan_count(item.get("mint") or "")
            mode = (filter_mode or "all").lower()
            if mode == "unresolved" and not item.get("unresolved"):
                continue
            if mode == "under_6h" and age_h > 6:
                continue
            if mode == "watchlist" and not item["on_watchlist"]:
                continue
            filtered.append(item)
        return filtered

    def freshness_ok(self, mint: str, live_liquidity: float | None) -> tuple[bool, dict[str, Any]]:
        """If archive exists and liquidity drift < 10%, serve cache (Germanus probe)."""
        prev = self.latest(mint)
        if not prev:
            return False, {"reason": "no_archive"}
        prev_liq = prev.get("liquidity_usd")
        if prev_liq is None and prev.get("cockpit"):
            prev_liq = (prev["cockpit"] or {}).get("liquidity_usd")
        drift = liquidity_drift_pct(
            float(prev_liq) if prev_liq is not None else None,
            live_liquidity,
        )
        meta = {
            "prev_liquidity": prev_liq,
            "live_liquidity": live_liquidity,
            "drift_pct": round(drift, 2) if drift is not None else None,
            "threshold_pct": FRESHNESS_LIQ_DRIFT_PCT,
            "scanned_at": prev.get("scanned_at"),
        }
        if drift is None:
            # no liquidity to compare — if scan < 15m still fresh
            age = time.time() - float(prev.get("scanned_at") or 0)
            if age < 15 * 60:
                return True, {**meta, "reason": "recent_scan"}
            return False, {**meta, "reason": "no_liquidity_compare"}
        if drift < FRESHNESS_LIQ_DRIFT_PCT:
            return True, {**meta, "reason": "liquidity_stable"}
        return False, {**meta, "reason": "liquidity_moved"}

    # --- watchlist ---
    def star(self, mint: str, *, symbol: str = "", name: str = "", notes: str = "") -> dict:
        mint = mint.strip()
        with _lock:
            conn = self._conn()
            try:
                conn.execute(
                    """
                    INSERT INTO watchlist (mint, symbol, name, starred_at, notes)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(mint) DO UPDATE SET
                        symbol=excluded.symbol,
                        name=excluded.name,
                        notes=excluded.notes,
                        starred_at=excluded.starred_at
                    """,
                    (mint, symbol, name, time.time(), notes[:200]),
                )
                conn.commit()
            finally:
                conn.close()
        return {"ok": True, "mint": mint, "starred": True}

    def unstar(self, mint: str) -> dict:
        with _lock:
            conn = self._conn()
            try:
                conn.execute("DELETE FROM watchlist WHERE mint=?", (mint.strip(),))
                conn.commit()
            finally:
                conn.close()
        return {"ok": True, "mint": mint, "starred": False}

    def watchlist(self) -> list[dict[str, Any]]:
        with _lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM watchlist ORDER BY starred_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["cockpit"] = json.loads(d.get("cockpit_json") or "{}")
        except Exception:
            d["cockpit"] = {}
        d.pop("cockpit_json", None)
        d.pop("raw_json", None)
        d["unresolved"] = bool(d.get("unresolved"))
        return d


def get_archive(db_path: Path | str | None = None) -> ScanArchive:
    global _default
    if db_path is not None:
        return ScanArchive(db_path)
    if _default is None:
        _default = ScanArchive()
    return _default
