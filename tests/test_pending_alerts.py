"""Pending alerts Take / Skip."""

from __future__ import annotations

from services import pending_alerts as pa


def test_add_take_skip_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "_PATH", tmp_path / "pending.json")
    pa.clear_cache()

    row = pa.add_pending(
        feed="moon",
        token={
            "tokenAddress": "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f",
            "symbol": "TEST",
            "mcap_usd": 12000,
            "moon_label": "MOON",
        },
        plan={"entry_mcap": 12000, "sizing": {"size_usd": 50, "risk_usd": 5}},
        label="MOON",
    )
    assert row.get("id")
    assert row["mint"].startswith("2fg5")
    listed = pa.list_pending()
    assert len(listed) == 1
    assert listed[0]["symbol"] == "TEST"

    assert pa.remove_pending(row["id"]) is True
    assert pa.list_pending() == []
    assert pa.remove_pending(row["id"]) is False
