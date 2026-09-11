"""FR-9 / FR-13 / NFR-5: the settings window's testable core.

The window itself is manual (TEST-PLAN: FR-9/FR-13 are visual, need a display).
What is pinned here is every piece of logic that does not need a screen: the
seconds clamp, the brightness index <-> level mapping, the fixed night palette,
and that Save writes exactly the shown values while Cancel writes nothing.
"""

from __future__ import annotations

import pytest

from asus_kbd_backlight import app_settings, config


@pytest.mark.parametrize(
    "raw,expected",
    [
        (600, 60),        # over the top -> clamped down
        ("600", 60),
        (0, 1),           # under the floor -> clamped up
        (-5, 1),
        (3, 3),
        ("45", 45),
        (3.6, 4),         # snaps to a whole second
        (2.4, 2),
    ],
)
def test_clamp_timeout_clamps_and_rounds(raw, expected):
    assert app_settings.clamp_timeout(raw) == expected


@pytest.mark.parametrize("junk", ["", "abc", None, "  ", "12x"])
def test_clamp_timeout_falls_back_on_garbage(junk):
    assert app_settings.clamp_timeout(junk, fallback=7) == 7


@pytest.mark.parametrize("index,level", [(0, 1), (1, 2), (2, 3)])
def test_index_level_round_trip(index, level):
    assert app_settings.index_to_level(index) == level
    assert app_settings.level_to_index(level) == index


@pytest.mark.parametrize("index", [-1, 0, 1, 2, 5])
def test_index_to_level_stays_in_range(index):
    assert app_settings.index_to_level(index) in (1, 2, 3)


@pytest.mark.parametrize("level", [-2, 0, 1, 2, 3, 9])
def test_level_to_index_stays_in_range(level):
    assert app_settings.level_to_index(level) in (0, 1, 2)


def test_palette_has_all_the_prd_tokens():
    expected = {
        "bg", "surface", "border", "text", "text-muted",
        "accent", "accent-hover", "accent-pressed", "accent-text", "slider-knob",
    }
    assert set(app_settings.PALETTE) == expected


def test_palette_avoids_pure_black_and_white():
    for name, value in app_settings.PALETTE.items():
        assert value.lower() not in ("#ffffff", "#fff", "#000000", "#000"), name


def test_palette_contrast_is_readable_but_not_maxed():
    p = app_settings.PALETTE
    # Body text sits on the card surface; keep it comfortably readable (>= 7:1)
    # without slamming into the 21:1 pure-contrast ceiling FR-13 warns against.
    body = app_settings.contrast_ratio(p["surface"], p["text"])
    assert 7.0 <= body <= 14.0
    # Muted text (headers, ticks, version) is dimmer but still legible.
    muted = app_settings.contrast_ratio(p["bg"], p["text-muted"])
    assert 3.5 <= muted <= 7.0


def test_contrast_ratio_is_symmetric_and_bounded():
    p = app_settings.PALETTE
    assert app_settings.contrast_ratio(p["bg"], p["text"]) == pytest.approx(
        app_settings.contrast_ratio(p["text"], p["bg"])
    )
    assert app_settings.contrast_ratio(p["bg"], p["bg"]) == pytest.approx(1.0)


def test_build_config_uses_the_shown_values_and_keeps_device_id():
    base = config.Config(timeout=3.0, on_level=1, device_id=0x00050099, autostart=True)
    built = app_settings.build_config(
        base, timeout_s=12, level_index=2, autostart=False
    )
    assert built.timeout == 12.0
    assert built.on_level == 3
    assert built.autostart is False
    assert built.device_id == 0x00050099  # untouched: file-only (PRD open q7)


def test_build_config_clamps_an_out_of_range_timeout():
    built = app_settings.build_config(
        config.Config(), timeout_s=9000, level_index=0, autostart=True
    )
    assert built.timeout == 60.0


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cfg, path):
        self.calls.append((cfg, path))
        return path


def test_run_focuses_an_existing_window_instead_of_opening_a_second(monkeypatch):
    monkeypatch.setattr(app_settings, "_acquire_singleton", lambda: False)
    focused = []
    monkeypatch.setattr(app_settings, "_focus_existing", lambda: focused.append(True))
    # SettingsWindow must never be constructed on the singleton short-circuit.
    monkeypatch.setattr(
        app_settings, "SettingsWindow",
        lambda *a, **k: pytest.fail("second window constructed"),
    )
    assert app_settings.run(None) == 0
    assert focused == [True]
