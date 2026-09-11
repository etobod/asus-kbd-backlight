"""FR-9 / FR-13 / NFR-5: the settings window.

The pure helpers (seconds clamp, brightness index <-> level mapping, the fixed
night palette, letter spacing, building the saved Config) are tested without a
screen. The widget tests below build the real customtkinter window - withdrawn,
with an injected ``save`` - and skip if Tk cannot start; what it *looks* like
is still the user's call (TEST-PLAN: FR-9/FR-13 are partly manual).
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
        (2.5, 3),         # half-up, not round-half-to-even
    ],
)
def test_clamp_timeout_clamps_and_rounds(raw, expected):
    assert app_settings.clamp_timeout(raw) == expected


@pytest.mark.parametrize("junk", ["", "abc", None, "  ", "12x", "inf", "-inf", "1e999", "nan"])
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


@pytest.mark.parametrize("hand_edited", [300.0, 2.5, 0.25])
def test_build_config_keeps_an_untouched_timeout_the_slider_cannot_show(hand_edited):
    # Settings opened only to flip autostart must not rewrite 300 s as 60 s.
    base = config.Config(timeout=hand_edited)
    shown = app_settings.clamp_timeout(hand_edited)
    built = app_settings.build_config(
        base, timeout_s=shown, level_index=0, autostart=False, timeout_touched=False
    )
    assert built.timeout == hand_edited


@pytest.mark.parametrize("hand_edited,picked", [(300.0, 60), (0.25, 1), (300.0, 10)])
def test_build_config_saves_a_touched_timeout_even_at_the_edge(hand_edited, picked):
    # A file's 300 shows as 60; a user who deliberately picks 60 must get 60.
    built = app_settings.build_config(
        config.Config(timeout=hand_edited), timeout_s=picked, level_index=0,
        autostart=True, timeout_touched=True,
    )
    assert built.timeout == float(picked)


@pytest.mark.parametrize(
    "work,size,expected",
    [
        ((0, 0, 1920, 1040), (400, 500), (760, 270)),       # primary, taskbar below
        ((-1920, 0, 0, 1080), (400, 500), (-1160, 290)),    # monitor left of primary
        ((0, 0, 300, 300), (400, 500), (0, 0)),             # bigger than the area
    ],
)
def test_centered_in_keeps_the_window_inside_the_work_area(work, size, expected):
    assert app_settings.centered_in(work, size) == expected


@pytest.mark.parametrize(
    "hex_color,expected",
    [("#141824", 0x00241814), ("#2A3350", 0x0050332A), ("#FF0000", 0x000000FF)],
)
def test_colorref_is_bgr(hex_color, expected):
    assert app_settings.colorref(hex_color) == expected


def test_style_native_frame_sets_dark_mode_and_palette_colours():
    calls = []
    app_settings.style_native_frame(0x1234, set_attribute=lambda a, v: calls.append((a, v)) or 0)
    p = app_settings.PALETTE
    assert calls == [
        (20, 1),                                      # DWMWA_USE_IMMERSIVE_DARK_MODE
        (35, app_settings.colorref(p["bg"])),         # caption
        (34, app_settings.colorref(p["border"])),     # border
        (36, app_settings.colorref(p["text-muted"])), # caption text
        (33, 2),                                      # rounded corners
    ]


def test_style_native_frame_falls_back_to_the_pre_20h1_dark_mode_id():
    calls = []

    def setter(attribute, value):
        calls.append(attribute)
        return 1 if attribute == 20 else 0  # nonzero HRESULT: id 20 unknown

    app_settings.style_native_frame(0x1234, set_attribute=setter)
    assert calls[:2] == [20, 19]


def test_dialog_style_drops_only_the_minimize_and_maximize_boxes():
    WS_OVERLAPPEDWINDOW = 0x00CF0000  # caption|sysmenu|thickframe|minimize|maximize
    assert app_settings.dialog_style(WS_OVERLAPPEDWINDOW) == 0x00CC0000


def test_style_native_frame_survives_every_call_failing():
    def setter(attribute, value):
        raise OSError("DwmSetWindowAttribute unavailable")

    app_settings.style_native_frame(0x1234, set_attribute=setter)  # must not raise


def test_run_shows_a_window_that_fails_to_open(monkeypatch, tmp_path):
    # --settings has no log file and the windowed exe no console.
    monkeypatch.setattr(app_settings, "_acquire_singleton", lambda: True)
    shown = []
    monkeypatch.setattr(app_settings, "_show_error", shown.append)

    def _broken(*a, **k):
        raise RuntimeError("Tk could not start")

    monkeypatch.setattr(app_settings, "SettingsWindow", _broken)
    assert app_settings.run(tmp_path / "config.toml") == 1
    assert len(shown) == 1 and "Tk could not start" in shown[0]


class _User32:
    """Records the calls _focus_existing makes."""

    def __init__(self, hwnd, iconic):
        self._hwnd, self._iconic, self.calls = hwnd, iconic, []

    def FindWindowW(self, cls, title):  # noqa: N802 - Win32 casing
        return self._hwnd

    def IsIconic(self, hwnd):  # noqa: N802
        return self._iconic

    def ShowWindow(self, hwnd, cmd):  # noqa: N802
        self.calls.append(("ShowWindow", hwnd, cmd))

    def SetForegroundWindow(self, hwnd):  # noqa: N802
        self.calls.append(("SetForegroundWindow", hwnd))


def test_focus_existing_restores_a_minimized_window_first():
    user32 = _User32(0x42, iconic=True)
    app_settings._focus_existing(user32)
    assert user32.calls == [("ShowWindow", 0x42, 9), ("SetForegroundWindow", 0x42)]


def test_focus_existing_just_raises_a_visible_window():
    user32 = _User32(0x42, iconic=False)
    app_settings._focus_existing(user32)
    assert user32.calls == [("SetForegroundWindow", 0x42)]


def test_focus_existing_does_nothing_without_a_window():
    user32 = _User32(0, iconic=False)
    app_settings._focus_existing(user32)
    assert user32.calls == []


def test_run_shows_a_broken_config_instead_of_silently_exiting(monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("timeout = 0\n")  # parses, but out of range
    monkeypatch.setattr(app_settings, "_acquire_singleton", lambda: True)
    shown = []
    monkeypatch.setattr(app_settings, "_show_error", shown.append)
    monkeypatch.setattr(
        app_settings, "SettingsWindow", lambda *a, **k: pytest.fail("opened on a bad config"),
    )
    assert app_settings.run(path) == 1
    assert len(shown) == 1 and str(path) in shown[0]


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


def test_letter_spaced_puts_hair_spaces_between_letters_not_words():
    assert app_settings.letter_spaced("AB CD") == "A B C D"


# --- the real window (needs customtkinter and a display) -------------------


@pytest.fixture
def make_window(tmp_path):
    """Build a withdrawn SettingsWindow over ``cfg``; ``w.saved_calls`` records
    every ``(cfg, path)`` handed to save."""
    pytest.importorskip("customtkinter")
    import time
    import tkinter

    built = []

    def _make(cfg):
        saved = []
        # Tk occasionally fails to read its own init.tcl/tk.tcl when
        # interpreters are created back to back (seen locally, ~1 in 10, never
        # in isolation) - retry that transient startup error; only a persistent
        # one (no display on a headless runner) turns into a skip.
        for attempt in range(3):
            try:
                w = app_settings.SettingsWindow(
                    cfg, tmp_path / "config.toml",
                    save=lambda cfg, path: saved.append((cfg, path)),
                )
                break
            except tkinter.TclError as exc:
                if attempt == 2:
                    pytest.skip(f"Tk cannot start here: {exc}")
                time.sleep(0.2)
        w.root.withdraw()
        w.saved_calls = saved
        built.append(w)
        return w

    yield _make
    for w in built:
        if not w._closed:
            w.root.destroy()


@pytest.fixture
def window(make_window):
    return make_window(
        config.Config(timeout=6.0, on_level=2, autostart=False, device_id=0x00050099)
    )


@pytest.fixture
def window_300(make_window):
    """A window over a hand-edited timeout the slider can't show (300 s)."""
    return make_window(config.Config(timeout=300.0))


