"""Single-token analysis pipeline — extracted from main.py (behavior-preserving)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import HTTPException

from config import (
    DUMP_HIDE_FRAC,
    EVM_CHAIN_IDS,
    FAST_SCAN_SKIP_DEX_ORDERS,
    MIGRATION_MCAP_MAX_USD,
    NEAR_ATH_BUY_FRAC,
    SCAN_MCAP_MAX_USD,
)
from services.alpha_setup import analyze_alpha_setup
from services.avoid_filters import BLOCKED_MINTS, analyze_avoid_flags
from services.checker_hub import run_checker_hub
from services.deep_analysis import build_deep_analysis
from services.dexscreener import DexScreenerClient
from services.evm_analyzer import EVMAnalyzer
from services.migration_path import analyze_migration_path
from services.padre import PadreClient
from services.pumpfun import PumpFunClient
from services.scorer import compute_moon_score
from services.signals import (
    generate_entry_signal,
    generate_exit_signal,
    generate_invest_signal,
)
from services.smart_money import (
    analyze_smart_money,
    analyze_smart_money_async,
)
from services.social_signals import analyze_social_narrative
from services.solana_analyzer import SolanaAnalyzer

logger = logging.getLogger("moon-scanner.analyze_token")

_dex = DexScreenerClient()
_pump = PumpFunClient()
_padre = PadreClient()
_evm = EVMAnalyzer()
_sol = SolanaAnalyzer()
_learning: Any = None
_analyze_sem: asyncio.Semaphore | None = None


def bind_learning(engine: Any) -> None:
    """Wire LearningEngine from app startup (same DB as main)."""
    global _learning
    _learning = engine


def _get_analyze_sem() -> asyncio.Semaphore:
    global _analyze_sem
    if _analyze_sem is None:
        from config import ANALYZE_CONCURRENCY

        _analyze_sem = asyncio.Semaphore(max(1, int(ANALYZE_CONCURRENCY)))
    return _analyze_sem


def format_pair_summary(pair: dict) -> dict:
    txns_m5 = (pair.get("txns") or {}).get("m5") or {}
    txns_h1 = (pair.get("txns") or {}).get("h1") or {}
    txns_h24 = (pair.get("txns") or {}).get("h24") or {}
    pump_coin = pair.get("pumpfun") or {}

    age_hours = None
    age_minutes = None
    if pair.get("pairCreatedAt"):
        age_ms = time.time() * 1000 - pair["pairCreatedAt"]
        age_hours = round(age_ms / 3_600_000, 2)
        age_minutes = round(age_ms / 60_000, 1)
    elif pump_coin.get("created_timestamp"):
        age_ms = time.time() * 1000 - pump_coin["created_timestamp"]
        age_hours = round(age_ms / 3_600_000, 2)
        age_minutes = round(age_ms / 60_000, 1)

    summary = {
        "pairAddress": pair.get("pairAddress"),
        "dexId": pair.get("dexId"),
        "url": pair.get("url"),
        "priceUsd": pair.get("priceUsd"),
        "priceChange": pair.get("priceChange"),
        "volume": pair.get("volume"),
        "liquidity": pair.get("liquidity"),
        "marketCap": pair.get("marketCap"),
        "fdv": pair.get("fdv"),
        "pairCreatedAt": pair.get("pairCreatedAt"),
        "baseToken": pair.get("baseToken"),
        "quoteToken": pair.get("quoteToken"),
        "txns_m5": txns_m5,
        "txns_h1": txns_h1,
        "txns_h24": txns_h24,
        "is_pumpfun_synthetic": bool(pair.get("is_pumpfun_synthetic")),
        "age_hours": age_hours,
        "age_minutes": age_minutes,
        "is_pumpfun": bool(pump_coin),
    }

    if pump_coin:
        summary["pumpfun"] = {
            "mint": pump_coin.get("mint"),
            "name": pump_coin.get("name"),
            "symbol": pump_coin.get("symbol"),
            "description": pump_coin.get("description"),
            "twitter": pump_coin.get("twitter"),
            "website": pump_coin.get("website"),
            "telegram": pump_coin.get("telegram"),
            "bonding_progress": round(PumpFunClient.bonding_progress(pump_coin), 1),
            "usd_market_cap": pump_coin.get("usd_market_cap"),
            "ath_market_cap": pump_coin.get("ath_market_cap"),
            "ath_market_cap_timestamp": pump_coin.get("ath_market_cap_timestamp"),
            "created_timestamp": pump_coin.get("created_timestamp"),
            "reply_count": pump_coin.get("reply_count", 0),
            "complete": pump_coin.get("complete", False),
            "creator": pump_coin.get("creator"),
            "real_sol_reserves": pump_coin.get("real_sol_reserves"),
            "pump_url": f"https://pump.fun/coin/{pump_coin.get('mint', '')}",
        }

    return summary


def padre_links(chain_id: str, token_address: str) -> dict[str, str]:
    return {
        "trade": _padre.trade_url(chain_id, token_address),
        "trenches": _padre.trenches_url(),
        "new_pairs": _padre.new_pairs_url(),
    }


async def resolve_pair(
    chain_id: str, token_address: str, candidate: dict | None = None
) -> dict:
    cand = candidate or {}
    pump_coin = cand.get("pumpfun")
    if not pump_coin and chain_id == "solana":
        pump_coin = await _pump.get_coin(token_address)

    dex_pair = cand.get("_dex_pair")
    if not dex_pair:
        pairs = await _dex.get_token_pairs(chain_id, token_address)
        dex_pair = _dex.pick_best_pair(pairs)

    if pump_coin:
        return _pump.to_market_pair(pump_coin, dex_pair)

    if dex_pair:
        return dex_pair

    raise HTTPException(404, f"No market data for {token_address}")


def token_mcap(pair: dict, candidate: dict | None = None) -> float:
    pump = (pair or {}).get("pumpfun") or (candidate or {}).get("pumpfun") or {}
    try:
        return float(
            pump.get("usd_market_cap")
            or (pair or {}).get("marketCap")
            or (pair or {}).get("fdv")
            or (candidate or {}).get("_mcap")
            or (candidate or {}).get("marketCap")
            or 0
        )
    except (TypeError, ValueError):
        return 0.0


async def analyze_token(
    chain_id: str,
    token_address: str,
    candidate: dict | None = None,
    *,
    fast: bool = False,
) -> dict:
    """Full token analysis — global concurrency limited (anti-amp)."""
    async with _get_analyze_sem():
        return await _analyze_token_body(
            chain_id, token_address, candidate, fast=fast
        )


async def _analyze_token_body(
    chain_id: str,
    token_address: str,
    candidate: dict | None = None,
    *,
    fast: bool = False,
) -> dict:
    mint = (token_address or "").strip()
    if mint in BLOCKED_MINTS:
        return {
            "skipped": True,
            "skipReason": "Hard-blocked mint (known dump / rug)",
            "tokenAddress": mint,
            "mcap_usd": token_mcap({}, candidate) if candidate else 0,
        }
    # Early drop: skip heavy analysis if feed already shows mcap too high
    if candidate:
        pre_mcap = token_mcap({}, candidate)
        cand_pf = (candidate or {}).get("pumpfun") or {}
        pre_ath = float(
            cand_pf.get("ath_market_cap")
            or candidate.get("ath_market_cap")
            or candidate.get("_ath_mcap")
            or 0
        )
        if pre_mcap > SCAN_MCAP_MAX_USD and (candidate or {}).get("column") not in (
            "almost_bonded",
            "recently_bonded",
            "under_25k",
        ):
            # allow migration columns higher via col cap later
            if pre_mcap > MIGRATION_MCAP_MAX_USD:
                return {
                    "chainId": chain_id,
                    "tokenAddress": token_address,
                    "skipped": True,
                    "skipReason": f"mcap ${pre_mcap:,.0f} too high",
                    "mcap_usd": pre_mcap,
                    "analyzedAt": time.time(),
                }
        # Instant dump skip before RugCheck (speed + accuracy)
        if pre_ath >= 3_000 and pre_mcap > 0 and pre_mcap < pre_ath * DUMP_HIDE_FRAC:
            return {
                "chainId": chain_id,
                "tokenAddress": token_address,
                "skipped": True,
                "skipReason": (
                    f"Already dumped −{(1 - pre_mcap / pre_ath) * 100:.0f}% "
                    f"(${pre_ath:,.0f}→${pre_mcap:,.0f})"
                ),
                "mcap_usd": pre_mcap,
                "analyzedAt": time.time(),
            }

    # Market resolve + Padre (timeout Padre so bulk doesn't stall)
    async def _padre_safe():
        try:
            return await asyncio.wait_for(
                _padre.get_token_audit(chain_id, token_address),
                timeout=2.5 if fast else 5.0,
            )
        except Exception:
            return None

    pair, padre_audit = await asyncio.gather(
        resolve_pair(chain_id, token_address, candidate),
        _padre_safe(),
    )
    pump_coin = pair.get("pumpfun") or (candidate or {}).get("pumpfun")
    mcap_for_sm = token_mcap(pair, candidate)
    ath_live = float((pump_coin or {}).get("ath_market_cap") or 0)

    # Dump skip after live resolve
    if ath_live >= 3_000 and mcap_for_sm > 0 and mcap_for_sm < ath_live * DUMP_HIDE_FRAC:
        return {
            "chainId": chain_id,
            "tokenAddress": token_address,
            "skipped": True,
            "skipReason": (
                f"Already dumped −{(1 - mcap_for_sm / ath_live) * 100:.0f}% "
                f"(ATH ${ath_live:,.0f}→${mcap_for_sm:,.0f})"
            ),
            "mcap_usd": mcap_for_sm,
            "market": format_pair_summary(pair),
            "analyzedAt": time.time(),
        }

    # Late drop after live mcap resolve
    if mcap_for_sm > MIGRATION_MCAP_MAX_USD:
        return {
            "chainId": chain_id,
            "tokenAddress": token_address,
            "skipped": True,
            "skipReason": f"live mcap ${mcap_for_sm:,.0f} > ${MIGRATION_MCAP_MAX_USD:,.0f}",
            "mcap_usd": mcap_for_sm,
            "market": format_pair_summary(pair),
            "analyzedAt": time.time(),
        }

    if chain_id == "solana":
        safety = await _sol.analyze(
            token_address,
            pump_coin=pump_coin,
            padre_audit=padre_audit,
            fast=fast,
        )
    elif chain_id in EVM_CHAIN_IDS:
        safety = await _evm.analyze(
            chain_id, token_address, pair.get("pairAddress")
        )
        if padre_audit:
            parsed = PadreClient.parse_audit(padre_audit)
            safety["padre"] = parsed
            if parsed.get("honeypot"):
                safety["is_honeypot"] = True
                safety["passed"] = False
            safety.setdefault("issues", []).extend(parsed.get("issues", []))
    else:
        safety = {
            "passed": False,
            "issues": [f"Unsupported chain: {chain_id}"],
            "type": "unknown",
        }

    moon = compute_moon_score(safety, pair, early_mode=True)
    entry = generate_entry_signal(safety, pair, moon, early_mode=True)
    exit_sig = generate_exit_signal(safety, pair, moon)

    sources = (candidate or {}).get("sources") or []
    if not sources and (candidate or {}).get("source"):
        sources = [
            s.strip()
            for s in (candidate or {})["source"].split(",")
            if s.strip()
        ]
    invest = generate_invest_signal(
        safety, pair, moon, sources=sources, early_mode=True
    )
    trench = invest.get("trench")

    base_token = pair.get("baseToken") or {}
    social = analyze_social_narrative(
        pump_coin=pump_coin,
        name=base_token.get("name", ""),
        symbol=base_token.get("symbol", ""),
        description=(candidate or {}).get("description")
        or (pump_coin or {}).get("description", ""),
        links=(candidate or {}).get("links")
        or ((pump_coin and PumpFunClient.to_candidate(pump_coin).get("links")) or None),
    )

    # Re-run avoid with full pump ATH + dex sell pressure (catches Baby Corn P&Ds)
    avoid = analyze_avoid_flags(
        safety, pump_coin, mint=token_address, pair=pair
    )
    safety["avoid"] = avoid
    if avoid.get("avoid"):
        safety["passed"] = False
        for reason in avoid.get("reasons") or []:
            tag = f"AVOID: {reason}"
            if tag not in (safety.get("issues") or []):
                safety.setdefault("issues", []).append(tag)

    checker_hub = run_checker_hub(
        chain_id, token_address, safety, pair=pair, padre_audit=padre_audit
    )

    # Fast bulk scan: skip DexScreener order round-trip
    if fast or FAST_SCAN_SKIP_DEX_ORDERS:
        smart_money = analyze_smart_money(
            safety, pair=pair, mcap_usd=mcap_for_sm, dex_orders=None
        )
    else:
        smart_money = await analyze_smart_money_async(
            safety,
            pair,
            chain_id,
            token_address,
            mcap_usd=mcap_for_sm,
        )

    alpha = analyze_alpha_setup(
        safety=safety,
        pair=pair,
        pump=pump_coin,
        social=social,
        smart_money=smart_money,
        mcap_usd=mcap_for_sm,
    )
    migration = analyze_migration_path(
        mcap_usd=mcap_for_sm,
        bonding_progress=(pump_coin or {}).get("bonding_progress"),
        safety=safety,
        pair=pair,
        pump=pump_coin,
        avoid=(safety.get("avoid") or {}),
        alpha=alpha,
        complete=bool((pump_coin or {}).get("complete")),
    )
    # Capital protection: default WATCH. BUY only multi-gate deep analysis.
    bond_pct = float(migration.get("bonding_pct") or 0)
    ath_now = float((pump_coin or {}).get("ath_market_cap") or ath_live or 0)
    near_ath = ath_now <= 0 or (
        mcap_for_sm > 0 and mcap_for_sm >= ath_now * NEAR_ATH_BUY_FRAC
    )
    tx_act = alpha.get("txActivity") or {}
    invest = dict(invest or {})
    # Strip any generic STRONG from base signals
    if invest.get("signal") in ("STRONG_INVEST", "INVEST"):
        invest["signal"] = "WATCH"
        invest["summary"] = "Default WATCH — multi-gate buy not verified yet."

    deep = build_deep_analysis(
        mcap=mcap_for_sm,
        safety=safety,
        pair=pair,
        pump=pump_coin,
        alpha=alpha,
        migration=migration,
        avoid=safety.get("avoid"),
        smart_money=smart_money,
        social=social,
    )

    if deep.get("verdict") == "BUY" and deep.get("buy_ready"):
        invest["signal"] = "STRONG_INVEST"
        invest["confidence"] = deep.get("confidence")
        invest["summary"] = (
            f"BUY gate passed ({deep.get('gates_passed')}/{deep.get('gates_total')}). "
            f"{deep.get('summary')} · {deep.get('position_advice')}"
        )
        invest["deep_buy"] = True
    elif deep.get("verdict") == "SKIP" or deep.get("dump", {}).get("is_dumped"):
        invest["signal"] = "AVOID"
        invest["confidence"] = deep.get("confidence") or 85
        invest["summary"] = deep.get("summary") or "Skip — dump/risk"
    else:
        invest["signal"] = "WATCH"
        invest["confidence"] = min(50, int(deep.get("confidence") or 40))
        invest["summary"] = (
            f"WATCH only ({deep.get('gates_passed')}/{deep.get('gates_total')} gates). "
            f"Do not size up. {deep.get('position_advice')}"
        )
    invest["migration_lane"] = migration.get("lane")
    invest["ceiling"] = alpha.get("ceiling_label")
    invest["reasons"] = (deep.get("why") or [])[:6] + (deep.get("risks") or [])[:4]

    # Keep unused locals for parity with prior main.py (gates may use later)
    _ = (bond_pct, near_ath, tx_act)

    links = padre_links(chain_id, token_address)

    result = {
        "chainId": chain_id,
        "tokenAddress": token_address,
        "safety": safety,
        "market": format_pair_summary(pair),
        "moonScore": moon,
        "entrySignal": entry,
        "exitSignal": exit_sig,
        "investSignal": invest,
        "trenchAnalysis": trench,
        "socialSignals": social,
        "smartMoney": smart_money,
        "alphaSetup": alpha,
        "migrationPath": migration,
        "deepAnalysis": deep,
        "checkerHub": checker_hub,
        "padre": links,
        "analyzedAt": time.time(),
        "source": (candidate or {}).get("source", ""),
        "sources": sources,
        "icon": (candidate or {}).get("icon", ""),
        "description": (candidate or {}).get("description", ""),
        "mcap_usd": mcap_for_sm,
        "ath_mcap": ath_now or None,
    }
    try:
        if _learning is not None:
            trade_plan = _learning.observe_analysis(result)
            if trade_plan:
                # Force plan to match multi-gate deep analysis (protect capital)
                trade_plan = dict(trade_plan)
                if deep.get("verdict") == "BUY" and deep.get("buy_ready"):
                    trade_plan["action"] = "ENTER"
                    trade_plan["summary"] = invest.get("summary")
                    trade_plan["confidence"] = deep.get("confidence")
                else:
                    if trade_plan.get("action") == "ENTER":
                        trade_plan["action"] = (
                            "WATCH" if deep.get("verdict") == "WATCH" else "SKIP"
                        )
                    trade_plan["summary"] = (
                        f"Gate override: {deep.get('summary')}. "
                        + (trade_plan.get("summary") or "")
                    )
                trade_plan["deepAnalysis"] = {
                    "verdict": deep.get("verdict"),
                    "gates": f"{deep.get('gates_passed')}/{deep.get('gates_total')}",
                    "dump": deep.get("dump"),
                    "tx": deep.get("tx_interest"),
                }
                result["tradePlan"] = trade_plan
                if trade_plan.get("action") == "SKIP":
                    invest = dict(invest)
                    invest["signal"] = "AVOID"
                    invest["summary"] = trade_plan.get("summary")
                    result["investSignal"] = invest
    except Exception as exc:
        logger.debug("learning observe failed: %s", exc)
    result["deepAnalysis"] = deep
    result["investSignal"] = invest
    return result
