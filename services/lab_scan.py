"""Fast Lab snapshot — multi-source parallel facts (Germanus alternative).

Goals vs Germanus deep scan (1–3 min, fragile):
  - Target wall time: ~1–3s (fast) or ~4–6s (deep)
  - Parallel pump + dex + rugcheck (summary first)
  - Multi-source mcap/liq merge (more accurate than single API)
  - Partial results OK — n/a for missing cells, never invent
  - In-memory cache for snappy re-reads
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from services.bundle_sniper import analyze_bundle_and_snipers
from services.cockpit import extract_cockpit
from services.dev_risk import analyze_creator_history, attach_dev_risk
from services.dexscreener import DexScreenerClient
from services.fee_flow import analyze_fee_flow
from services.http_client import get as http_get
from services.pumpfun import PumpFunClient
from services.scan_archive import get_archive
from services.solana_analyzer import SolanaAnalyzer
from services.ticker_registry import attach_ticker_uniqueness
from services.tx_activity import score_tx_activity
from config import REQUEST_TIMEOUT, USER_AGENT

logger = logging.getLogger("moon-scanner.lab_scan")

_dex = DexScreenerClient()
_pump = PumpFunClient()
_sol = SolanaAnalyzer()
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens"

# mint -> (ts, payload)
_mem_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_MEM_TTL_FAST = 45.0
_MEM_TTL_DEEP = 90.0


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


async def _timed(name: str, coro, timeout: float) -> tuple[str, Any, float, str | None]:
    t0 = time.perf_counter()
    try:
        val = await asyncio.wait_for(coro, timeout=timeout)
        return name, val, (time.perf_counter() - t0) * 1000, None
    except asyncio.TimeoutError:
        return name, None, (time.perf_counter() - t0) * 1000, "timeout"
    except Exception as exc:
        return name, None, (time.perf_counter() - t0) * 1000, f"{type(exc).__name__}:{exc}"[:120]


async def _fetch_pump(mint: str) -> dict | None:
    return await _pump.get_coin(mint)


async def _fetch_dex(mint: str) -> dict | None:
    pairs = await _dex.get_token_pairs("solana", mint)
    if not pairs:
        return None
    pair = _dex.pick_best_pair(pairs)
    return {"pair": pair, "pairs": pairs, "pair_count": len(pairs)}


async def _fetch_rug_summary(mint: str) -> dict | None:
    resp = await http_get(
        f"{RUGCHECK}/{mint}/report/summary",
        headers={"User-Agent": USER_AGENT},
        timeout=min(REQUEST_TIMEOUT, 3.5),
    )
    if resp.status_code == 404:
        return {"_empty": True}
    if resp.status_code == 429:
        return {"_rate_limited": True}
    if resp.status_code != 200:
        return {"_error": f"HTTP {resp.status_code}"}
    return resp.json()


async def _fetch_rug_full(mint: str) -> dict | None:
    resp = await http_get(
        f"{RUGCHECK}/{mint}/report",
        headers={"User-Agent": USER_AGENT},
        timeout=min(REQUEST_TIMEOUT, 5.0),
    )
    if resp.status_code != 200:
        return None
    return resp.json()


def _merge_market(pump: dict | None, dex: dict | None) -> dict[str, Any]:
    pair = (dex or {}).get("pair") or {}
    pump = pump or {}
    liq = 0.0
    if isinstance(pair.get("liquidity"), dict):
        liq = _f(pair["liquidity"].get("usd"))
    mcap_dex = _f(pair.get("marketCap") or pair.get("fdv"))
    mcap_pump = _f(pump.get("usd_market_cap"))
    # Prefer higher non-zero as high-water; for current use max of live sources when both
    mcap = 0.0
    if mcap_dex > 0 and mcap_pump > 0:
        # Prefer dex when graduated (complete); pump when on curve
        if pump.get("complete"):
            mcap = mcap_dex or mcap_pump
        else:
            mcap = mcap_pump or mcap_dex
    else:
        mcap = mcap_dex or mcap_pump
    ath = max(
        _f(pump.get("ath_market_cap")),
        mcap_dex,
        mcap_pump,
    )
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
    return {
        "priceChange": pair.get("priceChange") or {},
        "txns": pair.get("txns") or {},
        "volume": pair.get("volume") or {},
        "liquidity": pair.get("liquidity") or {"usd": liq},
        "liquidity_usd": liq,
        "marketCap": mcap,
        "url": pair.get("url"),
        "pair_count": (dex or {}).get("pair_count") or (1 if pair else 0),
        "dexId": pair.get("dexId"),
        "pairAddress": pair.get("pairAddress"),
        "baseToken": base,
        "pumpfun": pump,
        "sources_mcap": {
            "pump": mcap_pump or None,
            "dex": mcap_dex or None,
            "chosen": mcap or None,
        },
        "ath_merged": ath or None,
    }


def _build_result(
    mint: str,
    *,
    pump: dict | None,
    dex: dict | None,
    safety: dict[str, Any],
    mode: str,
    timings: dict[str, float],
    source_errors: dict[str, str],
    wall_ms: float,
) -> dict[str, Any]:
    market = _merge_market(pump, dex)
    pf = pump or {}
    age = PumpFunClient.coin_age_minutes(pf) if pf else None
    if age is not None and age < 9000:
        pf = {**pf, "age_minutes": round(age, 2)}
        market["pumpfun"] = pf

    mcap = _f(market.get("marketCap"))
    symbol = (
        pf.get("symbol")
        or (market.get("baseToken") or {}).get("symbol")
        or safety.get("token_symbol")
        or "?"
    )
    name = (
        pf.get("name")
        or (market.get("baseToken") or {}).get("name")
        or safety.get("token_name")
        or ""
    )

    # Bundle + fee + dev on assembled card
    token: dict[str, Any] = {
        "tokenAddress": mint,
        "chainId": "solana",
        "symbol": symbol,
        "name": name,
        "mcap_usd": mcap,
        "ath_mcap": market.get("ath_merged") or pf.get("ath_market_cap"),
        "age_minutes": pf.get("age_minutes") or age,
        "safety": safety,
        "market": market,
        "pumpfun": pf,
        "priceChange": market.get("priceChange"),
        "analyzedAt": time.time(),
    }
    try:
        token["txActivity"] = score_tx_activity(pair=market, pump=pf)
    except Exception:
        pass
    try:
        bs = analyze_bundle_and_snipers(
            safety,
            pf,
            market,
            age_minutes=token.get("age_minutes"),
            mcap_usd=mcap or None,
        )
        token["bundleSniper"] = bs
        safety["bundleSniper"] = bs
    except Exception:
        pass
    try:
        token["feeFlow"] = analyze_fee_flow(token)
    except Exception:
        pass
    try:
        attach_dev_risk(token)
    except Exception:
        pass
    try:
        attach_ticker_uniqueness(token, record=True)
    except Exception:
        pass

    cockpit = extract_cockpit(token)
    # Enrich cockpit with lab meta
    cockpit["lab_mode"] = mode
    cockpit["latency_ms"] = round(wall_ms, 1)
    cockpit["source_timings_ms"] = {k: round(v, 1) for k, v in timings.items()}
    cockpit["source_errors"] = source_errors or {}
    cockpit["sources_ok"] = [
        s
        for s in ("pump", "dex", "rugcheck")
        if s not in source_errors and timings.get(s) is not None
    ]
    # Multi-source accuracy note
    sm = market.get("sources_mcap") or {}
    cockpit["mcap_sources"] = sm
    if sm.get("pump") and sm.get("dex"):
        drift = abs(float(sm["pump"]) - float(sm["dex"])) / max(float(sm["pump"]), 1.0)
        cockpit["mcap_source_drift_pct"] = round(drift * 100, 1)

    # Attach fee/dev/ticker facts to cockpit for UI
    ff = token.get("feeFlow") or {}
    if isinstance(ff, dict):
        cockpit["fee_quality"] = ff.get("quality")
        cockpit["vol_m5_usd"] = ff.get("vol_m5_usd")
        cockpit["flow_score"] = ff.get("score")
    dev = token.get("devRisk") or {}
    if isinstance(dev, dict):
        cockpit["dev_risk"] = dev.get("risk_level")
        cockpit["dev_proven"] = dev.get("proven_dev")
        cockpit["prior_moons"] = dev.get("prior_moons")
        cockpit["prior_rugs"] = dev.get("prior_rugs")
        cockpit["tokens_migrated"] = dev.get("tokens_migrated")
    tu = token.get("tickerUniqueness") or {}
    if isinstance(tu, dict):
        cockpit["ticker_status"] = tu.get("status")
        cockpit["ticker_unique"] = tu.get("unique")

    token["cockpit"] = cockpit
    return token


async def lab_fast_scan(mint: str, *, force: bool = False) -> dict[str, Any]:
    """Parallel multi-source lab snapshot (default path)."""
    mint = mint.strip()
    t_wall = time.perf_counter()

    # Memory cache
    if not force and mint in _mem_cache:
        ts, payload = _mem_cache[mint]
        if time.time() - ts < _MEM_TTL_FAST:
            out = dict(payload)
            out["served_from"] = "memory_cache"
            out["cache_age_sec"] = round(time.time() - ts, 1)
            return out

    arch = get_archive()

    # Parallel: pump + dex + rug summary + full report
    # (summary alone has NO totalHolders/topHolders — full report required for holders)
    results = await asyncio.gather(
        _timed("pump", _fetch_pump(mint), 2.8),
        _timed("dex", _fetch_dex(mint), 2.5),
        _timed("rugcheck", _fetch_rug_summary(mint), 3.2),
        _timed("rug_full", _fetch_rug_full(mint), 4.5),
    )
    timings: dict[str, float] = {}
    errors: dict[str, str] = {}
    pump = dex = rug_sum = rug_full = None
    for name, val, ms, err in results:
        timings[name] = ms
        if err:
            errors[name] = err
        elif name == "pump":
            pump = val
        elif name == "dex":
            dex = val
        elif name == "rugcheck":
            rug_sum = val
        elif name == "rug_full":
            rug_full = val

    if isinstance(rug_sum, dict) and rug_sum.get("_rate_limited"):
        errors["rugcheck"] = "rate_limited"
        rug_sum = None
    if isinstance(rug_sum, dict) and rug_sum.get("_empty"):
        errors["rugcheck"] = "not_indexed"
        rug_sum = None
    if isinstance(rug_sum, dict) and rug_sum.get("_error"):
        errors["rugcheck"] = str(rug_sum["_error"])
        rug_sum = None

    # Parse safety: summary + full (holders/authorities live on full report)
    safety: dict[str, Any] = {}
    if rug_sum and isinstance(rug_sum, dict):
        try:
            safety = _sol._parse_response(
                mint,
                rug_sum,
                rug_full if isinstance(rug_full, dict) else {},
                pump_coin=pump,
                padre_audit=None,
            )
        except Exception as exc:
            errors["rug_parse"] = str(exc)[:80]
            safety = {}
    elif isinstance(rug_full, dict):
        # Full-only path when summary failed
        try:
            safety = _sol._parse_response(
                mint, {}, rug_full, pump_coin=pump, padre_audit=None
            )
        except Exception as exc:
            errors["rug_parse"] = str(exc)[:80]
            safety = {}
    if not safety and pump:
        safety = _sol._pumpfun_fallback(mint, pump, padre_audit=None)
        errors.setdefault("rugcheck", "fallback_pump")
    if not safety:
        safety = {
            "mint": mint,
            "passed": False,
            "error": True,
            "issues": ["No safety source responded"],
            "top_holders": [],
        }

    # Ensure holder fields populated even if parse missed alternate keys
    if isinstance(rug_full, dict):
        if not safety.get("total_holders"):
            th = rug_full.get("totalHolders") or rug_full.get("total_holders")
            if th is not None:
                try:
                    safety["total_holders"] = int(th)
                except (TypeError, ValueError):
                    pass
        if not safety.get("top_holders"):
            tops = rug_full.get("topHolders") or rug_full.get("top_holders") or []
            if isinstance(tops, list) and tops:
                safety["top_holders"] = tops[:20]
        if "mint_authority" not in safety or safety.get("mint_authority") is True:
            if "mintAuthority" in rug_full:
                safety["mint_authority"] = rug_full.get("mintAuthority")
        if "freeze_authority" not in safety or safety.get("freeze_authority") is True:
            if "freezeAuthority" in rug_full:
                safety["freeze_authority"] = rug_full.get("freezeAuthority")

    wall_ms = (time.perf_counter() - t_wall) * 1000
    token = _build_result(
        mint,
        pump=pump,
        dex=dex,
        safety=safety,
        mode="fast",
        timings=timings,
        source_errors=errors,
        wall_ms=wall_ms,
    )

    # Archive always (fast snapshots still valuable)
    try:
        stored = arch.store(token, store_raw=False)
    except Exception as exc:
        stored = {"ok": False, "error": str(exc)[:80]}
        logger.debug("lab archive store failed: %s", exc)

    payload = {
        "ok": True,
        "served_from": "lab_fast",
        "mode": "fast",
        "mint": mint,
        "latency_ms": round(wall_ms, 1),
        "timings_ms": {k: round(v, 1) for k, v in timings.items()},
        "source_errors": errors,
        "cockpit": token.get("cockpit"),
        "delta": (stored or {}).get("delta") if isinstance(stored, dict) else None,
        "archive": stored if isinstance(stored, dict) else None,
        "feeFlow": token.get("feeFlow"),
        "devRisk": token.get("devRisk"),
        "tickerUniqueness": token.get("tickerUniqueness"),
        "bundleSniper": token.get("bundleSniper"),
        "message": (
            f"Fast lab multi-source scan in {wall_ms:.0f}ms "
            f"(pump+dex+rugcheck parallel). Use mode=deep for full report."
        ),
    }
    _mem_cache[mint] = (time.time(), payload)
    # prune
    if len(_mem_cache) > 200:
        oldest = sorted(_mem_cache.items(), key=lambda x: x[1][0])[:50]
        for k, _ in oldest:
            _mem_cache.pop(k, None)
    return payload


async def lab_deep_scan(mint: str, *, force: bool = False) -> dict[str, Any]:
    """Deeper lab: full rugcheck report + fast path sources in parallel."""
    mint = mint.strip()
    t_wall = time.perf_counter()

    if not force and mint in _mem_cache:
        ts, payload = _mem_cache[mint]
        if time.time() - ts < _MEM_TTL_DEEP and payload.get("mode") == "deep":
            out = dict(payload)
            out["served_from"] = "memory_cache"
            return out

    results = await asyncio.gather(
        _timed("pump", _fetch_pump(mint), 3.0),
        _timed("dex", _fetch_dex(mint), 2.8),
        _timed("rug_summary", _fetch_rug_summary(mint), 3.5),
        _timed("rug_full", _fetch_rug_full(mint), 5.5),
    )
    timings: dict[str, float] = {}
    errors: dict[str, str] = {}
    pump = dex = rug_sum = rug_full = None
    for name, val, ms, err in results:
        timings[name] = ms
        if err:
            errors[name] = err
        elif name == "pump":
            pump = val
        elif name == "dex":
            dex = val
        elif name == "rug_summary":
            rug_sum = val
        elif name == "rug_full":
            rug_full = val

    if isinstance(rug_sum, dict) and (
        rug_sum.get("_rate_limited") or rug_sum.get("_empty") or rug_sum.get("_error")
    ):
        errors["rug_summary"] = str(
            rug_sum.get("_error")
            or ("rate_limited" if rug_sum.get("_rate_limited") else "empty")
        )
        rug_sum = None

    safety: dict[str, Any] = {}
    if rug_sum:
        try:
            safety = _sol._parse_response(
                mint,
                rug_sum if isinstance(rug_sum, dict) else {},
                rug_full if isinstance(rug_full, dict) else {},
                pump_coin=pump,
            )
        except Exception as exc:
            errors["rug_parse"] = str(exc)[:80]
    if not safety and pump:
        safety = _sol._pumpfun_fallback(mint, pump)
        errors.setdefault("rugcheck", "fallback_pump")

    wall_ms = (time.perf_counter() - t_wall) * 1000
    token = _build_result(
        mint,
        pump=pump,
        dex=dex,
        safety=safety or {"mint": mint, "error": True, "top_holders": []},
        mode="deep",
        timings=timings,
        source_errors=errors,
        wall_ms=wall_ms,
    )
    try:
        stored = get_archive().store(token, store_raw=False)
    except Exception as exc:
        stored = {"ok": False, "error": str(exc)[:80]}

    payload = {
        "ok": True,
        "served_from": "lab_deep",
        "mode": "deep",
        "mint": mint,
        "latency_ms": round(wall_ms, 1),
        "timings_ms": {k: round(v, 1) for k, v in timings.items()},
        "source_errors": errors,
        "cockpit": token.get("cockpit"),
        "delta": (stored or {}).get("delta") if isinstance(stored, dict) else None,
        "archive": stored if isinstance(stored, dict) else None,
        "feeFlow": token.get("feeFlow"),
        "devRisk": token.get("devRisk"),
        "tickerUniqueness": token.get("tickerUniqueness"),
        "bundleSniper": token.get("bundleSniper"),
        "message": f"Deep lab scan in {wall_ms:.0f}ms (full RugCheck when available).",
    }
    _mem_cache[mint] = (time.time(), payload)
    return payload


async def lab_analyze_smart(
    mint: str,
    *,
    force: bool = False,
    mode: str = "fast",
) -> dict[str, Any]:
    """Entry: archive freshness → memory → live fast/deep."""
    mint = mint.strip()
    mode = (mode or "fast").lower()
    if mode not in ("fast", "deep"):
        mode = "fast"

    arch = get_archive()

    # Cheap dex probe for freshness (skip if force)
    live_liq = None
    if not force:
        try:
            _, dex, ms, err = await _timed("dex_probe", _fetch_dex(mint), 2.0)
            if dex and isinstance(dex, dict):
                pair = dex.get("pair") or {}
                if isinstance(pair.get("liquidity"), dict):
                    live_liq = _f(pair["liquidity"].get("usd")) or None
            # memory after probe
            if mint in _mem_cache:
                ts, payload = _mem_cache[mint]
                ttl = _MEM_TTL_DEEP if payload.get("mode") == "deep" else _MEM_TTL_FAST
                if time.time() - ts < ttl:
                    # still check archive drift if we have liq
                    if live_liq is not None:
                        fresh, meta = arch.freshness_ok(mint, live_liq)
                        if fresh or time.time() - ts < 20:
                            out = dict(payload)
                            out["served_from"] = "memory_cache"
                            out["freshness"] = meta if live_liq is not None else {
                                "reason": "hot_memory"
                            }
                            out["cache_age_sec"] = round(time.time() - ts, 1)
                            return out
                    else:
                        out = dict(payload)
                        out["served_from"] = "memory_cache"
                        out["cache_age_sec"] = round(time.time() - ts, 1)
                        return out
            if live_liq is not None:
                fresh, meta = arch.freshness_ok(mint, live_liq)
                if fresh:
                    latest = arch.latest(mint)
                    if latest:
                        return {
                            "ok": True,
                            "served_from": "archive",
                            "mode": "archive",
                            "freshness": meta,
                            "latency_ms": round(ms, 1),
                            "message": (
                                "Liquidity stable (<10% drift) — archive hit. "
                                "Force rescan for a new snapshot."
                            ),
                            "scan": latest,
                            "cockpit": latest.get("cockpit"),
                            "mint": mint,
                        }
        except Exception:
            pass

    if mode == "deep":
        return await lab_deep_scan(mint, force=force)
    return await lab_fast_scan(mint, force=force)
