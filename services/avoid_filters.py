"""Hard avoid filters — scams, ghost launches, drained curves, pullable LP.

Learned from real rugs:
  BD42…pump  — ghost launch / mass-mint metadata
  4GTk… / FAAn… — drained curve, creator dump
  62pz…pump (Baby Corn) — flash P&D + Instagram "website"
  5ocg…pump (CEO of Sex) — user lost 0.2 SOL:
    * ATH ~$8k in 1.5m → −73% crash
    * status-link Twitter only, empty desc, 0 replies
    * adult-bait name, dust holders, creator dumped, ~0.16 SOL left
    * RugCheck still "clean" — must catch BEFORE entry, not after dump
  BTU7…pump (USWR) — "all green, no sellers":
    * polished pitch + own X + website — looks institutional
    * 99.9% curve, only dust holders (0.05% max), 27 holders
    * creator dumped, curve ~0.04 SOL, ATH crash
    * extreme one-way buys (2676 buys / 560 sells) then chart "green"
  9Sj7…pump (CUBEMAN) — wash + AI pitch, still "green":
    * long AI community copy, ZERO twitter/telegram/website
    * 3355 buys vs 64 sells (extreme one-way), creator balance 0
    * many mid bags look "distributed" but flow is fake
  Bw1g…pump (Cashoty / CASHOTY) — user-flagged junk:
    * ATH ~$25.6k in ~4m then −90% to ~$2.2k
    * status-link Twitter only, EMPTY description, 0 replies
    * polished website but no community — entry packaging
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# Explicit mint blocklist (user-flagged rugs / junk)
BLOCKED_MINTS: set[str] = {
    "BD42EGwRsQArB2SKwgdqPzjsBbme963ZrR9sioTopump",
    "4GTkEsYhegrJmbAiiUe9TrsQrTrqx7n1jDMSH5GGpump",
    "FAAnKpATxZuWWsCbxWZ5yaNn9CyCj4d9Wnqzhhdqpump",
    "62pzwoXyHi5Z1iEdD67RDPTT12spZ4ph8WsLU5y8pump",  # Baby Corn flash P&D
    "5ocgBRqLyQxZEvtAYcX1nXeVhAj1cuCHi2ZfSZKVpump",  # CEO of Sex — status-link P&D
    "BTU78ZNs11eDYsaUXysXnEPEJrCDYDobAkTfQQafpump",  # USWR — all green, no real sellers/holders
    "9Sj7Yi6oYCATrjC68or2Rqk3D6YkgKaqc9UepDogpump",  # CUBEMAN — AI pitch, no socials, wash buys, creator dumped
    "Bw1gX5ih2DJFtXggXnnGbWqqpBte1uvb9jurUSecpump",  # Cashoty — status X + empty desc, flash ATH then dump
}

# Adult-bait / shock names — almost always pure attention rugs (CEO of Sex, etc.)
ADULT_BAIT_KEYWORDS: tuple[str, ...] = (
    "sex", "sexy", "porn", "nude", "nudes", "onlyfans", "only fans",
    "xxx", "nsfw", "milf", "boob", "tits", "ass ", "ceo of sex",
    "penis", "vagina", "hentai", "slut", "whore",
)

# Metadata hosts used by mass-launch / bot farm tools
SUSPICIOUS_METADATA_HOSTS: set[str] = {
    "metadata.j7tracker.io",
    "j7tracker.io",
}

# Description / social spam markers for serial deploy tools
SPAM_DEPLOY_MARKERS: tuple[str, ...] = (
    "j7tracker",
    "deployed using",
    "deployed with",
    "pump.fun bot",
    "auto deploy",
)

# "Website" that is really just a media post (not a project site)
FAKE_PROJECT_SITE_HOSTS: tuple[str, ...] = (
    "instagram.com",
    "tiktok.com",
    "vm.tiktok.com",
    "youtube.com",
    "youtu.be",
    "x.com",
    "twitter.com",
    "t.me",
    "telegram.me",
    "urbandictionary.com",
    "pump.fun",
    "j7tracker.io",
)

# Pool / curve style bags
_POOL_PCT_MIN = 40.0
_MIN_MEANINGFUL_PCT = 0.15
_MAX_MEANINGFUL_PCT = 15.0

_MIN_CURVE_SOL = 0.5
_MIN_LP_LOCKED_PCT = 80.0


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return 0.0


def _metadata_host(uri: str | None) -> str:
    if not uri:
        return ""
    try:
        return (urlparse(uri).netloc or "").lower().strip().replace("www.", "")
    except Exception:
        return ""


def _is_status_twitter(url: str) -> bool:
    u = (url or "").lower()
    return "status/" in u or "/i/status" in u


def analyze_avoid_flags(
    safety: dict | None = None,
    pump: dict | None = None,
    mint: str | None = None,
    pair: dict | None = None,
) -> dict[str, Any]:
    """Return avoid=True when token matches known-junk / LP-pull / P&D patterns."""
    safety = safety or {}
    pump = pump or {}
    pair = pair or {}
    mint = (mint or safety.get("mint") or "").strip()

    flags: list[str] = []
    reasons: list[str] = []

    # --- 1. Explicit blocklist ---
    if mint and mint in BLOCKED_MINTS:
        flags.append("blocklist")
        reasons.append("Mint on avoid list (user-flagged scam / rug)")

    if pump.get("is_banned"):
        flags.append("banned")
        reasons.append("Banned on pump.fun")

    # --- 2. Metadata host (mass-mint tools) ---
    meta = safety.get("token_meta") or {}
    uri = (
        meta.get("uri")
        or safety.get("metadata_uri")
        or pump.get("metadata_uri")
        or pump.get("uri")
        or ""
    )
    host = _metadata_host(uri)
    if host and any(host == h or host.endswith("." + h) for h in SUSPICIOUS_METADATA_HOSTS):
        flags.append("suspicious_metadata")
        reasons.append(f"Mass-mint metadata host: {host}")

    desc = (pump.get("description") or "").strip()
    desc_l = desc.lower()
    if any(m in desc_l for m in SPAM_DEPLOY_MARKERS):
        flags.append("spam_deploy_tool")
        reasons.append("Description advertises mass-deploy tool (e.g. j7tracker)")

    # --- 2c. Fake social proof (Baby Corn pattern) ---
    twitter = str(pump.get("twitter") or "")
    website = str(pump.get("website") or "")
    if twitter and _is_status_twitter(twitter):
        flags.append("fake_twitter")
        reasons.append(
            "Twitter is a random status link — not a project account (social spoof)"
        )
    web_host = _metadata_host(website)
    if web_host and any(
        web_host == h or web_host.endswith("." + h) for h in FAKE_PROJECT_SITE_HOSTS
    ):
        flags.append("fake_website")
        reasons.append(
            f"Website is {web_host} media link — not a real project site"
        )

    # --- 3. Ghost / dead book ---
    top = safety.get("top_holders") or []
    meaningful: list[float] = []
    for h in top:
        pct = _safe_float(h.get("pct"))
        if pct >= _POOL_PCT_MIN:
            continue
        if h.get("insider"):
            continue
        if _MIN_MEANINGFUL_PCT <= pct <= _MAX_MEANINGFUL_PCT:
            meaningful.append(pct)

    replies = int(pump.get("reply_count") or 0)
    has_social = bool(twitter or pump.get("telegram") or website)
    # Real social = own account + non-media website; status/reel don't count
    has_real_social = bool(
        (twitter and not _is_status_twitter(twitter))
        or (
            website
            and web_host
            and not any(
                web_host == h or web_host.endswith("." + h)
                for h in FAKE_PROJECT_SITE_HOSTS
            )
        )
        or pump.get("telegram")
    )
    holders = int(safety.get("total_holders") or 0)
    on_curve = bool(
        safety.get("on_bonding_curve")
        or (pump and not pump.get("complete", True))
    )

    ghost_holders = len(meaningful) == 0 and holders < 80
    ghost_community = replies == 0 and not has_real_social and len(desc) < 8

    if on_curve and ghost_holders and ghost_community:
        flags.append("ghost_launch")
        reasons.append(
            f"Ghost launch — {len(meaningful)} real holders, "
            f"{replies} replies, no real socials/description"
        )

    if replies == 0 and len(meaningful) <= 1 and holders < 50 and on_curve:
        if "ghost_launch" not in flags:
            flags.append("dead_book")
            reasons.append(
                f"Dead book — {len(meaningful)} meaningful holders, 0 replies"
            )

    # Spoofed socials + no community = hard scam packaging
    if (
        ("fake_twitter" in flags or "fake_website" in flags)
        and replies == 0
        and len(meaningful) <= 2
    ):
        flags.append("social_spoof_scam")
        reasons.append(
            "Fake socials + no community — classic scam packaging"
        )

    # EARLY ENTRY TRAP (catch BEFORE dump — 5ocg / CEO of Sex / Cashoty):
    # status-link Twitter + empty description = do not enter
    # (website alone does NOT save it — Cashoty had cashothy.fun then −90%)
    if "fake_twitter" in flags and len(desc) < 8:
        flags.append("entry_trap_social")
        reasons.append(
            "Entry trap: status-link Twitter + empty description "
            "— skip before the dump (site alone is not enough)"
        )
    elif "fake_twitter" in flags and replies == 0 and len(desc) < 20:
        flags.append("entry_trap_social")
        reasons.append(
            "Entry trap: status tweet only + zero replies "
            "— packaging, not community"
        )

    # Adult-bait / shock ticker or name
    name_blob = " ".join(
        [
            str(pump.get("name") or ""),
            str(pump.get("symbol") or ""),
            str(safety.get("token_name") or ""),
            str(safety.get("token_symbol") or ""),
        ]
    ).lower()
    if any(k in name_blob for k in ADULT_BAIT_KEYWORDS):
        flags.append("adult_bait")
        reasons.append(
            "Adult/shock name bait — almost always a pure attention rug"
        )

    # Parabolic mcap with zero organic community (pre-dump phase)
    cur_mcap = _safe_float(pump.get("usd_market_cap"))
    age_min = None
    created = _safe_float(pump.get("created_timestamp"))
    if created:
        import time

        age_min = (time.time() * 1000 - created) / 60_000
    if (
        cur_mcap >= 4_000
        and age_min is not None
        and age_min < 4
        and replies == 0
        and not has_real_social
        and len(meaningful) <= 2
    ):
        flags.append("parabolic_no_community")
        reasons.append(
            f"Parabolic ${cur_mcap:,.0f} in {age_min:.1f}m with no community "
            "— high flash-scam risk (do not FOMO)"
        )

    # --- 4. Creator serial spammer ---
    creator_tokens = int(safety.get("creator_token_count") or 0)
    if creator_tokens >= 8:
        flags.append("serial_creator")
        reasons.append(f"Creator has launched {creator_tokens} tokens")

    # --- 5. Liquidity pull / drain ---
    quote_sol = _safe_float(safety.get("lp_quote_sol"))
    quote_usd = _safe_float(safety.get("lp_quote_usd"))
    lp_locked_pct = safety.get("lp_locked_pct")
    lp_unlocked = _safe_float(safety.get("lp_unlocked"))
    market_type = str(safety.get("market_type") or "").lower()
    creator_sold = bool(safety.get("creator_sold"))
    creator_bal = _safe_float(safety.get("creator_balance"))
    creator_pct = _safe_float(safety.get("creator_pct"))

    # pump.fun real_sol_reserves is in lamports
    real_sol = _safe_float(pump.get("real_sol_reserves")) / 1e9
    if real_sol > 0 and (quote_sol <= 0 or real_sol < quote_sol):
        # Prefer real reserves when available
        if quote_sol <= 0:
            quote_sol = real_sol

    if on_curve or market_type in ("pump_fun", "pumpfun", "pump"):
        if 0 < quote_sol < _MIN_CURVE_SOL:
            flags.append("drained_curve")
            reasons.append(
                f"Bonding curve drained — only {quote_sol:.3f} SOL left "
                f"(need ≥ {_MIN_CURVE_SOL} SOL for safe exit)"
            )
        elif quote_sol == 0 and quote_usd == 0 and holders > 0:
            base_usd = _safe_float(safety.get("liquidity_usd"))
            if base_usd > 0 or _safe_float(safety.get("lp_locked_usd")) > 0:
                flags.append("drained_curve")
                reasons.append(
                    "No SOL side liquidity on curve (quote=$0) — exit risk"
                )
        if 0 < real_sol < _MIN_CURVE_SOL and "drained_curve" not in flags:
            flags.append("drained_curve")
            reasons.append(
                f"Only {real_sol:.3f} real SOL on pump curve — liquidity gone"
            )

    if not on_curve:
        if lp_unlocked > 0:
            flags.append("lp_unlocked")
            reasons.append(
                f"LP unlocked ({lp_unlocked:g}) — dev can pull liquidity"
            )
        locked = _safe_float(lp_locked_pct, default=-1)
        if locked >= 0 and locked < _MIN_LP_LOCKED_PCT:
            flags.append("lp_not_locked")
            reasons.append(
                f"LP only {locked:.0f}% locked — want ≥ {_MIN_LP_LOCKED_PCT:.0f}%"
            )

    if safety.get("freeze_authority"):
        flags.append("freeze_authority")
        reasons.append("Freeze authority active — wallets can be frozen")

    if on_curve and creator_sold and creator_bal == 0 and creator_pct < 0.05:
        if len(meaningful) <= 2 or replies == 0:
            flags.append("creator_dumped")
            reasons.append(
                "Creator sold bag on thin curve — classic dump-then-drain setup"
            )

    # --- 5e. Flash pump-and-dump (Baby Corn: ATH in <5m then crash) ---
    ath = _safe_float(pump.get("ath_market_cap"))
    cur = _safe_float(pump.get("usd_market_cap"))
    ath_ts = _safe_float(pump.get("ath_market_cap_timestamp"))
    created = _safe_float(pump.get("created_timestamp"))
    if ath >= 5_000 and cur > 0 and ath > cur:
        dump_frac = 1.0 - (cur / ath)
        mins_to_ath = (ath_ts - created) / 60_000 if ath_ts and created else 999.0
        if mins_to_ath <= 5 and dump_frac >= 0.45:
            flags.append("flash_pump_dump")
            reasons.append(
                f"Flash pump-dump: ATH ${ath:,.0f} in {mins_to_ath:.1f}m, "
                f"now ${cur:,.0f} (−{dump_frac*100:.0f}%)"
            )
        elif dump_frac >= 0.55 and (mins_to_ath <= 60 or len(meaningful) <= 1):
            # USWR-style: −55%+ from ATH with empty book still counts
            flags.append("post_ath_crash")
            reasons.append(
                f"Crashed {dump_frac*100:.0f}% from ATH ${ath:,.0f} "
                f"— exit already happened"
            )

    # --- 5f. Buy/sell flow traps (honeypot / wash / "all green no sellers") ---
    all_tx = pair.get("txns") or {}
    windows = []
    for win in ("m5", "h1", "h6", "h24"):
        t = all_tx.get(win) or {}
        b, s = int(t.get("buys") or 0), int(t.get("sells") or 0)
        if b or s:
            windows.append((win, b, s))

    for win, buys, sells in windows:
        # Zero sellers with meaningful buy count = cannot exit / wash only
        if buys >= 12 and sells == 0:
            flags.append("zero_sellers")
            reasons.append(
                f"All green / no sellers ({buys} buys, 0 sells in {win}) "
                "— honeypot or fake volume, do not enter"
            )
            break
        if buys >= 8 and sells == 0 and win in ("m5", "h1"):
            flags.append("zero_sellers")
            reasons.append(
                f"No sellers in {win} ({buys} buys) — cannot verify exit"
            )
            break

    # Extreme one-way buys (wash trading paints green chart)
    for win, buys, sells in windows:
        ratio = buys / max(sells, 1)
        if buys >= 40 and ratio >= 4.0:
            flags.append("wash_buys")
            reasons.append(
                f"One-way buys: {buys} buys vs {sells} sells ({win}) "
                "— wash volume / no real market"
            )
            break
        # CUBEMAN-class: hundreds of buys, almost no sells
        if buys >= 200 and ratio >= 8.0:
            flags.append("extreme_wash")
            reasons.append(
                f"Extreme wash: {buys} buys / {sells} sells ({win}, {ratio:.0f}x) "
                "— chart is fake green"
            )
            break
        if buys >= 500 and sells <= max(20, buys * 0.05):
            flags.append("extreme_wash")
            reasons.append(
                f"Bot buy flood: {buys} buys vs {sells} sells ({win}) "
                "— skip even if holders look distributed"
            )
            break

    # Active dump
    m5 = all_tx.get("m5") or {}
    buys_m5 = int(m5.get("buys") or 0)
    sells_m5 = int(m5.get("sells") or 0)
    if sells_m5 >= 25 and sells_m5 > buys_m5 * 1.05:
        flags.append("sell_pressure")
        reasons.append(
            f"Selling dominates: {sells_m5} sells vs {buys_m5} buys (5m) — dump in progress"
        )

    # Creator already out while chart still green (CUBEMAN)
    if (
        on_curve
        and creator_sold
        and creator_bal == 0
        and creator_pct < 0.05
        and (
            sells_m5 < buys_m5 * 0.25
            or any(b >= 50 and b / max(s, 1) >= 3 for _, b, s in windows)
        )
    ):
        flags.append("dev_out_green_chart")
        reasons.append(
            "Dev already sold while buys still dominate — exit liquidity is a trap"
        )

    # --- 5g. Polished narrative + empty holder book (USWR) ---
    max_non_pool = max(meaningful) if meaningful else 0.0
    for h in top:
        pct = _safe_float(h.get("pct"))
        if 0 < pct < _POOL_PCT_MIN and not h.get("insider"):
            max_non_pool = max(max_non_pool, pct)
    if (
        on_curve
        and holders < 80
        and len(meaningful) == 0
        and max_non_pool < 0.5
        and (has_real_social or len(desc) >= 60)
    ):
        flags.append("empty_distribution")
        reasons.append(
            f"Looks marketed green but empty book — "
            f"{holders} holders, max bag {max_non_pool:.2f}% outside curve "
            "(wash / no real free float)"
        )

    # --- 5h. AI pitch / long copy with ZERO socials (CUBEMAN) ---
    # Generic "community vibes" essay + no X/TG/web = marketing shell
    if (
        len(desc) >= 120
        and not twitter
        and not website
        and not pump.get("telegram")
        and replies == 0
    ):
        flags.append("ai_pitch_no_socials")
        reasons.append(
            "Long pitch + zero socials (no X/TG/web) — AI/marketing shell, not organic"
        )

    # Mid-bag cluster + wash + no socials = bot farm book that looks "distributed"
    if (
        len(meaningful) >= 5
        and not has_real_social
        and replies == 0
        and any(
            b >= 100 and b / max(s, 1) >= 5
            for _, b, s in windows
        )
    ):
        flags.append("bot_holder_cluster")
        reasons.append(
            f"{len(meaningful)} mid bags + wash buys + no socials "
            "— bot farm distribution, not a real community"
        )

    # --- 6. Insider / honeypot / rugged ---
    if safety.get("rugged"):
        flags.append("rugged")
        reasons.append("Flagged rugged on RugCheck")
    if safety.get("is_honeypot"):
        flags.append("honeypot")
        reasons.append("Honeypot detected")
    if safety.get("insider_detected") or int(safety.get("insider_networks") or 0) > 0:
        flags.append("insiders")
        reasons.append("Insider wallet graph detected")
    if safety.get("mint_authority"):
        flags.append("mint_authority")
        reasons.append("Mint authority still active — supply can be inflated")

    for risk in safety.get("risks") or []:
        name = (risk.get("name") or "").lower()
        level = risk.get("level") or ""
        if level not in ("danger", "critical", "warn"):
            continue
        if any(
            k in name
            for k in ("lp unlocked", "low liquidity", "copycat", "rug", "mutable")
        ):
            flag = "rugcheck_" + name.replace(" ", "_")[:24]
            if flag not in flags:
                flags.append(flag)
                reasons.append(f"RugCheck {level}: {risk.get('name')}")

    # Hard = invest-blocking (still shown in UI unless fatal for trenches hide)
    hard_set = {
        "blocklist",
        "banned",
        "rugged",
        "honeypot",
        "ghost_launch",
        "serial_creator",
        "drained_curve",
        "lp_unlocked",
        "lp_not_locked",
        "freeze_authority",
        "mint_authority",
        "spam_deploy_tool",
        "flash_pump_dump",
        "post_ath_crash",
        "adult_bait",
        "extreme_wash",
        "ai_pitch_no_socials",
        "dev_out_green_chart",
    }
    # Soft packaging — warn but don't treat as automatic hard hide
    soft_hardish = {
        "social_spoof_scam",
        "entry_trap_social",
        "parabolic_no_community",
        "zero_sellers",
        "wash_buys",
        "empty_distribution",
        "bot_holder_cluster",
    }
    hard = bool(hard_set & set(flags))
    # Status+empty desc is hard for invest, but only if also no real website
    # (Cashoty-class is on blocklist; generic status tweets with real sites = soft)
    if "entry_trap_social" in flags and "fake_website" in flags:
        hard = True
    elif "entry_trap_social" in flags and not has_real_social:
        hard = True

    soft_combo = {
        "dead_book",
        "suspicious_metadata",
        "insiders",
        "creator_dumped",
        "fake_twitter",
        "fake_website",
        "sell_pressure",
    } | soft_hardish
    soft = bool(flags) and not hard

    avoid = hard or (soft and len(soft_combo & set(flags)) >= 2)

    return {
        "avoid": avoid,
        "hard_avoid": hard,
        "flags": flags,
        "reasons": reasons[:12],
        "meaningful_holders": len(meaningful),
        "summary": reasons[0] if reasons else "No hard avoid flags",
        "mint": mint,
        "metadata_host": host,
        "lp_quote_sol": quote_sol,
        "lp_locked_pct": lp_locked_pct,
        "has_real_social": has_real_social,
    }
