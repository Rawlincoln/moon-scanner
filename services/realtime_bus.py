"""In-memory realtime launch bus.

Geyser / Yellowstone / ShredStream (or a fast poller) push LaunchEvents here.
Moon scan and UI pull recent mints for priority analysis.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


# Pump.fun program (bonding curve) — primary sniper subscription target
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


@dataclass
class LaunchEvent:
    mint: str
    source: str  # yellowstone | shredstream | pump_poll | manual
    seen_at: float
    slot: int | None = None
    signature: str | None = None
    kind: str = "create"  # create | buy | unknown
    program_id: str = PUMPFUN_PROGRAM_ID
    meta: dict[str, Any] = field(default_factory=dict)

    def age_ms(self) -> float:
        return (time.time() - self.seen_at) * 1000


class RealtimeBus:
    """Thread-safe ring buffer of recent launch events."""

    def __init__(self, maxlen: int = 500) -> None:
        self._events: deque[LaunchEvent] = deque(maxlen=maxlen)
        self._by_mint: dict[str, LaunchEvent] = {}
        self._lock = threading.Lock()
        self._stats = {
            "pushed": 0,
            "duplicates": 0,
            "last_push_at": 0.0,
            "sources": {},
        }
        self.feed_status: dict[str, Any] = {
            "mode": "offline",
            "yellowstone": {"configured": False, "connected": False, "detail": ""},
            "shredstream": {"configured": False, "connected": False, "detail": ""},
            "pump_poll": {"running": False, "interval_sec": 0, "detail": ""},
        }

    def push(self, event: LaunchEvent) -> bool:
        mint = (event.mint or "").strip()
        if not mint:
            return False
        with self._lock:
            prev = self._by_mint.get(mint)
            if prev and event.kind == "create" and prev.kind == "create":
                self._stats["duplicates"] += 1
                return False
            # Prefer earlier create; allow buy to update meta
            if prev and prev.seen_at <= event.seen_at and prev.kind == "create":
                if event.kind == "buy":
                    prev.meta = {**prev.meta, **event.meta, "last_buy_sig": event.signature}
                self._stats["duplicates"] += 1
                return False
            self._by_mint[mint] = event
            self._events.appendleft(event)
            self._stats["pushed"] += 1
            self._stats["last_push_at"] = event.seen_at
            src = event.source
            self._stats["sources"][src] = self._stats["sources"].get(src, 0) + 1
            return True

    def recent(self, limit: int = 40, max_age_sec: float = 600) -> list[dict[str, Any]]:
        now = time.time()
        out: list[dict[str, Any]] = []
        with self._lock:
            for ev in self._events:
                if now - ev.seen_at > max_age_sec:
                    continue
                d = asdict(ev)
                d["age_ms"] = round(ev.age_ms(), 1)
                out.append(d)
                if len(out) >= limit:
                    break
        return out

    def has_mint(self, mint: str) -> LaunchEvent | None:
        with self._lock:
            return self._by_mint.get((mint or "").strip())

    def priority_mints(self, limit: int = 30, max_age_sec: float = 180) -> list[str]:
        return [e["mint"] for e in self.recent(limit=limit, max_age_sec=max_age_sec)]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._stats,
                "buffer_size": len(self._events),
                "unique_mints": len(self._by_mint),
                "feed": dict(self.feed_status),
            }


# Global bus
realtime_bus = RealtimeBus()