def _drag_timeout(window, seconds):
    """What a drag does: customtkinter sets the variable, then calls command."""
    window._timeout.set(seconds)
    window._on_slider(float(seconds))


def test_window_caption_has_only_the_close_box(window):
    style = app_settings._window_style(app_settings._toplevel_hwnd(window.root))
    assert style & 0x00030000 == 0  # neither WS_MINIMIZEBOX nor WS_MAXIMIZEBOX


def test_window_starts_from_the_loaded_config(window):
    assert window._timeout.get() == 6
    assert window._entry.get() == "6"
    assert window._level.get() == 1  # on_level 2 -> middle detent
    assert window._autostart.get() is False


def test_entry_clamps_and_drives_the_slider(window):
    window._entry.delete(0, "end")
    window._entry.insert(0, "600")
    window._on_entry()
    assert window._timeout.get() == 60
    assert window._entry.get() == "60"
    assert round(window._timeout_slider.get()) == 60


def test_garbage_in_the_entry_keeps_the_current_value(window):
    window._entry.delete(0, "end")
    window._entry.insert(0, "soon")
    window._on_entry()
    assert window._timeout.get() == 6
    assert window._entry.get() == "6"


def test_brightness_slider_only_has_three_detents(window):
    assert window._level_slider.cget("number_of_steps") == 2
    assert window._level_slider.cget("from_") == 0
    assert window._level_slider.cget("to") == 2


