"""Continuously observe tokens from discovery → dev dump → crash → outcome."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

# time used in finalize path

import httpx

from config import REQUEST_TIMEOUT, USER_AGENT
from services.learning.features import extract_features
from services.learning.memory import LearningMemory
from services.learning.predictor import predict_trade

logger = logging.getLogger("moon-scanner.learning")

PUMP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Origin": "https://pump.fun",
    "Referer": "https://pump.fun/",
}


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def classify_outcome(
    *,
    first_mcap: float,
    ath_mcap: float,
    last_mcap: float,
    avoid_flags: list[str] | None = None,
    rugged: bool = False,
    creator_dumped: bool = False,
    graduated: bool = False,
    mins_to_ath: float | None = None,
) -> str:
    """Label lifecycle for learning — prefer absolute ATH + dump depth."""
    flags = set(avoid_flags or [])
    if rugged or "rugged" in flags or "honeypot" in flags:
        return "RUGGED"
    if any(
        f in flags
        for f in (
            "flash_pump_dump",
            "social_spoof_scam",
            "adult_bait",
            "entry_trap_social",
            "blocklist",
            "spam_deploy_tool",
        )
    ):
        return "SCAM"
    if first_mcap <= 0:
        first_mcap = max(last_mcap, 1)
    mult = ath_mcap / first_mcap if first_mcap > 0 else 0
    crash = last_mcap / ath_mcap if ath_mcap > 0 else 1

    # Flash pump-dump: ATH in under 5m then −55%+
    if (
        mins_to_ath is not None
        and mins_to_ath <= 5
        and ath_mcap >= 5_000
        and crash <= 0.55
    ):
        return "SCAM"

    # Absolute ATH tiers — multi‑$M first
    if ath_mcap >= 10_000_000 and mult >= 1.5:
        return "MEGA"
    if ath_mcap >= 1_000_000 and mult >= 1.5:
        return "SUPER"
    if ath_mcap >= 100_000 and mult >= 2.0:
        return "SUPER"
    if ath_mcap >= 50_000 and mult >= 2.0:
        return "WINNER"
    # Graduated with real multiple still counts as WINNER/RUNNER
    if graduated and ath_mcap >= 40_000 and mult >= 2.0:
        return "WINNER"
    if mult >= 3.0 and crash > 0.22:
        return "WINNER"
    if mult >= 1.8 and crash > 0.18:
        return "RUNNER"
    if crash <= 0.45 or (creator_dumped and crash <= 0.6):
        return "DUMP"
    if any(f in flags for f in ("drained_curve", "creator_dumped", "sell_pressure", "post_ath_crash")):
        return "DUMP"
    return "NEUTRAL"


class LearningEngine:
    def __init__(self, memory: LearningMemory) -> None:
        self.memory = memory
        self._seen_creator_zero: set[str] = set()

    def observe_analysis(self, result: dict[str, Any]) -> dict[str, Any]:
        """Call on every full token analysis — learn entry features + predict."""
        if result.get("skipped"):
            return {}
        mint = result.get("tokenAddress") or ""
        if not mint:
            return {}
        safety = result.get("safety") or {}
        market = result.get("market") or {}
        pump = market.get("pumpfun") or {}
        pair_like = {
            "priceUsd": market.get("priceUsd"),
            "marketCap": market.get("marketCap") or market.get("fdv"),
            "volume": market.get("volume"),
            "txns": {
                "m5": market.get("txns_m5") or {},
                "h1": market.get("txns_h1") or {},
            },
            "priceChange": market.get("priceChange"),
            "pumpfun": pump,
            "liquidity": market.get("liquidity"),
        }
        mcap = _f(
            result.get("mcap_usd")
            or pump.get("usd_market_cap")
            or market.get("marketCap")
        )
        price = _f(market.get("priceUsd"))
        base = market.get("baseToken") or {}
        name = base.get("name") or safety.get("token_name") or ""
        symbol = base.get("symbol") or safety.get("token_symbol") or ""

        mig = result.get("migrationPath") or {}
        runner = result.get("runnerRadar") or {}
        if not runner:
            try:
                from services.runner_radar import score_runner_candidate

                runner = score_runner_candidate(
                    {
                        "mcap_usd": mcap,
                        "bonding_progress": mig.get("bonding_pct"),
                        "ath_mcap": _f(pump.get("ath_market_cap")),
                        "alphaSetup": result.get("alphaSetup"),
                        "migrationPath": mig,
                        "safety": safety,
                        "safetyReport": {"avoid": safety.get("avoid")},
                        "priceChange": market.get("priceChange"),
                        "age_minutes": market.get("age_minutes"),
                    }
                )
                result["runnerRadar"] = runner
            except Exception:
                runner = {}
        feats = extract_features(
            safety=safety,
            pair=pair_like,
            pump=pump,
            social=result.get("socialSignals"),
            smart_money=result.get("smartMoney"),
            alpha=result.get("alphaSetup"),
            avoid=safety.get("avoid"),
            mcap=mcap,
            migration=mig,
            runner=runner,
        )
        # Track pump ATH on the token row for better outcomes later
        ath_now = _f(pump.get("ath_market_cap"))
        if ath_now > mcap:
            self.memory.upsert_token(
                mint, name=name, symbol=symbol, mcap=ath_now, price=price
            )
        self.memory.upsert_token(
            mint,
            name=name,
            symbol=symbol,
            mcap=mcap,
            price=price,
            features=feats,
        )
        self.memory.add_snapshot(
            mint,
            mcap=mcap,
            price=price,
            holders=int(safety.get("total_holders") or 0),
            creator_pct=_f(safety.get("creator_pct")),
            creator_balance=_f(safety.get("creator_balance")),
            quote_sol=_f(safety.get("lp_quote_sol")),
            buys_m5=int(feats.get("buys_m5") or 0),
            sells_m5=int(feats.get("sells_m5") or 0),
            replies=int(pump.get("reply_count") or 0),
            features=feats,
        )

        # Creator dump detection at observation time
        if (
            safety.get("creator_sold")
            or (
                _f(safety.get("creator_balance")) == 0
                and _f(safety.get("creator_pct")) < 0.05
            )
        ):
            if mint not in self._seen_creator_zero:
                self.memory.mark_creator_dump(mint, mcap)
                self._seen_creator_zero.add(mint)

        pred = predict_trade(
            self.memory,
            safety=safety,
            pair=pair_like,
            pump=pump,
            social=result.get("socialSignals"),
            smart_money=result.get("smartMoney"),
            alpha=result.get("alphaSetup"),
            avoid=safety.get("avoid"),
            migration=mig,
            runner=runner,
            mcap=mcap,
            price=price,
        )

        # Early finalize scams/avoid so model learns immediately
        avoid = safety.get("avoid") or result.get("avoid") or {}
        hard = False
        hard_why = None
        try:
            from services.avoid_filters import is_hard_avoid

            hard, hard_why = is_hard_avoid(result)
            if not hard:
                hard, hard_why = is_hard_avoid({"avoid": avoid})
        except Exception:
            hard = bool(avoid.get("hard_avoid") or avoid.get("hard"))
            hard_why = avoid.get("summary")
        if hard:
            self.memory.finalize_outcome(
                mint,
                "SCAM",
                notes=hard_why or avoid.get("summary") or "hard_avoid",
                features=feats,
            )
        elif avoid.get("avoid") and feats.get("already_crashed"):
            self.memory.finalize_outcome(
                mint,
                "DUMP",
                notes=avoid.get("summary") or "avoid_crashed",
                features=feats,
            )
        elif runner.get("crashed") or feats.get("already_crashed"):
            # Learn dumps as soon as ATH crash is visible
            tok = self.memory.get_token(mint)
            first = _f((tok or {}).get("first_mcap")) or mcap
            ath = max(_f((tok or {}).get("ath_mcap")), ath_now, mcap)
            if ath >= 5_000 and mcap <= ath * 0.45:
                self.memory.finalize_outcome(
                    mint,
                    "DUMP",
                    max_multiple=(ath / first) if first > 0 else 0,
                    notes=runner.get("crash_reason")
                    or f"ath_crash ath={ath:.0f} last={mcap:.0f}",
                    features=feats,
                )

        return pred

    def observe_feed_card(
        self, card: dict[str, Any], *, source: str = "feed"
    ) -> dict[str, Any]:
        """Observe a moon/snipe UI card (lighter than full analyze_token).

        Maps feed card shape into observe_analysis so the model learns from
        what the product actually recommended, not only deep /api/analyze.
        """
        if not isinstance(card, dict):
            return {}
        mint = (card.get("tokenAddress") or card.get("mint") or "").strip()
        if not mint:
            return {}
        # Skip incomplete enrich — unknown safety should not train as "entry"
        if card.get("enrich_ok") is not True:
            return {}

        pump = card.get("pumpfun") or {}
        market = card.get("market") or {}
        safety = card.get("safety") or {}
        avoid = (
            card.get("avoid")
            or (card.get("safetyReport") or {}).get("avoid")
            or safety.get("avoid")
            or {}
        )
        # Normalize avoid onto safety for extract_features
        if avoid and not safety.get("avoid"):
            safety = {**safety, "avoid": avoid}

        mcap = _f(
            card.get("mcap_usd")
            or pump.get("usd_market_cap")
            or market.get("marketCap")
        )
        # Build analyze-like result for observe_analysis
        result = {
            "tokenAddress": mint,
            "mcap_usd": mcap,
            "safety": safety,
            "market": {
                **market,
                "marketCap": market.get("marketCap") or mcap,
                "priceChange": card.get("priceChange") or market.get("priceChange"),
                "txns_m5": (market.get("txns") or {}).get("m5")
                or market.get("txns_m5"),
                "txns_h1": (market.get("txns") or {}).get("h1")
                or market.get("txns_h1"),
                "pumpfun": pump,
                "age_minutes": card.get("age_minutes"),
                "baseToken": {
                    "name": card.get("name") or pump.get("name") or "",
                    "symbol": card.get("symbol") or pump.get("symbol") or "",
                },
            },
            "socialSignals": card.get("socialSignals"),
            "smartMoney": card.get("smartMoney"),
            "alphaSetup": card.get("alphaSetup"),
            "migrationPath": card.get("migrationPath")
            or {
                "bonding_pct": card.get("bonding_progress")
                or pump.get("bonding_progress")
            },
            "runnerRadar": card.get("runnerRadar"),
            "bundleSniper": card.get("bundleSniper"),
            "_feed_source": source,
            "_feed_label": card.get("moon_label")
            or card.get("snipe_label")
            or (card.get("moon") or {}).get("label")
            or (card.get("snipe") or {}).get("label"),
        }
        try:
            pred = self.observe_analysis(result)
            # Always mark observed even if predictor returns empty
            return pred if pred else {"observed": True, "mint": mint, "source": source}
        except Exception as exc:
            logger.debug("observe_feed_card %s: %s", mint[:8], exc)
            return {}

    def observe_feed_cards(
        self, cards: list[dict[str, Any]], *, source: str = "feed", limit: int = 16
    ) -> int:
        """Batch-observe shown feed tokens. Returns count observed."""
        n = 0
        for card in (cards or [])[: max(0, int(limit))]:
            out = self.observe_feed_card(card, source=source)
            if out:
                n += 1
        if n:
            logger.info("Learning observed %s %s feed cards", n, source)
        return n

    async def poll_active(self) -> int:
        """Refresh active tokens from pump.fun; detect dump/crash; finalize."""
        from config import (
            LEARNING_ACTIVE_CAP_PAID,
            LEARNING_ACTIVE_CAP_PUBLIC,
            rpc_is_paid,
        )

        cap = LEARNING_ACTIVE_CAP_PAID if rpc_is_paid() else LEARNING_ACTIVE_CAP_PUBLIC
        mints = self.memory.get_active_mints(limit=cap)
        if not mints:
            return 0
        updated = 0
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, headers=PUMP_HEADERS
        ) as client:
            for mint in mints:
                try:
                    resp = await client.get(
                        f"https://frontend-api-v3.pump.fun/coins/{mint}"
                    )
                    if resp.status_code != 200:
                        continue
                    coin = resp.json()
                    mcap = _f(coin.get("usd_market_cap"))
                    # rough price from mcap/supply not reliable; store mcap focus
                    self.memory.upsert_token(
                        mint,
                        name=coin.get("name") or "",
                        symbol=coin.get("symbol") or "",
                        mcap=mcap,
                        price=0,
                    )
                    real_sol = _f(coin.get("real_sol_reserves")) / 1e9
                    self.memory.add_snapshot(
                        mint,
                        mcap=mcap,
                        holders=0,
                        creator_balance=0,
                        quote_sol=real_sol,
                        replies=int(coin.get("reply_count") or 0),
                    )
                    tok = self.memory.get_token(mint)
                    if not tok:
                        continue
                    first = _f(tok.get("first_mcap"))
                    ath = max(_f(tok.get("ath_mcap")), mcap)
                    age = time.time() - float(tok.get("first_seen") or time.time())
                    ath_hist = _f(coin.get("ath_market_cap"))
                    if ath_hist > ath:
                        ath = ath_hist

                    # Finalize conditions
                    crashed = ath > 0 and mcap > 0 and mcap <= ath * 0.45
                    drained = 0 < real_sol < 0.5 and not coin.get("complete")
                    old = age > 3 * 3600
                    graduated = bool(coin.get("complete"))

                    # Also finalize if still active but clearly dumped from peak
                    soft_crash = ath >= 8_000 and mcap > 0 and mcap <= ath * 0.40
                    if crashed or drained or old or graduated or soft_crash:
                        ath_ts = _f(coin.get("ath_market_cap_timestamp"))
                        created = _f(coin.get("created_timestamp"))
                        mins_to_ath = None
                        if ath_ts and created and ath_ts >= created:
                            mins_to_ath = (ath_ts - created) / 60_000
                        outcome = classify_outcome(
                            first_mcap=first or mcap,
                            ath_mcap=ath,
                            last_mcap=mcap,
                            creator_dumped=bool(tok.get("creator_dump_ts")),
                            graduated=graduated,
                            mins_to_ath=mins_to_ath,
                        )
                        notes = (
                            f"ath={ath:.0f} last={mcap:.0f} "
                            f"dump_mcap={tok.get('creator_dump_mcap')} "
                            f"grad={graduated}"
                        )
                        feats = None
                        if tok.get("entry_features"):
                            import json

                            try:
                                feats = json.loads(tok["entry_features"])
                            except Exception:
                                feats = None
                        self.memory.finalize_outcome(
                            mint,
                            outcome,
                            max_multiple=(ath / first) if first > 0 else 0,
                            notes=notes,
                            features=feats,
                        )
                    updated += 1
                except Exception as exc:
                    logger.debug("poll %s failed: %s", mint[:8], exc)
                await asyncio.sleep(0.05)
        return updated

    def seed_known_examples(self, force: bool = False) -> int:
        """Seed model with documented winners/scams + historical $10M+ megas."""
        from services.learning.mega_seeds import MEGA_SEEDS, MEGA_SEEDS_VERSION

        seeds: list[dict] = [
            # scams user flagged
            {
                "mint": "62pzwoXyHi5Z1iEdD67RDPTT12spZ4ph8WsLU5y8pump",
                "name": "Baby Corn",
                "symbol": "Corn",
                "first_mcap": 6000,
                "ath_mcap": 10806,
                "outcome": "SCAM",
                "features": {
                    "fake_twitter": 1,
                    "fake_website": 1,
                    "holders_lt_15": 1,
                    "mid_bags_lt_3": 1,
                    "curve_sol_drained": 1,
                    "sell_pressure": 1,
                    "mcap_bin:sweet_3.5_7.5k": 1,
                },
            },
            {
                "mint": "5ocgBRqLyQxZEvtAYcX1nXeVhAj1cuCHi2ZfSZKVpump",
                "name": "CEO of Sex",
                "symbol": "CEOSex",
                "first_mcap": 6500,
                "ath_mcap": 7993,
                "outcome": "SCAM",
                "features": {
                    "fake_twitter": 1,
                    "adult_bait": 1,
                    "holders_lt_15": 1,
                    "mid_bags_lt_3": 1,
                    "curve_sol_drained": 1,
                    "sell_pressure": 1,
                    "mcap_bin:sweet_3.5_7.5k": 1,
                },
            },
            {
                "mint": "4GTkEsYhegrJmbAiiUe9TrsQrTrqx7n1jDMSH5GGpump",
                "name": "Point Of Dog",
                "symbol": "POD",
                "first_mcap": 4000,
                "ath_mcap": 5000,
                "outcome": "DUMP",
                "features": {
                    "fake_twitter": 1,
                    "holders_lt_15": 1,
                    "curve_sol_drained": 1,
                    "creator_sold": 1,
                },
            },
            {
                "mint": "FAAnKpATxZuWWsCbxWZ5yaNn9CyCj4d9Wnqzhhdqpump",
                "name": "The Jeet",
                "symbol": "Raccoon",
                "first_mcap": 4000,
                "ath_mcap": 5000,
                "outcome": "SCAM",
                "features": {
                    "spam_deploy_tool": 1,
                    "curve_sol_drained": 1,
                    "creator_sold": 1,
                    "holders_lt_15": 1,
                },
            },
            {
                "mint": "BD42EGwRsQArB2SKwgdqPzjsBbme963ZrR9sioTopump",
                "name": "Dregg",
                "symbol": "DREGG",
                "first_mcap": 2100,
                "ath_mcap": 2100,
                "outcome": "SCAM",
                "features": {
                    "suspicious_metadata": 1,
                    "holders_lt_15": 1,
                    "mid_bags_lt_3": 1,
                    "mcap_bin:under_3.5k": 1,
                },
            },
            {
                "mint": "BTU78ZNs11eDYsaUXysXnEPEJrCDYDobAkTfQQafpump",
                "name": "USWR",
                "symbol": "USWR",
                "first_mcap": 6000,
                "ath_mcap": 12000,
                "outcome": "SCAM",
                "features": {
                    "own_twitter": 1,
                    "real_website": 1,
                    "holders_lt_15": 1,
                    "mid_bags_lt_3": 1,
                    "curve_sol_drained": 1,
                    "buy_ratio_ge_1.3": 1,
                    "mcap_bin:sweet_3.5_7.5k": 1,
                },
                "notes": "all-green wash / empty float",
            },
            {
                "mint": "Bw1gX5ih2DJFtXggXnnGbWqqpBte1uvb9jurUSecpump",
                "name": "Cashoty",
                "symbol": "CASHOTY",
                "first_mcap": 6000,
                "ath_mcap": 25614,
                "outcome": "SCAM",
                "features": {
                    "fake_twitter": 1,
                    "has_desc": 0,
                    "already_crashed": 1,
                    "fading_from_ath": 1,
                    "mcap_bin:lottery_dies_under_7k": 1,
                    "buy_ratio_ge_1.3": 1,
                },
                "notes": "status X + empty desc, ATH 25k then dump to 2k",
            },
            {
                "mint": "9Sj7Yi6oYCATrjC68or2Rqk3D6YkgKaqc9UepDogpump",
                "name": "CUBEMAN",
                "symbol": "CUBEMAN",
                "first_mcap": 5000,
                "ath_mcap": 15000,
                "outcome": "SCAM",
                "features": {
                    "holders_ge_40": 1,
                    "mid_bags_ge_5": 1,
                    "creator_sold": 1,
                    "buy_ratio_ge_1.3": 1,
                    "mcap_bin:sweet_3.5_7.5k": 1,
                },
                "notes": "AI pitch + zero socials + wash",
            },
        ]
        # Historical multi‑$M tokens (idealized early fingerprint)
        seeds.extend(MEGA_SEEDS)

        ver = self.memory.get_meta("mega_seeds_version")
        reseed_megas = force or ver != MEGA_SEEDS_VERSION
        n = 0
        for s in seeds:
            existing = self.memory.get_token(s["mint"])
            is_mega_seed = s.get("outcome") in ("MEGA", "SUPER") or (
                float(s.get("ath_mcap") or 0) >= 1_000_000
            )
            already_same = (
                existing
                and existing.get("outcome") == s["outcome"]
                and float(existing.get("ath_mcap") or 0) >= float(s.get("ath_mcap") or 0) * 0.5
            )
            if already_same and not force:
                continue
            inject_stats = not (
                existing and existing.get("outcome") == s["outcome"]
            )
            # Build feature dict for storage + key extraction
            feats = {
                "has_viral": 0,
                "own_twitter": 0,
                "real_website": 0,
                "fake_twitter": 0,
                "fake_website": 0,
                "adult_bait": 0,
                "creator_sold": 0,
                "mcap_bin": "sweet_3.5_7.5k",
                "alpha_bin": "alpha_high" if is_mega_seed else "alpha_low",
                "sniper_risk": "low",
                "holders": 80 if is_mega_seed else 10,
                "mid_bags": 6 if is_mega_seed else 0,
                "quote_sol": 12.0 if is_mega_seed else 0.2,
                "buy_ratio": 1.6 if is_mega_seed else 0.9,
                "buys_m5": 20 if is_mega_seed else 5,
                "sells_m5": 12 if is_mega_seed else 50,
                "organic_two_way": 1 if is_mega_seed else 0,
                "clean_social_stack": 1 if is_mega_seed else 0,
                "deep_curve_sol": 1 if is_mega_seed else 0,
                "solid_distribution": 1 if is_mega_seed else 0,
                "external_narrative": 1 if is_mega_seed else 0,
                "mega_fingerprint": "MEGA_10M" if is_mega_seed else "NONE",
            }
            for k, v in s["features"].items():
                if ":" in k:
                    base, val = k.split(":", 1)
                    feats[base] = val
                elif k.endswith(
                    (
                        "_ge_5",
                        "_ge_3",
                        "_ge_100",
                        "_ge_40",
                        "_ge_15",
                        "_lt_15",
                        "_lt_3",
                        "_ge_1.3",
                        "_drained",
                    )
                ):
                    pass
                else:
                    feats[k] = v
            for bk in (
                "has_viral",
                "own_twitter",
                "real_website",
                "fake_twitter",
                "fake_website",
                "adult_bait",
                "creator_sold",
                "organic_two_way",
                "clean_social_stack",
                "deep_curve_sol",
                "solid_distribution",
                "external_narrative",
            ):
                if s["features"].get(bk):
                    feats[bk] = 1
            self.memory.upsert_token(
                s["mint"],
                name=s["name"],
                symbol=s["symbol"],
                mcap=s["first_mcap"],
                features=feats,
                force_new_features=True,
            )
            self.memory.upsert_token(
                s["mint"], name=s["name"], symbol=s["symbol"], mcap=s["ath_mcap"]
            )
            with self.memory._lock:
                conn = self.memory._conn()
                try:
                    mult = s["ath_mcap"] / max(s["first_mcap"], 1)
                    from services.learning.features import feature_keys_for_learning

                    keys = set(feature_keys_for_learning(feats))
                    for fk, val in s["features"].items():
                        if isinstance(val, int) and val == 0:
                            continue
                        if isinstance(val, int) and val == 1:
                            keys.add(fk)
                        elif ":" in fk:
                            keys.add(fk)
                    if inject_stats:
                        for key in keys:
                            conn.execute(
                                """
                                INSERT INTO feature_stats(feature, outcome, count, sum_multiple)
                                VALUES(?,?,1,?)
                                ON CONFLICT(feature, outcome) DO UPDATE SET
                                    count = count + 1,
                                    sum_multiple = sum_multiple + excluded.sum_multiple
                                """,
                                (key, s["outcome"], mult),
                            )
                    notes = s.get("notes") or "seed"
                    conn.execute(
                        """
                        UPDATE tokens SET outcome=?, outcome_ts=?, max_multiple=?,
                            active=0, notes=?, first_mcap=?, ath_mcap=?
                        WHERE mint=?
                        """,
                        (
                            s["outcome"],
                            time.time(),
                            mult,
                            notes,
                            s["first_mcap"],
                            s["ath_mcap"],
                            s["mint"],
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
            n += 1
        self.memory.set_meta("mega_seeds_version", MEGA_SEEDS_VERSION)
        logger.info("Seeded %s examples (mega catalog %s)", n, MEGA_SEEDS_VERSION)
        return n
