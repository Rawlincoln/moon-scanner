"""P0 audit pack: alerts auth, heat hard_avoid, graduated enrich_ok, moon soft flags."""

from __future__ import annotations

from services.alert_auth import admin_header_ok, cron_secret_ok, force_auth_ok
from services.graduated_runners import graduated_reject_reason
from services.moon_picks import _pillar_safety
from services.organic_heat import heat_reject_reason


def test_cron_query_never_accepts_admin_key(monkeypatch):
    """?key= must only match TELEGRAM_CRON_SECRET, not ADMIN_API_KEY."""
    import services.alert_auth as aa

    monkeypatch.setattr(aa, "ADMIN_API_KEY", "admin-secret-xyz")
    monkeypatch.setattr(aa, "TELEGRAM_CRON_SECRET", "cron-secret-abc")
    assert cron_secret_ok("admin-secret-xyz") is False
    assert cron_secret_ok("cron-secret-abc") is True
    assert admin_header_ok("admin-secret-xyz") is True
    assert admin_header_ok("cron-secret-abc") is False


def test_force_auth_fail_closed_when_bot_wired(monkeypatch):
    import services.alert_auth as aa

    monkeypatch.setattr(aa, "ADMIN_API_KEY", "")
    monkeypatch.setattr(aa, "TELEGRAM_CRON_SECRET", "")
    monkeypatch.setattr(aa, "IS_PRODUCTION", True)
    assert force_auth_ok(bot_wired=True) is False


def test_force_auth_allows_local_unconfigured(monkeypatch):
    import services.alert_auth as aa

    monkeypatch.setattr(aa, "ADMIN_API_KEY", "")
    monkeypatch.setattr(aa, "TELEGRAM_CRON_SECRET", "")
    monkeypatch.setattr(aa, "IS_PRODUCTION", False)
    assert force_auth_ok(bot_wired=False) is True


def test_force_auth_accepts_cron_query(monkeypatch):
    import services.alert_auth as aa

    monkeypatch.setattr(aa, "ADMIN_API_KEY", "admin-x")
    monkeypatch.setattr(aa, "TELEGRAM_CRON_SECRET", "cron-y")
    monkeypatch.setattr(aa, "IS_PRODUCTION", True)
    assert force_auth_ok(key="cron-y", allow_query_cron=True, bot_wired=True) is True
    assert force_auth_ok(key="admin-x", allow_query_cron=True, bot_wired=True) is False


def test_heat_blocks_bundled_hard_avoid():
    t = {
        "tokenAddress": "HeatBlockMint1111111111111111111111111",
        "mcap_usd": 8_000,
        "ath_mcap": 8_500,
        "age_minutes": 20,
        "enrich_ok": True,
        "avoid": {
            "hard_avoid": True,
            "flags": ["bundled", "snipers"],
            "summary": "bundled launch",
        },
        "safety": {"passed": True, "top_holders": [{"pct": 2}]},
        "pumpfun": {"reply_count": 20},
    }
    r = heat_reject_reason(t)
    assert r is not None


def test_graduated_missing_enrich_ok_blocks():
    t = {
        "tokenAddress": "GradEnrichMint111111111111111111111111",
        "mcap_usd": 2_000_000,
        "ath_mcap": 2_500_000,
        "age_minutes": 400,
        "complete": True,
        "enrich_ok": False,
        "enrich_errors": ["rugcheck_timeout"],
        "pumpfun": {"complete": True},
    }
    r = graduated_reject_reason(t)
    assert r and "safety unknown" in r.lower()


def test_graduated_safety_error_blocks():
    t = {
        "tokenAddress": "GradErrMint11111111111111111111111111",
        "mcap_usd": 2_000_000,
        "ath_mcap": 2_200_000,
        "age_minutes": 400,
        "complete": True,
        "enrich_ok": True,
        "safety": {"error": True, "passed": False},
        "pumpfun": {"complete": True},
    }
    r = graduated_reject_reason(t)
    assert r and ("safety" in r.lower() or "error" in r.lower())


def test_moon_soft_flags_match_emitters():
    t = {
        "avoid": {
            "flags": ["entry_trap_social", "dead_book", "wash_buys"],
            "avoid": True,
        },
        "pumpfun": {},
        "socialSignals": {"edge_score": 10, "namejack_risk": False},
    }
    score, notes = _pillar_safety(t)
    assert score < 65
    joined = " ".join(notes).lower()
    assert (
        "entry_trap_social" in joined
        or "dead_book" in joined
        or "wash" in joined
    )
