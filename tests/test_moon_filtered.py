"""Moon filtered list 6h persistence."""

from __future__ import annotations

from services import moon_filtered as mf


def test_remember_and_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "_PATH", tmp_path / "moon_filtered.json")
    mf.clear_cache()

    mint = "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f"
    rows = mf.remember_filtered(
        [
            {
                "tokenAddress": mint,
                "symbol": "FILT",
                "mcap_usd": 9000,
                "age_minutes": 12,
                "reject": "below score gate",
                "reject_key": "score",
            }
        ]
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "FILT"
    assert "padre.gg" in rows[0]["padre_url"]
    assert mint in rows[0]["padre_url"]

    # Re-merge keeps one mint, refreshes
    rows2 = mf.remember_filtered(
        [{"tokenAddress": mint, "symbol": "FILT", "reject": "still filtered"}]
    )
    assert len(rows2) == 1
    assert rows2[0]["reject"] == "still filtered"
    listed = mf.list_filtered()
    assert listed[0]["tokenAddress"] == mint
