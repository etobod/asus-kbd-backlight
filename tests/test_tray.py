"""FR-8: the tray dispatch table, tooltip text and Explorer-restart re-add.

The window itself, the menu pixels and the icon swap are manual (TEST-PLAN);
what is pinned here is that a menu id reaches the right callback, that a
handler blowing up cannot crash the message pump, that ``TaskbarCreated``
re-adds the icon, and that the tooltip tracks the pause state. Every Win32 call
is stubbed so no test touches the real shell.
"""

import sys
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="tray is Windows-only")

from asus_kbd_backlight import tray as tray_mod  # noqa: E402
from asus_kbd_backlight.tray import TrayIcon  # noqa: E402


@pytest.fixture
def win32(monkeypatch):
    """Neutralise the Win32 surface: no real shell icon, no real quit post."""
    notify = Mock(return_value=1)
    post_quit = Mock()
    monkeypatch.setattr(tray_mod._shell32, "Shell_NotifyIconW", notify)
    monkeypatch.setattr(tray_mod._user32, "PostQuitMessage", post_quit)
    monkeypatch.setattr(TrayIcon, "_load_icon", lambda self: None)
    return Mock(notify=notify, post_quit=post_quit)


@pytest.fixture
def callbacks():
    return Mock(settings=Mock(), toggle=Mock(), quit=Mock())


def _tray(callbacks):
    return TrayIcon(
        on_settings=callbacks.settings,
        on_toggle_pause=callbacks.toggle,
        on_quit=callbacks.quit,
    )


def test_menu_ids_reach_their_callbacks(win32, callbacks):
    t = _tray(callbacks)
    t._dispatch(TrayIcon.ID_SETTINGS)
    t._dispatch(TrayIcon.ID_PAUSE)
    callbacks.settings.assert_called_once_with()
    callbacks.toggle.assert_called_once_with()


def test_quit_id_calls_back_then_posts_wm_quit(win32, callbacks):
    t = _tray(callbacks)
    t._dispatch(TrayIcon.ID_QUIT)
    callbacks.quit.assert_called_once_with()
    win32.post_quit.assert_called_once_with(0)


def test_unknown_command_id_is_ignored(win32, callbacks):
    t = _tray(callbacks)
    t._dispatch(4242)  # e.g. a stray WM_COMMAND
    callbacks.settings.assert_not_called()
    callbacks.toggle.assert_not_called()
    callbacks.quit.assert_not_called()


def test_a_raising_handler_does_not_propagate(win32, callbacks):
    callbacks.settings.side_effect = RuntimeError("boom")
    t = _tray(callbacks)
    t._dispatch(TrayIcon.ID_SETTINGS)  # must not raise - would kill the pump


def test_wm_command_is_routed_through_dispatch(win32, callbacks):
    t = _tray(callbacks)
    t._wndproc(0, tray_mod.WM_COMMAND, TrayIcon.ID_PAUSE, 0)
    callbacks.toggle.assert_called_once_with()


def test_left_click_opens_settings(win32, callbacks):
    t = _tray(callbacks)
    t._wndproc(0, tray_mod.WM_TRAYICON, 0, tray_mod.WM_LBUTTONDBLCLK)
    callbacks.settings.assert_called_once_with()


def test_right_click_shows_the_menu(win32, callbacks, monkeypatch):
    shown = Mock()
    monkeypatch.setattr(TrayIcon, "_show_menu", shown)
    t = _tray(callbacks)
    t._wndproc(0, tray_mod.WM_TRAYICON, 0, tray_mod.WM_RBUTTONUP)
    shown.assert_called_once()


def test_wm_close_and_endsession_quit(win32, callbacks):
    t = _tray(callbacks)
    assert t._wndproc(0, tray_mod.WM_QUERYENDSESSION, 0, 0) == 1
    t._wndproc(0, tray_mod.WM_CLOSE, 0, 0)
    assert callbacks.quit.call_count == 2
    assert win32.post_quit.call_count == 2


def test_taskbar_created_readds_the_icon(win32, callbacks):
    t = _tray(callbacks)
    win32.notify.reset_mock()
    t._wndproc(0, t._taskbar_created, 0, 0)
    win32.notify.assert_called_once()
    assert win32.notify.call_args[0][0] == tray_mod.NIM_ADD


def test_tooltip_tracks_pause_state(win32, callbacks):
    t = _tray(callbacks)
    assert "running" in t.tooltip()
    t.set_paused(True)
    assert "paused" in t.tooltip()
    t.set_paused(False)
    assert "running" in t.tooltip()


def test_load_icon_picks_the_paused_variant_when_paused(callbacks, monkeypatch):
    asked = []
    monkeypatch.setattr(tray_mod.paths, "asset", lambda name: asked.append(name) or None)
    t = _tray(callbacks)

    t._load_icon()
    t._paused = True
    t._load_icon()
    assert asked == ["icon.ico", "icon-paused.ico"]


def test_status_provider_supplies_the_tooltip_and_falls_back_on_error(win32, callbacks):
    text = ["asus-kbd-backlight 0.2.0 — running, off after 3s"]
    t = TrayIcon(
        on_settings=callbacks.settings, on_toggle_pause=callbacks.toggle,
        on_quit=callbacks.quit, status_provider=lambda: text[0],
    )
    assert t.tooltip() == text[0]
    text[0] = "off after 6s"
    assert "6s" in t.tooltip()

    def boom():
        raise RuntimeError("nope")

    t._status_provider = boom
    assert "running" in t.tooltip()  # fell back to the static string, no raise


