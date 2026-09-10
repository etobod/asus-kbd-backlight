import pytest

from asus_kbd_backlight import backlight
from asus_kbd_backlight.backlight import (
    BacklightError,
    NullBacklight,
    WmiBacklight,
    encode_control_status,
)


@pytest.mark.parametrize(
    "level, expected",
    [(0, 0x80), (1, 0x81), (2, 0x82), (3, 0x83)],
)
def test_encode_control_status_sets_bit7(level, expected):
    # Regression guard: dropping bit 7 ships a keyboard that never lights.
    assert encode_control_status(level) == expected


@pytest.mark.parametrize("bad", [-1, 4, 0x80, "1", None])
def test_encode_control_status_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        encode_control_status(bad)


@pytest.mark.parametrize("bad", [-1, 4, 99])
def test_nullbacklight_validates_level(bad):
    with pytest.raises(ValueError):
        NullBacklight().set_level(bad)


def test_nullbacklight_records_level():
    b = NullBacklight()
    b.set_level(2)
    assert b.last_level == 2


def test_wmibacklight_wraps_com_failure_and_leaves_cache_empty(monkeypatch):
    """A raw COM failure in _bind must become BacklightError, not escape (NFR-4),
    and must not half-populate the cache."""
    import win32com.client

    def boom(_moniker):
        raise Exception("Access is denied.")

    monkeypatch.setattr(win32com.client, "GetObject", boom)

    wb = WmiBacklight()
    with pytest.raises(BacklightError) as ei:
        wb.set_level(1)

    assert "Administrator" in str(ei.value)  # 'denied' -> admin hint
    assert wb._instance is None and wb._devs is None  # cache not wedged


def test_get_backlight_dry_run_is_null():
    assert isinstance(backlight.get_backlight(0x50021, dry_run=True), NullBacklight)
