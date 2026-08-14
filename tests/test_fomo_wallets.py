"""FOMO managed wallet list CRUD."""

from __future__ import annotations

from services.fomo_wallets import (
    add_wallet,
    list_wallets,
    remove_wallet,
    valid_address,
    clear_cache,
)


def test_valid_address():
    assert valid_address("2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f")
    assert not valid_address("not-a-wallet")
    assert not valid_address("So11111111111111111111111111111111111111112")


def test_add_and_remove(tmp_path, monkeypatch):
    import services.fomo_wallets as fw

    monkeypatch.setattr(fw, "_PATH", tmp_path / "fomo_wallets.json")
    clear_cache()
    # empty start — may seed from elite; force empty save
    fw._save([])
    clear_cache()

    addr = "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f"
    row = add_wallet(addr, label="Cupsey", tier="S")
    assert row["label"] == "Cupsey"
    assert row["address"] == addr
    wallets = list_wallets(force=True)
    assert any(w["address"] == addr for w in wallets)

    assert remove_wallet(addr) is True
    wallets2 = list_wallets(force=True)
    assert not any(w["address"] == addr for w in wallets2)
    assert remove_wallet(addr) is False


def test_open_manage_allows_without_key():
    from config import FOMO_OPEN_MANAGE

    # Default is open manage so the FOMO UI works on Render without pasting keys
    assert FOMO_OPEN_MANAGE is True


def test_fomo_wallets_uses_managed(tmp_path, monkeypatch):
    import services.fomo_wallets as fw
    import services.fomo_watch as watch

    monkeypatch.setattr(fw, "_PATH", tmp_path / "fomo_wallets.json")
    fw.clear_cache()
    fw._save([])
    fw.clear_cache()
    add_wallet(
        "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
        label="Cented",
        tier="S",
    )
    fw.clear_cache()
    wallets = watch.fomo_wallets()
    assert any(w.get("label") == "Cented" for w in wallets)