def test_selected_tick_is_highlighted(window):
    window._level.set(2)  # as the slider does before calling its command
    window._on_level_change(2.0)
    colours = [label.cget("text_color") for label in window._tick_labels]
    assert colours == [
        app_settings.PALETTE["text-muted"],
        app_settings.PALETTE["text-muted"],
        app_settings.PALETTE["text"],
    ]


def test_save_writes_exactly_the_shown_values(window, tmp_path):
    _drag_timeout(window, 12)
    assert window._entry.get() == "12"
    window._level.set(2)
    window._autostart.set(True)
    window._on_save()
    assert window.saved_calls == [(
        config.Config(timeout=12.0, on_level=3, autostart=True, device_id=0x00050099),
        tmp_path / "config.toml",
    )]
    assert window._saved is True and window._closed is True


def test_flipping_only_autostart_keeps_a_hand_edited_timeout(window_300):
    assert window_300._entry.get() == "60"
    window_300._autostart.set(False)
    window_300._on_save()
    assert window_300.saved_calls[0][0].timeout == 300.0


def test_choosing_the_edge_value_on_purpose_saves_it(window_300):
    _drag_timeout(window_300, 30)
    _drag_timeout(window_300, 60)  # back to the value that was shown
    window_300._on_save()
    assert window_300.saved_calls[0][0].timeout == 60.0


def test_touching_the_slider_without_moving_it_counts_as_choosing(window_300):
    _drag_timeout(window_300, 60)  # a click on the knob where it already is
    window_300._on_save()
    assert window_300.saved_calls[0][0].timeout == 60.0


def test_retyping_the_shown_value_counts_as_choosing_it(window_300):
    window_300._entry.delete(0, "end")
    window_300._entry.insert(0, "60")
    window_300._on_save()
    assert window_300.saved_calls[0][0].timeout == 60.0


def test_a_failed_save_is_shown_and_keeps_the_window_open(window, monkeypatch):
    shown = []
    monkeypatch.setattr(
        app_settings, "_show_error", lambda text, owner=None: shown.append((text, owner))
    )

    def _disk_full(cfg, path):
        raise OSError("disk full")

    window._save = _disk_full
    window._on_save()
    assert len(shown) == 1 and "disk full" in shown[0][0]
    # Owned by the settings window, so the box is modal to it.
    assert shown[0][1] == app_settings._toplevel_hwnd(window.root) != 0
    assert window._closed is False and window._saved is False


def test_save_folds_in_a_half_typed_entry(window):
    window._entry.delete(0, "end")
    window._entry.insert(0, "25")  # typed, never confirmed with Return
    window._on_save()
    assert window.saved_calls[0][0].timeout == 25.0


def test_cancel_writes_nothing(window):
    window._timeout.set(40)
    window._on_cancel()
    assert window.saved_calls == []
    assert window._saved is False and window._closed is True
