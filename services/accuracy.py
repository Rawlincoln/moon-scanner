"""Shared accuracy helpers: holder book truth, learning soft-rank, ATH merge."""

from __future__ import annotations

from typing import Any

from services.runner_radar import extract_ath_mcap, extract_mcap_usd


def holders_known(token: dict[str, Any] | None) -> bool:
    """True only when RugCheck (or equivalent) returned a non-empty holder list."""
    if not isinstance(token, dict):
        return False
    bs = token.get("bundleSniper") or token.get("bundle_sniper") or {}
    if isinstance(bs, dict) and bs.get("holders_known") is True:
        return True
    safety = token.get("safety") or token.get("safetyReport") or {}
    top = safety.get("top_holders") if isinstance(safety, dict) else None
    if isinstance(top, list) and len(top) > 0:
        return True
    return False


def merge_ath_into_token(token: dict[str, Any]) -> float:
    """Multi-source ATH: max(pump ath, peaks, live mcap). Mutates token ath fields."""
    if not isinstance(token, dict):
        return 0.0
    candidates: list[float] = []
    for v in (
        token.get("ath_mcap"),
        token.get("ath_market_cap"),
        token.get("_peak_mcap"),
        token.get("peak_mcap"),
    ):
        try:
            f = float(v or 0)
            if f > 0:
                candidates.append(f)
        except (TypeError, ValueError):
            pass
    pf = token.get("pumpfun") or {}
    if isinstance(pf, dict):
        for k in ("ath_market_cap", "ath_mcap"):
            try:
                f = float(pf.get(k) or 0)
                if f > 0:
                    candidates.append(f)
            except (TypeError, ValueError):
                pass
    mkt = token.get("market") or {}
    if isinstance(mkt, dict):
        for k in ("ath_market_cap", "ath_mcap", "marketCap", "fdv"):
            try:
                f = float(mkt.get(k) or 0)
                if f > 0:
                    candidates.append(f)
            except (TypeError, ValueError):
                pass
    mcap = extract_mcap_usd(token)
    if mcap > 0:
        candidates.append(mcap)
    # Also trust extract_ath for any other path
    try:
        base = float(extract_ath_mcap(token) or 0)
        if base > 0:
            candidates.append(base)
    except Exception:
        pass
    ath = max(candidates) if candidates else 0.0
    if ath > 0:
        token["ath_mcap"] = ath
        if isinstance(pf, dict):
            pf = dict(pf)
            pf["ath_market_cap"] = max(float(pf.get("ath_market_cap") or 0), ath)
            token["pumpfun"] = pf
    return ath


def _predict_from_token(token: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort learning predict; returns None if engine/memory unavailable."""
    try:
        from services.scan_moon import get_learning
        from services.learning.predictor import predict_trade

        eng = get_learning()
        if eng is None:
            return None
        mem = getattr(eng, "memory", None)
        if mem is None:
            return None
        safety = token.get("safety") or {}
        avoid = (
            token.get("avoid")
            or (token.get("safetyReport") or {}).get("avoid")
            or safety.get("avoid")
            or {}
        )
        return predict_trade(
            mem,
            safety=safety,
            pair=token.get("market") or {},
            pump=token.get("pumpfun") or {},
            social=token.get("socialSignals") or {},
            avoid=avoid,
            mcap=float(token.get("mcap_usd") or 0),
        )
    except Exception:
        return None


def learning_soft_adjust(
    token: dict[str, Any],
    score: int,
    conf: int,
    *,
    min_sample: int = 20,
) -> tuple[int, int, dict[str, Any]]:
    """Blend learned P(good) into score/conf without sole-gating.

    - High P(bad) → soft demote (up to −12 score / −10 conf)
    - High P(good) → soft boost (up to +6 / +5)
    - Sparse model sample → no change
    """
    meta: dict[str, Any] = {"applied": False}
    pred = _predict_from_token(token)
    if not pred:
        return int(score), int(conf), meta

    p_good = pred.get("p_good")
    if p_good is None:
        # older shape
        p_good = pred.get("prob_good")
    try:
        p_good = float(p_good)
    except (TypeError, ValueError):
        return int(score), int(conf), meta

    sample_n = int(pred.get("sample_n") or pred.get("n_features") or 0)
    # sample_n in predictor is feature-match mass; use action confidence as proxy
    if sample_n < min_sample and pred.get("action") is None:
        return int(score), int(conf), meta

    p_bad = 1.0 - p_good
    score_i = int(score)
    conf_i = int(conf)
    delta_s = 0
    delta_c = 0

    if p_bad >= 0.62:
        delta_s = -12
        delta_c = -10
    elif p_bad >= 0.52:
        delta_s = -7
        delta_c = -6
    elif p_good >= 0.55:
        delta_s = 6
        delta_c = 5
    elif p_good >= 0.45:
        delta_s = 3
        delta_c = 2

    # Hard demote when model says SKIP with high confidence
    action = str(pred.get("action") or "").upper()
    pred_conf = int(pred.get("confidence") or 0)
    if action == "SKIP" and pred_conf >= 70:
        delta_s = min(delta_s, -10)
        delta_c = min(delta_c, -8)

    score_i = max(0, min(99, score_i + delta_s))
    conf_i = max(0, min(99, conf_i + delta_c))
    meta = {
        "applied": delta_s != 0 or delta_c != 0,
        "p_good": round(p_good, 3),
        "p_bad": round(p_bad, 3),
        "action": action or None,
        "delta_score": delta_s,
        "delta_conf": delta_c,
        "sample_n": sample_n,
    }
    token["learning_soft"] = meta
    return score_i, conf_i, meta
