"""Flash holders — too many wallets too young = concealed snipers/bundle."""

from services.avoid_filters import (
    HARD_AVOID_FLAGS,
    analyze_avoid_flags,
    flash_holders_reason,
)


def test_bd1y_class_two_min_245_holders():
    why = flash_holders_reason(245, 2.0)
    assert why is not None
    assert "245" in why
    assert "sniper" in why.lower() or "bundle" in why.lower() or "organic" in why.lower()


def test_organic_slow_ok():
    # 30 holders over 20 min is fine
    assert flash_holders_reason(30, 20.0) is None
    # 40 holders over 10 min ok
    assert flash_holders_reason(40, 10.0) is None


def test_velocity_flag():
    # 90 holders in 2 min
    assert flash_holders_reason(90, 2.0) is not None


def test_avoid_hard_flag():
    assert "flash_holders" in HARD_AVOID_FLAGS
    avoid = analyze_avoid_flags(
        safety={
            "total_holders": 245,
            "on_bonding_curve": True,
            "top_holders": [{"pct": 2.0}, {"pct": 1.5}],
            "passed": True,
        },
        pump={"age_minutes": 2.0, "reply_count": 5, "description": "test coin"},
        mint="Bd1YfJAtDYqPiZZv47M9zWPYF9dyzt2Zc6rtDR7epump",
    )
    assert "flash_holders" in (avoid.get("flags") or [])
    assert avoid.get("hard_avoid") or avoid.get("hard")


def test_bundle_sniper_flash_holders():
    from services.bundle_sniper import analyze_bundle_and_snipers

    bs = analyze_bundle_and_snipers(
        {
            "total_holders": 245,
            "top_holders": [{"pct": 3.0, "owner": "A"}, {"pct": 2.0, "owner": "B"}],
        },
        {"age_minutes": 2.0},
        {},
        age_minutes=2.0,
    )
    assert "flash_holders" in (bs.get("patterns") or []) or any(
        "Flash holders" in f or "holders @" in f
        for f in (bs.get("bundle") or {}).get("flags") or []
    ) or (bs.get("snipers") or {}).get("score", 0) >= 30
