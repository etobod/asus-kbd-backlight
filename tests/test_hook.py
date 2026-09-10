"""NFR-3 (privacy): the keyboard hook callback records only a timestamp.

Structural, not exhaustive — but it fails loudly if a future edit makes the
callback inspect or stash anything derived from the key event.
"""

from asus_kbd_backlight.hook import HC_ACTION, KeyboardIdleHook


def test_callback_records_timestamp_only():
    h = KeyboardIdleHook()
    attrs_before = set(vars(h))
    h.last_key = 0.0

    rc = h._callback(HC_ACTION, 0x0104, 0xDEADBEEF)  # wParam=WM_SYSKEYDOWN, lParam garbage

    assert h.last_key > 0.0                 # timestamp advanced
    assert set(vars(h)) == attrs_before     # nothing new stored
    assert h.last_key != 0xDEADBEEF         # not derived from lParam
    assert isinstance(rc, int)              # passed the event on


def test_callback_ignores_non_action_codes():
    h = KeyboardIdleHook()
    h.last_key = 0.0
    h._callback(-1, 0x0100, 0x1234)         # nCode < 0 -> must just forward
    assert h.last_key == 0.0