def test_wm_timer_pushes_a_modify_only_when_the_tooltip_changed(win32, callbacks):
    tip = ["off after 3s"]
    t = TrayIcon(
        on_settings=callbacks.settings, on_toggle_pause=callbacks.toggle,
        on_quit=callbacks.quit, status_provider=lambda: tip[0],
    )
    t._hwnd = 77
    t._notify(tray_mod.NIM_ADD)          # seeds _last_tip
    win32.notify.reset_mock()

    t._wndproc(77, tray_mod.WM_TIMER, tray_mod._TOOLTIP_TIMER_ID, 0)
    win32.notify.assert_not_called()     # unchanged -> no churn

    tip[0] = "off after 30s"
    t._wndproc(77, tray_mod.WM_TIMER, tray_mod._TOOLTIP_TIMER_ID, 0)
    win32.notify.assert_called_once()
    assert win32.notify.call_args[0][0] == tray_mod.NIM_MODIFY


def test_set_paused_pushes_a_modify_when_installed(win32, callbacks):
    t = _tray(callbacks)
    t._hwnd = 1234  # pretend install() ran
    win32.notify.reset_mock()
    t.set_paused(True)
    win32.notify.assert_called_once()
    assert win32.notify.call_args[0][0] == tray_mod.NIM_MODIFY


def test_notify_frees_the_icon_on_a_paused_state_change(win32, callbacks, monkeypatch):
    # LoadImageW is used without LR_SHARED, so a genuine icon swap owns an
    # HICON that must be released or the daemon leaks a GDI handle per Pause
    # toggle. A tooltip-only NIM_MODIFY (unchanged paused state) must not
    # reload at all - see test_notify_skips_the_icon_reload_when_unchanged.
    handles = iter([111, 222])
    monkeypatch.setattr(TrayIcon, "_load_icon", lambda self: next(handles))
    destroyed = Mock()
    monkeypatch.setattr(tray_mod._user32, "DestroyIcon", destroyed)
    t = _tray(callbacks)
    t._hwnd = 1  # pretend install() ran

    t._notify(tray_mod.NIM_ADD)       # nothing loaded yet -> loads 111
    t._paused = True
    t._notify(tray_mod.NIM_MODIFY)    # paused state changed -> frees 111, loads 222

    assert [c.args[0] for c in destroyed.call_args_list] == [111]
    assert t._hicon == 222


def test_notify_skips_the_icon_reload_when_paused_state_is_unchanged(win32, callbacks, monkeypatch):
    # A tooltip-only update (e.g. a live-reloaded timeout, polled every 2s)
    # must not touch disk (LoadImageW) or GDI (DestroyIcon) at all.
    load_icon = Mock(return_value=111)
    monkeypatch.setattr(TrayIcon, "_load_icon", load_icon)
    destroyed = Mock()
    monkeypatch.setattr(tray_mod._user32, "DestroyIcon", destroyed)
    t = _tray(callbacks)
    t._hwnd = 1

    t._notify(tray_mod.NIM_ADD)
    load_icon.reset_mock()

    t._notify(tray_mod.NIM_MODIFY, tip="off after 6s")
    t._notify(tray_mod.NIM_MODIFY, tip="off after 9s")

    load_icon.assert_not_called()
    destroyed.assert_not_called()
    assert t._hicon == 111


def test_remove_frees_the_icon_and_is_reentrant_via_wm_destroy(win32, callbacks, monkeypatch):
    monkeypatch.setattr(TrayIcon, "_load_icon", lambda self: 999)
    destroyed = Mock()
    monkeypatch.setattr(tray_mod._user32, "DestroyIcon", destroyed)
    t = _tray(callbacks)

    def fake_destroy_window(hwnd):
        # Windows dispatches WM_DESTROY synchronously from DestroyWindow; that
        # handler calls back into remove().
        t._wndproc(hwnd, tray_mod.WM_DESTROY, 0, 0)

    destroy_window = Mock(side_effect=fake_destroy_window)
    monkeypatch.setattr(tray_mod._user32, "DestroyWindow", destroy_window)

    t._hwnd = 4321
    t._hicon = 999
    win32.notify.reset_mock()
    t.remove()

    deletes = [c for c in win32.notify.call_args_list if c.args[0] == tray_mod.NIM_DELETE]
    assert len(deletes) == 1          # not doubled by the re-entrant remove()
    destroy_window.assert_called_once_with(4321)
    destroyed.assert_called_once_with(999)
    assert t._hwnd is None


def test_a_raising_quit_callback_still_posts_wm_quit(win32, callbacks):
    # WM_CLOSE / WM_QUERYENDSESSION reach _quit straight off the WndProc
    # boundary; a raising on_quit must not skip PostQuitMessage.
    callbacks.quit.side_effect = RuntimeError("boom")
    t = _tray(callbacks)
    t._wndproc(0, tray_mod.WM_CLOSE, 0, 0)  # must not raise
    win32.post_quit.assert_called_once_with(0)
