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
) -> str:
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
    if mult >= 3.0 and crash > 0.25:
        return "WINNER"
    if mult >= 1.5 and crash > 0.2:
        return "RUNNER"
    if crash <= 0.45 or (creator_dumped and crash <= 0.6):
        return "DUMP"
    if any(f in flags for f in ("drained_curve", "creator_dumped", "sell_pressure")):
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

        feats = extract_features(
            safety=safety,
            pair=pair_like,
            pump=pump,
            social=result.get("socialSignals"),
            smart_money=result.get("smartMoney"),
            alpha=result.get("alphaSetup"),
            avoid=safety.get("avoid"),
            mcap=mcap,
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
            mcap=mcap,
            price=price,
        )

        # Early finalize scams/avoid so model learns immediately
        avoid = safety.get("avoid") or {}
        if avoid.get("hard_avoid") or avoid.get("avoid"):
            self.memory.finalize_outcome(
                mint,
                "SCAM" if avoid.get("hard_avoid") else "DUMP",
                notes=avoid.get("summary") or "avoid_filter",
                features=feats,
            )

        return pred

    async def poll_active(self) -> int:
        """Refresh active tokens from pump.fun; detect dump/crash; finalize."""
        mints = self.memory.get_active_mints()
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

                    if crashed or drained or old or graduated:
                        outcome = classify_outcome(
                            first_mcap=first or mcap,
                            ath_mcap=ath,
                            last_mcap=mcap,
                            creator_dumped=bool(tok.get("creator_dump_ts")),
                        )
                        # Prefer SCAM if flash dump pattern on pump ath
                        ath_ts = _f(coin.get("ath_market_cap_timestamp"))
                        created = _f(coin.get("created_timestamp"))
                        if (
                            ath_hist >= 5000
                            and created
                            and ath_ts
                            and (ath_ts - created) / 60000 <= 5
                            and mcap < ath_hist * 0.55
                        ):
                            outcome = "SCAM"
                        notes = (
                            f"ath={ath:.0f} last={mcap:.0f} "
                            f"dump_mcap={tok.get('creator_dump_mcap')}"
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

    def seed_known_examples(self) -> int:
        """Seed model with documented winners/scams so learning starts non-empty."""
        seeds = [
            # winners / runners
            {
                "mint": "FUY6RbdfrDfa82y1AS5ZQRtaoSr1ZVTGD2EkN11bpump",
                "name": "The Addiction Bird",
                "symbol": "KIWI",
                "first_mcap": 5200,
                "ath_mcap": 62489,
                "outcome": "WINNER",
                "features": {
                    "has_viral": 1,
                    "own_twitter": 1,
                    "real_website": 1,
                    "mid_bags_ge_5": 1,
                    "holders_ge_100": 1,
                    "mcap_bin:sweet_3.5_7.5k": 1,
                    "alpha_bin:alpha_high": 1,
                    "curve_sol_ge_5": 1,
                    "buy_ratio_ge_1.3": 1,
                },
            },
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
        ]
        n = 0
        for s in seeds:
            existing = self.memory.get_token(s["mint"])
            if existing and existing.get("outcome"):
                continue
            # Build fake feature dict for keys
            feats = {
                "has_viral": 0,
                "own_twitter": 0,
                "real_website": 0,
                "fake_twitter": 0,
                "fake_website": 0,
                "adult_bait": 0,
                "creator_sold": 0,
                "mcap_bin": "sweet_3.5_7.5k",
                "alpha_bin": "alpha_low",
                "sniper_risk": "low",
                "holders": 10,
                "mid_bags": 0,
                "quote_sol": 0.2,
                "buy_ratio": 0.9,
                "sells_m5": 50,
            }
            # Map seed keys into feats for feature_keys_for_learning
            for k, v in s["features"].items():
                if ":" in k:
                    base, val = k.split(":", 1)
                    feats[base] = val
                elif k.endswith(("_ge_5", "_ge_3", "_ge_100", "_ge_40", "_ge_15", "_lt_15", "_lt_3", "_ge_1.3", "_drained")):
                    # synthetic binary handled by keys list directly via finalize
                    pass
                else:
                    feats[k] = v
            # Ensure binary flags
            for bk in (
                "has_viral",
                "own_twitter",
                "real_website",
                "fake_twitter",
                "fake_website",
                "adult_bait",
                "creator_sold",
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
            # set ath
            self.memory.upsert_token(
                s["mint"], name=s["name"], symbol=s["symbol"], mcap=s["ath_mcap"]
            )
            # Inject feature_stats for explicit keys in seed
            with self.memory._lock:
                conn = self.memory._conn()
                try:
                    mult = s["ath_mcap"] / s["first_mcap"]
                    for fk, val in s["features"].items():
                        if isinstance(val, int) and val == 0:
                            continue
                        key = fk if isinstance(val, int) or ":" in fk else fk
                        if isinstance(val, int) and val == 1:
                            key = fk
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
                    conn.execute(
                        """
                        UPDATE tokens SET outcome=?, outcome_ts=?, max_multiple=?,
                            active=0, notes='seed'
                        WHERE mint=?
                        """,
                        (s["outcome"], time.time(), mult, s["mint"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
            n += 1
        return n
