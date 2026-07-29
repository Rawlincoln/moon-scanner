"""Track moon UI recommendations → later mcap outcomes.

Closes the feedback loop: what we showed as MOON/WATCH, did it pump or dump?

Horizons:
  15m, 1h, 6h snapshots
Outcomes (at finalize, default 6h or earlier hard dump):
  win_2x   — mcap >= 2× entry
  win_1_5x — mcap >= 1.5× entry
  hold     — still within −20% of entry and not win
  dump     — mcap <= 70% of entry (or <= 55% of peak_seen)
  rug      — hard reject flags later / mcap dust
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

# Snapshot ages (seconds)
HORIZONS = {
    "m15": 15 * 60,
    "h1": 60 * 60,
    "h6": 6 * 60 * 60,
}
FINALIZE_AFTER = 6 * 60 * 60
DUMP_FRAC = 0.70  # −30% from entry = dump
HARD_DUMP_FRAC = 0.55
WIN_1_5 = 1.5
WIN_2 = 2.0


class MoonOutcomes:
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
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS moon_recs (
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
                        influencer_tweet INTEGER DEFAULT 0,
                        narrative TEXT,
                        features TEXT,
                        mcap_15m REAL,
                        mcap_1h REAL,
                        mcap_6h REAL,
                        peak_mcap REAL,
                        low_mcap REAL,
                        outcome TEXT,
                        outcome_ts REAL,
                        multiple REAL,
                        active INTEGER DEFAULT 1
                    );
                    CREATE INDEX IF NOT EXISTS idx_moon_recs_mint ON moon_recs(mint);
                    CREATE INDEX IF NOT EXISTS idx_moon_recs_active ON moon_recs(active);
                    CREATE INDEX IF NOT EXISTS idx_moon_recs_shown ON moon_recs(shown_at);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def record_shown(self, tokens: list[dict[str, Any]]) -> int:
        """Record tokens displayed by /api/moon (dedupe same mint within 30m)."""
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
                        SELECT id FROM moon_recs
                        WHERE mint=? AND shown_at > ?
                        ORDER BY shown_at DESC LIMIT 1
                        """,
                        (mint, now - 30 * 60),
                    ).fetchone()
                    if recent:
                        continue
                    moon = t.get("moon") or {}
                    social = t.get("socialSignals") or {}
                    bun = t.get("bundle") or (t.get("bundleSniper") or {}).get("bundle") or {}
                    mcap = float(t.get("mcap_usd") or 0)
                    ath = float(t.get("ath_mcap") or 0)
                    feats = {
                        "stage": t.get("stage") or moon.get("stage"),
                        "pillars": moon.get("pillars"),
                        "bundled_pct": bun.get("bundled_pct"),
                        "realtime": t.get("realtime"),
                        "realtime_source": t.get("realtime_source"),
                    }
                    conn.execute(
                        """
                        INSERT INTO moon_recs (
                            mint, symbol, name, shown_at, entry_mcap, entry_ath,
                            entry_label, entry_score, entry_confidence, entry_bundled_pct,
                            influencer_tweet, narrative, features, peak_mcap, low_mcap, active
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                        """,
                        (
                            mint,
                            t.get("symbol") or "",
                            t.get("name") or "",
                            now,
                            mcap,
                            ath,
                            t.get("moon_label") or moon.get("label") or "",
                            int(t.get("moon_score") or moon.get("moon_score") or 0),
                            int(t.get("confidence") or moon.get("confidence") or 0),
                            float(bun.get("bundled_pct") or 0) or None,
                            1
                            if (
                                moon.get("influencer_tweet")
                                or social.get("influencer_tweet")
                            )
                            else 0,
                            (moon.get("narrative") or social.get("summary") or "")[:200],
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
                    "SELECT DISTINCT mint FROM moon_recs WHERE active=1"
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
        mult = last / entry
        if last <= entry * HARD_DUMP_FRAC or (
            peak > 0 and last <= peak * 0.45 and mult < 0.85
        ):
            return "dump", round(mult, 3)
        if last <= entry * DUMP_FRAC:
            return "dump", round(mult, 3)
        if mult >= WIN_2:
            return "win_2x", round(mult, 3)
        if mult >= WIN_1_5:
            return "win_1_5x", round(mult, 3)
        if mult >= 0.80:
            return "hold", round(mult, 3)
        return "dump", round(mult, 3)

    def apply_mcap(self, mint: str, mcap: float) -> int:
        """Update all active recs for mint with current mcap; finalize if due."""
        if mcap is None or mcap <= 0:
            return 0
        now = time.time()
        updated = 0
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM moon_recs WHERE mint=? AND active=1",
                    (mint,),
                ).fetchall()
                for row in rows:
                    age = now - float(row["shown_at"])
                    peak = max(float(row["peak_mcap"] or 0), mcap)
                    low = float(row["low_mcap"] or mcap)
                    if low <= 0:
                        low = mcap
                    else:
                        low = min(low, mcap)
                    m15 = row["mcap_15m"]
                    m1h = row["mcap_1h"]
                    m6h = row["mcap_6h"]
                    if m15 is None and age >= HORIZONS["m15"]:
                        m15 = mcap
                    if m1h is None and age >= HORIZONS["h1"]:
                        m1h = mcap
                    if m6h is None and age >= HORIZONS["h6"]:
                        m6h = mcap

                    entry = float(row["entry_mcap"] or 0)
                    outcome = row["outcome"]
                    outcome_ts = row["outcome_ts"]
                    multiple = row["multiple"]
                    active = 1

                    # Early finalize on hard dump after 15m
                    if age >= HORIZONS["m15"] and entry > 0 and mcap <= entry * HARD_DUMP_FRAC:
                        outcome, multiple = self._classify(entry, peak, mcap)
                        outcome_ts = now
                        active = 0
                    elif age >= FINALIZE_AFTER:
                        last = m6h if m6h is not None else mcap
                        outcome, multiple = self._classify(entry, peak, float(last))
                        outcome_ts = now
                        active = 0

                    conn.execute(
                        """
                        UPDATE moon_recs SET
                            mcap_15m=?, mcap_1h=?, mcap_6h=?,
                            peak_mcap=?, low_mcap=?,
                            outcome=?, outcome_ts=?, multiple=?, active=?
                        WHERE id=?
                        """,
                        (
                            m15,
                            m1h,
                            m6h,
                            peak,
                            low,
                            outcome,
                            outcome_ts,
                            multiple,
                            active,
                            row["id"],
                        ),
                    )
                    updated += 1
                conn.commit()
            finally:
                conn.close()
        return updated

    @staticmethod
    def _rates_from_rows(rows: list) -> dict[str, Any]:
        """rows: list of (outcome, count, avg_mult) or Row-like with those keys."""
        by: dict[str, Any] = {}
        for row in rows:
            if hasattr(row, "keys"):
                oc, n, avg = row["outcome"], row["n"], row["avg_mult"]
            else:
                oc, n, avg = row[0], row[1], row[2]
            by[str(oc)] = {
                "count": int(n),
                "avg_multiple": round(float(avg or 0), 3),
            }
        wins = sum(by.get(k, {}).get("count", 0) for k in ("win_2x", "win_1_5x"))
        dumps = by.get("dump", {}).get("count", 0)
        holds = by.get("hold", {}).get("count", 0)
        done = wins + dumps + holds + by.get("unknown", {}).get("count", 0)
        # rates use wins+dumps+holds only (ignore unknown)
        base = wins + dumps + holds
        return {
            "by_outcome": by,
            "n": base,
            "win_rate_pct": round(100 * wins / base, 1) if base else None,
            "dump_rate_pct": round(100 * dumps / base, 1) if base else None,
            "hold_rate_pct": round(100 * holds / base, 1) if base else None,
        }

    def _segment_stats(self, conn: sqlite3.Connection, where: str, params: tuple = ()) -> dict[str, Any]:
        sql = f"""
            SELECT outcome, COUNT(*) AS n, AVG(multiple) AS avg_mult
            FROM moon_recs
            WHERE outcome IS NOT NULL AND ({where})
            GROUP BY outcome
        """
        rows = conn.execute(sql, params).fetchall()
        return self._rates_from_rows(rows)

    def _analytics_unlocked(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """Segment stats only (no gates — avoids recursion)."""
        total = conn.execute("SELECT COUNT(*) AS n FROM moon_recs").fetchone()["n"]
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM moon_recs WHERE active=1"
        ).fetchone()["n"]
        finalized = conn.execute(
            "SELECT COUNT(*) AS n FROM moon_recs WHERE outcome IS NOT NULL"
        ).fetchone()["n"]
        overall = self._segment_stats(conn, "1=1")
        by_label: dict[str, Any] = {}
        for lab in ("MOON", "WATCH", "WEAK"):
            by_label[lab] = self._segment_stats(conn, "entry_label=?", (lab,))
        by_influencer = {
            "yes": self._segment_stats(conn, "influencer_tweet=1"),
            "no": self._segment_stats(conn, "influencer_tweet=0"),
        }
        by_bundled = {
            "lt5": self._segment_stats(
                conn, "entry_bundled_pct IS NULL OR entry_bundled_pct < 5"
            ),
            "5_12": self._segment_stats(
                conn, "entry_bundled_pct >= 5 AND entry_bundled_pct < 12"
            ),
            "12_20": self._segment_stats(
                conn, "entry_bundled_pct >= 12 AND entry_bundled_pct < 20"
            ),
            "ge20": self._segment_stats(conn, "entry_bundled_pct >= 20"),
        }
        recent = []
        for row in conn.execute(
            """
            SELECT mint, symbol, shown_at, entry_mcap, entry_label,
                   entry_score, entry_confidence, entry_bundled_pct,
                   mcap_15m, mcap_1h, mcap_6h, peak_mcap,
                   outcome, multiple, active, influencer_tweet
            FROM moon_recs ORDER BY shown_at DESC LIMIT 25
            """
        ):
            recent.append(dict(row))
        return {
            "total_recs": total,
            "active": active,
            "finalized": finalized,
            "overall": overall,
            "by_outcome": overall["by_outcome"],
            "win_rate_pct": overall["win_rate_pct"],
            "dump_rate_pct": overall["dump_rate_pct"],
            "hold_rate_pct": overall["hold_rate_pct"],
            "by_label": by_label,
            "by_influencer": by_influencer,
            "by_bundled_band": by_bundled,
            "recent": recent,
        }

    def summary(self) -> dict[str, Any]:
        with self._lock:
            conn = self._conn()
            try:
                stats = self._analytics_unlocked(conn)
                gates = self.suggested_gates_from_stats(
                    overall=stats["overall"],
                    by_label=stats["by_label"],
                    by_influencer=stats["by_influencer"],
                    by_bundled=stats["by_bundled_band"],
                )
                stats["gates"] = gates
                return stats
            finally:
                conn.close()

    @staticmethod
    def suggested_gates_from_stats(
        *,
        overall: dict[str, Any],
        by_label: dict[str, Any],
        by_influencer: dict[str, Any],
        by_bundled: dict[str, Any],
    ) -> dict[str, Any]:
        """Data-driven gate suggestion with conservative floors/ceilings.

        Defaults (no data): score≥55, conf≥52, max_bundled 12%.
        Tightens when dump_rate is high; slight relax only with solid win sample.
        """
        base_score = 55
        base_conf = 52
        max_bundled = 12.0  # hard skip zone starts at 12% in bundle_sniper
        min_samples = 8
        reasons: list[str] = []

        n = int(overall.get("n") or 0)
        dump = overall.get("dump_rate_pct")
        win = overall.get("win_rate_pct")

        score, conf = base_score, base_conf

        if n < min_samples:
            reasons.append(
                f"Using defaults (need ≥{min_samples} finalized, have {n})"
            )
            return {
                "min_score": score,
                "min_confidence": conf,
                "max_bundled_pct": max_bundled,
                "require_influencer": False,
                "adapted": False,
                "sample_n": n,
                "reasons": reasons,
            }

        if dump is not None and dump >= 75:
            score += 8
            conf += 8
            reasons.append(f"Overall dump {dump}% ≥75% → +8 score/conf")
        elif dump is not None and dump >= 60:
            score += 5
            conf += 5
            reasons.append(f"Overall dump {dump}% ≥60% → +5 score/conf")
        elif dump is not None and dump >= 50:
            score += 3
            conf += 3
            reasons.append(f"Overall dump {dump}% ≥50% → +3 score/conf")

        # WATCH-only historically worse → raise conf more than score
        watch = by_label.get("WATCH") or {}
        if int(watch.get("n") or 0) >= 5 and (watch.get("dump_rate_pct") or 0) >= 65:
            conf += 4
            reasons.append(
                f"WATCH dump {watch.get('dump_rate_pct')}% (n={watch.get('n')}) → +4 conf"
            )

        # Non-influencer dumps worse than influencer
        inf_no = by_influencer.get("no") or {}
        inf_yes = by_influencer.get("yes") or {}
        require_inf = False
        if (
            int(inf_no.get("n") or 0) >= 6
            and (inf_no.get("dump_rate_pct") or 0) >= 70
            and (
                inf_yes.get("n") is None
                or int(inf_yes.get("n") or 0) < 3
                or (inf_yes.get("dump_rate_pct") or 100)
                < (inf_no.get("dump_rate_pct") or 0) - 10
            )
        ):
            conf += 3
            require_inf = (inf_no.get("dump_rate_pct") or 0) >= 80
            reasons.append(
                f"Non-influencer dump {inf_no.get('dump_rate_pct')}% → "
                + ("require influencer" if require_inf else "+3 conf")
            )

        # Bundled mid band toxic
        b512 = by_bundled.get("5_12") or {}
        if int(b512.get("n") or 0) >= 5 and (b512.get("dump_rate_pct") or 0) >= 70:
            max_bundled = 5.0
            reasons.append(
                f"Bundled 5–12% dump {b512.get('dump_rate_pct')}% → max_bundled 5%"
            )
        b1220 = by_bundled.get("12_20") or {}
        if int(b1220.get("n") or 0) >= 3 and (b1220.get("dump_rate_pct") or 0) >= 60:
            max_bundled = min(max_bundled, 8.0)
            reasons.append(
                f"Bundled 12–20% dump {b1220.get('dump_rate_pct')}% → cap bundled ≤8%"
            )

        # Slight relax only if clearly working
        if (
            n >= 12
            and win is not None
            and win >= 40
            and dump is not None
            and dump <= 40
        ):
            score = max(base_score, score - 2)
            conf = max(base_conf, conf - 2)
            reasons.append(f"Solid book win {win}% / dump {dump}% → slight relax −2")

        # Floors / ceilings
        score = max(52, min(72, score))
        conf = max(50, min(70, conf))
        max_bundled = max(5.0, min(12.0, max_bundled))

        return {
            "min_score": int(score),
            "min_confidence": int(conf),
            "max_bundled_pct": float(max_bundled),
            "require_influencer": bool(require_inf),
            "adapted": True,
            "sample_n": n,
            "reasons": reasons,
        }

    def suggested_gates(self) -> dict[str, Any]:
        """Load stats and return adaptive gates (no recursive summary)."""
        with self._lock:
            conn = self._conn()
            try:
                stats = self._analytics_unlocked(conn)
            finally:
                conn.close()
        return self.suggested_gates_from_stats(
            overall=stats["overall"],
            by_label=stats["by_label"],
            by_influencer=stats["by_influencer"],
            by_bundled=stats["by_bundled_band"],
        )


# Default store next to learning.db
_default: MoonOutcomes | None = None


def get_outcomes(base_dir: Path | None = None) -> MoonOutcomes:
    global _default
    if _default is None:
        root = base_dir or Path(__file__).resolve().parent.parent
        _default = MoonOutcomes(root / "data" / "moon_outcomes.db")
    return _default

