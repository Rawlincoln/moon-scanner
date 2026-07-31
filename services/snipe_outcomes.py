"""Track safe-snipe UI recommendations → later mcap outcomes + adaptive gates.

Mirrors moon_outcomes but for 2× SNIPE/SETUP grades (shorter horizons).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from services.http_client import get as http_get
from config import PUMPFUN_API_URL, REQUEST_TIMEOUT

HORIZONS = {
    "m15": 15 * 60,
    "h1": 60 * 60,
    "h3": 3 * 60 * 60,
}
FINALIZE_AFTER = 3 * 60 * 60
DUMP_FRAC = 0.72
HARD_DUMP_FRAC = 0.55
WIN_1_5 = 1.5
WIN_2 = 2.0


class SnipeOutcomes:
    def __init__(self, db_path: Path | str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=30000")
                except Exception:
                    pass
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS snipe_recs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        mint TEXT NOT NULL,
                        symbol TEXT,
                        name TEXT,
                        shown_at REAL NOT NULL,
                        entry_mcap REAL,
                        entry_ath REAL,
                        entry_label TEXT,
                        entry_score INTEGER,
                        entry_confidence INTEGER,
                        entry_bundled_pct REAL,
                        features TEXT,
                        mcap_15m REAL,
                        mcap_1h REAL,
                        mcap_3h REAL,
                        peak_mcap REAL,
                        low_mcap REAL,
                        outcome TEXT,
                        outcome_ts REAL,
                        multiple REAL,
                        active INTEGER DEFAULT 1,
                        cohort TEXT DEFAULT 'shown'
                    );
                    CREATE INDEX IF NOT EXISTS idx_snipe_recs_mint ON snipe_recs(mint);
                    CREATE INDEX IF NOT EXISTS idx_snipe_recs_active ON snipe_recs(active);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def record_shown(self, tokens: list[dict[str, Any]]) -> int:
        if not tokens:
            return 0
        now = time.time()
        n = 0
        with self._lock:
            conn = self._conn()
            try:
                for t in tokens:
                    mint = str(t.get("tokenAddress") or t.get("mint") or "").strip()
                    if not mint:
                        continue
                    recent = conn.execute(
                        """
                        SELECT id FROM snipe_recs
                        WHERE mint=? AND shown_at > ?
                        ORDER BY shown_at DESC LIMIT 1
                        """,
                        (mint, now - 30 * 60),
                    ).fetchone()
                    if recent:
                        continue
                    sn = t.get("snipe") or {}
                    bun = t.get("bundle") or (t.get("bundleSniper") or {}).get("bundle") or {}
                    if not isinstance(bun, dict):
                        bun = {}
                    mcap = float(t.get("mcap_usd") or 0)
                    ath = float(t.get("ath_mcap") or 0)
                    feats = {
                        "label": t.get("snipe_label") or sn.get("label"),
                        "bundle_pct": sn.get("bundle_pct") or bun.get("bundled_pct"),
                        "sniper_level": sn.get("sniper_level"),
                        "holders_known": sn.get("holders_known"),
                    }
                    conn.execute(
                        """
                        INSERT INTO snipe_recs (
                            mint, symbol, name, shown_at, entry_mcap, entry_ath,
                            entry_label, entry_score, entry_confidence, entry_bundled_pct,
                            features, peak_mcap, low_mcap, active, cohort
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,'shown')
                        """,
                        (
                            mint,
                            t.get("symbol") or "",
                            t.get("name") or "",
                            now,
                            mcap,
                            ath,
                            t.get("snipe_label") or sn.get("label") or "",
                            int(t.get("snipe_score") or sn.get("snipe_score") or 0),
                            int(t.get("confidence") or sn.get("confidence") or 0),
                            float(bun.get("bundled_pct") or sn.get("bundle_pct") or 0)
                            or None,
                            json.dumps(feats),
                            mcap,
                            mcap,
                        ),
                    )
                    n += 1
                conn.commit()
            finally:
                conn.close()
        return n

    def active_mints(self) -> list[str]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT mint FROM snipe_recs WHERE active=1"
                ).fetchall()
                return [r["mint"] for r in rows]
            finally:
                conn.close()

    async def fetch_mcap(self, mint: str) -> float | None:
        try:
            resp = await http_get(
                f"{PUMPFUN_API_URL}/coins/{mint}",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Origin": "https://pump.fun",
                },
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return float(data.get("usd_market_cap") or 0) or None
        except Exception:
            return None

    def _classify(self, entry: float, peak: float, last: float) -> tuple[str, float]:
        if entry <= 0 or last is None or last <= 0:
            return "unknown", 0.0
        last_mult = last / entry
        peak_mult = (peak / entry) if peak and peak > 0 else last_mult
        best = max(last_mult, peak_mult)
        if best >= WIN_2:
            return "win_2x", round(best, 3)
        if best >= WIN_1_5:
            return "win_1_5x", round(best, 3)
        if last <= entry * HARD_DUMP_FRAC or (
            peak > 0 and last <= peak * 0.45 and last_mult < 0.85
        ):
            return "dump", round(last_mult, 3)
        if last <= entry * DUMP_FRAC:
            return "dump", round(last_mult, 3)
        if last_mult >= 0.80:
            return "hold", round(last_mult, 3)
        return "dump", round(last_mult, 3)

    def apply_mcap(self, mint: str, mcap: float) -> int:
        now = time.time()
        n = 0
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM snipe_recs WHERE mint=? AND active=1",
                    (mint,),
                ).fetchall()
                for row in rows:
                    age = now - float(row["shown_at"])
                    peak = max(float(row["peak_mcap"] or 0), mcap)
                    low = min(
                        float(row["low_mcap"] or mcap) if row["low_mcap"] else mcap,
                        mcap,
                    )
                    sets = ["peak_mcap=?", "low_mcap=?"]
                    params: list[Any] = [peak, low]
                    if age >= HORIZONS["m15"] and row["mcap_15m"] is None:
                        sets.append("mcap_15m=?")
                        params.append(mcap)
                    if age >= HORIZONS["h1"] and row["mcap_1h"] is None:
                        sets.append("mcap_1h=?")
                        params.append(mcap)
                    if age >= HORIZONS["h3"] and row["mcap_3h"] is None:
                        sets.append("mcap_3h=?")
                        params.append(mcap)
                    finalize = age >= FINALIZE_AFTER or (
                        peak > 0
                        and mcap <= peak * 0.45
                        and mcap <= float(row["entry_mcap"] or 0) * 0.70
                    )
                    if finalize and row["outcome"] is None:
                        last = mcap
                        outcome, mult = self._classify(
                            float(row["entry_mcap"] or 0), peak, last
                        )
                        sets.extend(
                            ["outcome=?", "outcome_ts=?", "multiple=?", "active=0"]
                        )
                        params.extend([outcome, now, mult])
                    params.append(row["id"])
                    conn.execute(
                        f"UPDATE snipe_recs SET {', '.join(sets)} WHERE id=?",
                        params,
                    )
                    n += 1
                conn.commit()
            finally:
                conn.close()
        return n

    def _rates(self, rows: list) -> dict[str, Any]:
        by: dict[str, dict[str, Any]] = {}
        for r in rows:
            oc = r["outcome"] or "unknown"
            by.setdefault(oc, {"count": 0, "avg_multiple": 0.0})
            by[oc]["count"] += int(r["n"] or 0)
            by[oc]["avg_multiple"] = round(float(r["avg_mult"] or 0), 3)
        wins = sum(by.get(k, {}).get("count", 0) for k in ("win_2x", "win_1_5x"))
        dumps = by.get("dump", {}).get("count", 0)
        holds = by.get("hold", {}).get("count", 0)
        base = wins + dumps + holds
        return {
            "by_outcome": by,
            "n": base,
            "win_rate_pct": round(100 * wins / base, 1) if base else None,
            "dump_rate_pct": round(100 * dumps / base, 1) if base else None,
            "hold_rate_pct": round(100 * holds / base, 1) if base else None,
        }

    def summary(self) -> dict[str, Any]:
        with self._lock:
            conn = self._conn()
            try:
                total = conn.execute("SELECT COUNT(*) AS n FROM snipe_recs").fetchone()[
                    "n"
                ]
                active = conn.execute(
                    "SELECT COUNT(*) AS n FROM snipe_recs WHERE active=1"
                ).fetchone()["n"]
                rows = conn.execute(
                    """
                    SELECT outcome, COUNT(*) AS n, AVG(multiple) AS avg_mult
                    FROM snipe_recs WHERE outcome IS NOT NULL
                    GROUP BY outcome
                    """
                ).fetchall()
                overall = self._rates(rows)
                gates = self.suggested_gates_from_stats(overall=overall)
                return {
                    "total_recs": total,
                    "active": active,
                    "finalized": overall["n"],
                    "overall": overall,
                    "win_rate_pct": overall["win_rate_pct"],
                    "dump_rate_pct": overall["dump_rate_pct"],
                    "hold_rate_pct": overall["hold_rate_pct"],
                    "gates": gates,
                }
            finally:
                conn.close()

    @staticmethod
    def suggested_gates_from_stats(*, overall: dict[str, Any]) -> dict[str, Any]:
        base_score = 55
        min_samples = 8
        n = int(overall.get("n") or 0)
        dump = overall.get("dump_rate_pct")
        win = overall.get("win_rate_pct")
        score = base_score
        reasons: list[str] = []
        if n < min_samples:
            return {
                "min_score": score,
                "adapted": False,
                "sample_n": n,
                "reasons": [f"Using defaults (need ≥{min_samples} finalized, have {n})"],
            }
        if dump is not None and dump >= 70:
            score += 8
            reasons.append(f"Snipe dump {dump}% ≥70% → +8 min_score")
        elif dump is not None and dump >= 55:
            score += 5
            reasons.append(f"Snipe dump {dump}% ≥55% → +5 min_score")
        if n >= 12 and win is not None and win >= 40 and dump is not None and dump <= 40:
            score = max(base_score, score - 2)
            reasons.append(f"Solid snipe book win {win}% → slight relax")
        score = max(50, min(72, score))
        return {
            "min_score": int(score),
            "adapted": True,
            "sample_n": n,
            "reasons": reasons,
        }

    def suggested_gates(self) -> dict[str, Any]:
        return self.summary().get("gates") or {
            "min_score": 55,
            "adapted": False,
            "sample_n": 0,
            "reasons": ["defaults"],
        }


_default: SnipeOutcomes | None = None


def get_snipe_outcomes(base_dir: Path | None = None) -> SnipeOutcomes:
    global _default
    if _default is None:
        root = base_dir or Path(__file__).resolve().parent.parent
        _default = SnipeOutcomes(root / "data" / "snipe_outcomes.db")
    return _default
