"""Asset lookup, frozen and from a source checkout (FR-10)."""

import sys

from asus_kbd_backlight import paths


def test_asset_dir_points_at_the_repo_assets_when_not_frozen():
    assert paths.asset_dir() == paths.Path(__file__).resolve().parents[1] / "assets"


def test_asset_dir_follows_meipass_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.asset_dir() == tmp_path / "assets"


def test_icons_are_present_and_are_real_ico_files():
    for name in ("icon.ico", "icon-paused.ico"):
        found = paths.asset(name)
        assert found is not None, f"{name} missing - run python scripts/make-icon.py"
        head = found.read_bytes()[:6]
        # ICONDIR: reserved 0, type 1 (icon), then the image count.
        assert head[:4] == b"\x00\x00\x01\x00"
        assert int.from_bytes(head[4:6], "little") >= 5  # 16/24/32/48/256


def test_missing_asset_is_none_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.asset("icon.ico") is None
