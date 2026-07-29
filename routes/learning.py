"""Learning system API."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.deps import learning, learning_memory
from app.paths import BASE_DIR
from services.analyze_token import analyze_token

router = APIRouter(tags=["learning"])


@router.get("/api/learning/stats")
async def learning_stats():
    """How many tokens learned + outcome breakdown."""
    from services.learning.mega_seeds import MEGA_SEEDS, MEGA_SEEDS_VERSION

    summary = learning_memory.get_outcomes_summary()
    recent = learning_memory.recent_finalized(20)
    mega_recent = [
        r
        for r in learning_memory.recent_finalized(80)
        if r.get("outcome") in ("MEGA", "SUPER")
        or float(r.get("ath_mcap") or 0) >= 1_000_000
    ][:15]
    return {
        "ok": True,
        "summary": summary,
        "recent": recent,
        "base_rates": learning_memory.outcome_base_rates(),
        "model": "likelihood_ratio_v2",
        "model_version": learning_memory.get_meta("learn_model_version"),
        "mega_seeds": {
            "version": MEGA_SEEDS_VERSION,
            "applied": learning_memory.get_meta("mega_seeds_version"),
            "catalog_size": len(MEGA_SEEDS),
            "in_db": mega_recent,
        },
        "db": str(BASE_DIR / "data" / "learning.db"),
    }


@router.post("/api/learning/reseed")
async def learning_reseed(force: bool = Query(False)):
    """Re-apply historical mega + scam seeds into the learning DB."""
    n = learning.seed_known_examples(force=force)
    return {
        "ok": True,
        "seeded": n,
        "summary": learning_memory.get_outcomes_summary(),
        "version": learning_memory.get_meta("mega_seeds_version"),
    }


@router.post("/api/learning/rebuild")
async def learning_rebuild():
    """Recompute feature→outcome table from all finalized tokens (accuracy refresh)."""
    rebuilt = learning_memory.rebuild_feature_stats()
    learning_memory.set_meta("learn_model_version", "learn_lr_v2_2026_07")
    return {
        "ok": True,
        "rebuilt": rebuilt,
        "summary": learning_memory.get_outcomes_summary(),
        "base_rates": learning_memory.outcome_base_rates(),
        "model": "likelihood_ratio_v2",
    }


@router.get("/api/learning/predict/{mint}")
async def learning_predict(mint: str):
    """Full analysis + learned trade plan for one mint."""
    result = await analyze_token("solana", mint.strip())
    return {
        "mint": mint,
        "tradePlan": result.get("tradePlan"),
        "alphaSetup": result.get("alphaSetup"),
        "investSignal": result.get("investSignal"),
        "mcap_usd": result.get("mcap_usd"),
        "safety": {
            "passed": (result.get("safety") or {}).get("passed"),
            "avoid": (result.get("safety") or {}).get("avoid"),
        },
        "history": learning_memory.get_token(mint.strip()),
    }
