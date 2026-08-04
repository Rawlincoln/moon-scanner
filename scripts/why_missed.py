"""Diagnose why mints were missed by each feed."""

from __future__ import annotations

import asyncio
import sys

from services.graduated_runners import (
    graduated_card_from_coin,
    graduated_reject_reason,
    evaluate_graduated,
)
from services.moon_picks import reject_reason, evaluate
from services.organic_heat import (
    HEAT_MCAP_MAX,
    HEAT_MCAP_MIN,
    evaluate_heat,
    heat_card_from_coin,
    heat_reject_reason,
)
from services.pumpfun import PumpFunClient
from services.safe_snipes import (
    SNIPE_MCAP_MAX,
    SNIPE_MCAP_MIN,
    snipe_card_from_coin,
    snipe_reject_reason,
)
from services.scan_moon import enrich_moon_card, moon_card_from_coin


async def analyze(mint: str) -> None:
    p = PumpFunClient()
    coin = await p.get_coin(mint)
    if not coin:
        print(f"=== {mint[:16]}… NO DATA")
        return
    mcap = float(coin.get("usd_market_cap") or 0)
    ath = float(coin.get("ath_market_cap") or 0)
    age = PumpFunClient.coin_age_minutes(coin)
    bond = PumpFunClient.bonding_progress(coin)
    ret = (100 * mcap / ath) if ath > 0 and mcap > 0 else None
    print("=" * 64)
    print(f"{coin.get('symbol')} | {coin.get('name')}")
    print(f"mint {mint}")
    if ret is not None:
        print(f"mcap=${mcap:,.0f} ath=${ath:,.0f} ret={ret:.1f}% of ATH")
    else:
        print(f"mcap=${mcap:,.0f} ath=${ath:,.0f}")
    print(
        f"age={age:.1f}m ({age/60:.1f}h / {age/1440:.1f}d) "
        f"bond={bond:.1f}% complete={coin.get('complete')} "
        f"replies={coin.get('reply_count')}"
    )
    tw = (coin.get("twitter") or "")[:70]
    print(f"twitter={tw!r}")

    mc = moon_card_from_coin(coin)
    hc = heat_card_from_coin(coin)
    sc = snipe_card_from_coin(coin)
    gc = graduated_card_from_coin(coin)
    print(
        f"pre-cards: moon={bool(mc)} heat={bool(hc)} "
        f"snipe={bool(sc)} grad={bool(gc)}"
    )
    print(
        f"band check: heat[{HEAT_MCAP_MIN:,.0f}-{HEAT_MCAP_MAX:,.0f}]="
        f"{HEAT_MCAP_MIN <= mcap <= HEAT_MCAP_MAX} "
        f"snipe[{SNIPE_MCAP_MIN:,.0f}-{SNIPE_MCAP_MAX:,.0f}]="
        f"{SNIPE_MCAP_MIN <= mcap <= SNIPE_MCAP_MAX}"
    )

    base = mc or hc or sc
    if base:
        en = await enrich_moon_card(dict(base), skip_narrative_gate=True)
        holders = bool((en.get("safety") or {}).get("top_holders"))
        print(
            f"enrich_ok={en.get('enrich_ok')} holders={holders} "
            f"errs={en.get('enrich_errors')}"
        )
        en_m = await enrich_moon_card(dict(base), skip_narrative_gate=False)
        mr = reject_reason(en_m)
        print(f"MOON reject: {mr or '(none)'}")
        if mr is None:
            ev = evaluate(en_m)
            print(
                f"  moon -> {ev.get('label')} score={ev.get('moon_score')} "
                f"eligible={ev.get('eligible')}"
            )
        hr = heat_reject_reason(en)
        print(f"HEAT reject: {hr or '(none)'}")
        if hr is None:
            he = evaluate_heat(en)
            print(
                f"  heat -> {he.get('label')} score={he.get('heat_score')} "
                f"reject={he.get('reject')}"
            )
        sr = snipe_reject_reason(en)
        print(f"SNIPE reject: {sr or '(none)'}")
    else:
        print("No early-feed pre-card — outside moon/heat/snipe builders")

    if gc:
        en_g = await enrich_moon_card(dict(gc), skip_narrative_gate=True)
        gr = graduated_reject_reason(en_g)
        print(f"GRAD reject: {gr or '(none)'}")
        if gr is None:
            ge = evaluate_graduated(en_g)
            print(
                f"  grad -> {ge.get('label')} score={ge.get('grad_score')} "
                f"eligible={ge.get('eligible')}"
            )
            if ge.get("why"):
                print(f"  why: {ge.get('why')[:4]}")
    print()


async def main(mints: list[str]) -> None:
    for m in mints:
        try:
            await analyze(m)
        except Exception as exc:
            print(f"ERR {m[:16]} {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    mints = sys.argv[1:] or [
        "d3GXUEB3ewK8CYcTH5rbhQEZg4m7utiNjXfjUArpump",
        "CmzacHm3ob14huUYYhzaPcPcCgTVLDCBU6nSjxzvpump",
        "E14Zh2nA8GTwAXh5XSbz6XuVkEeDZDmx9ANibUvapump",
        "5VbMioVZem8cyWnst51DKJSo2xko6daYxqdcNkDQpump",  # Tiktok Coin candidate
    ]
    asyncio.run(main(mints))
