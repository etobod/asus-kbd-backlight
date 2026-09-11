# DESIGN — asus-kbd-backlight

Implementation notes for the requirements in [PRD.md](PRD.md). Tracks code
version 0.2.1.

## Overview

```
key event ──► WH_KEYBOARD_LL hook ──► last_key = monotonic()      (hook thread)
                                           │
                          poll every 50 ms │
                                           ▼
                        idle < timeout ?  ──► desired state (on/off)   (worker thread)
                                           │
                             on change only│
                                           ▼
                           AsusAtkWmi_WMNB.DEVS(device_id, level)
```

Three threads are in play:

1. **Message-pump thread** (`app.main` → `hook.pump_messages`). Installs the
   hook, creates the tray window (`TrayIcon.install`), and runs
   `GetMessage`/`DispatchMessage`. A low-level hook is only serviced while its
   installing thread pumps messages, so installation and the pump must share a
   thread; the tray's `WndProc` is dispatched by that **same** loop — there is
   no second message loop anywhere in the daemon (NFR-6). This thread blocks
   until `WM_QUIT` (posted by the console-ctrl handler, the tray's Quit, or
   `WM_DESTROY`).
2. **Hook callback**, invoked by Windows on the pump thread. Does exactly one
   thing: `last_key = time.monotonic()`. No key code is read (NFR-3).
3. **Worker thread** (`Controller._run`). Every 50 ms it computes
   `idle_for = now - last_key` and calls `_apply(on = idle_for < timeout)`.
   `_apply` is a no-op unless the boolean differs from the last applied state,
   so `DEVS` is called only on transitions (NFR-2). `_apply` runs on this
   thread only — `Controller.pause()` (pump thread) just sets a flag and lets
   the next tick issue the call — so every COM call keeps its apartment
   affinity and no lock is needed.

## Module map

| Module | Responsibility |
|---|---|
| `hook.py` | `KeyboardIdleHook` (ctypes `WH_KEYBOARD_LL`), `pump_messages` |
| `elevate.py` | `is_admin`, `should_elevate` (pure decision), `relaunch_elevated` via `ShellExecuteW "runas"`, `spawn_settings` (de-elevated child, NFR-5) |
| `paths.py` | `asset()` / `asset_dir()` — bundled icons, `sys._MEIPASS` when frozen |
| `tray.py` | `TrayIcon` — hidden window, `Shell_NotifyIcon`, context menu, injected `on_settings`/`on_toggle_pause`/`on_quit` |
| `app_settings.py` | the unelevated `tkinter` settings window + pure helpers (`PALETTE`, `clamp_timeout`, `index_to_level`, `contrast_ratio`, `build_config`) |
| `autostart.py` | `reconcile` / `task_exists` / `action_target` over Task Scheduler `Schedule.Service` (FR-12) |
| `backlight.py` | `WmiBacklight` (real), `NullBacklight` (dry-run / non-Windows), `get_backlight` factory |
| `config.py` | `Config` dataclass (`timeout`/`on_level`/`device_id`/`autostart`), TOML `load` / `load_bytes` + atomic `save` from/to `%APPDATA%\asus-kbd-backlight\config.toml`, validation |
| `app.py` | `Controller` state machine (+ `pause`/`resume`/`toggle_pause`/`status_text`, live config reload) + CLI (`main`) |
| `__main__.py` | `python -m asus_kbd_backlight` entry point |

## Key decisions

### `DEVS` Control_status carries an enable bit

Keyboard backlight is set with `DEVS(0x00050021, 0x80 | level)`. A bare
`level` (0..3) only writes the level register: a subsequent read reports the
new value but the illumination never engages (the EC drops it). Bit 7 marks
the write as "apply now". Same convention as the Linux `asus-wmi` driver
(`ctrl_param = 0x80 | value`) and G-Helper (`brightness | 0x80`). The method
is called via `ExecMethod_` on the instance from `ExecQuery`, not on the class
object (which WMI rejects), with `Device_ID` / `Control_status` set by name.

### No state read-back (FR-1, R-3)

`DSTS` is unreliable across firmware revisions, so the tool never queries the
current level. `Controller._is_on` starts as `None`; the first `_apply(False)`
at startup forces a known-off state and sets it to `False`. Everything after is
a diff against that internal model.

### Hook stays on the pointer-free path (FR-2)

`WH_KEYBOARD_LL` does not receive mouse or touchpad events, so keyboard-only
idle detection needs no filtering logic — the OS does it. This is the whole
reason the tool exists; Armoury Crate / G-Helper watch global input instead.

### Polling the timestamp, not the backlight (NFR-1, NFR-2)

The hook must not block, so it cannot call WMI. Instead it writes a timestamp
and a cheap 50 ms poll on another thread decides the desired state. 50 ms keeps
turn-off latency far under the 500 ms target while costing negligible CPU
(a subtraction and a compare).

### Self-elevation happens once, and never for the settings window (FR-7)

Neither build carries a `requireAdministrator` manifest (no PyInstaller
`--uac-admin`): the exes start `asInvoker`, exactly like a source checkout. An
earlier build had the manifest on the windowed exe, but a manifest elevates
*every* launch of that exe — including `asus-kbd-backlight.exe --settings`, so
the settings window would have run elevated (breaking NFR-5) and Explorer would
have raised a UAC prompt each time it was opened from the tray. Instead
`main()` decides elevation first of all —
`should_elevate(args, admin=is_admin())` — and if it is true calls
`relaunch_elevated(argv)`, which re-launches with `ShellExecuteW(None, "runas",
…)` and returns. This runs **before** `_setup_logging`, and the about-to-exit
parent is told `to_file=False`: it must not open the rotating log file the
elevated child then owns, or a size-boundary rollover races between the two
processes on Windows.

`relaunch_elevated` passes the current working directory to `ShellExecuteW`:
a `runas` child otherwise starts in `C:\Windows\System32`, and a relative
`--config my.toml` would silently name a different (missing) file there. For
the same reason `main()` resolves `--config` to an absolute path up front, and
the settings shortcut `_spawn_via_explorer` writes carries the daemon's working
directory (Explorer would otherwise start the child in the target's folder).

The guard against a prompt loop is `--no-elevate`, appended to the relaunched
command line by `child_command` and always honoured by `should_elevate` —
deterministic, because a command-line argument cannot be lost in transit.
`relaunch_elevated` also sets `AKB_ELEVATED=1` in *this* process's environment
before calling `ShellExecuteW("runas", …)`. That is a **best-effort backstop,
not a second guaranteed guard**: a `runas`-elevated child is started by the
elevation broker (AppInfo / `consent.exe`), not as a direct child of this
process via `CreateProcess`, so Windows makes no promise it inherits an
environment variable set here — it exists as the hook for anyone driving the
program by setting the variable themselves before a *direct* (non-`runas`)
launch, e.g. from a script. `--no-elevate` is what must never be dropped from
`child_command`.

`--dry-run` never elevates: it touches no hardware (`NullBacklight`), and the
documented development command must not throw a UAC prompt. `--set LEVEL` never
elevates either — it is the device-id / level probe, and a `runas` relaunch
would put its output and any `BacklightError` on a detached process the caller
never sees. `--settings` never elevates — that is NFR-5, and it is the same
switch, so the settings window cannot accidentally acquire an admin token by
being started from the elevated daemon.

Declining the prompt is a clean exit: `ShellExecuteW` returns
`SE_ERR_ACCESSDENIED` (≤ 32), one line is logged and the process returns
non-zero rather than continuing half-working with a hook but no backlight
control.

### Icons are generated, not rasterised (FR-10)

`assets/icon.ico` and `assets/icon-paused.ico` are committed, and
`scripts/make-icon.py` regenerates them from stdlib only (`zlib` + `struct`,
supersampled 4× and box-filtered, PNG-compressed ICO entries at
16/24/32/48/256). The PRD sketched an ImageMagick/Inkscape step over an
`icon.svg`; a procedural drawing keeps the toolchain at "the interpreter that
already builds the project" and keeps the two variants guaranteed
pixel-identical apart from the desaturation and the missing glow. The paused
icon is greyscaled, so it reads as off without depending on colour vision.

**Gitignore trap.** `/scripts/` is blanket-ignored (added for the
`loc-planning` skill's own scratch area; see its commit message) with the note
"new scripts need `git add -f`" — `scripts/build.ps1` was already tracked
before that rule landed, but `scripts/make-icon.py` is not, and neither is
`assets/`. Both must be **force-added** on the first commit that includes them
(`git add -f scripts/make-icon.py assets/`), or a fresh clone has no icons and
`build.ps1`'s fallback has no generator to fall back to either.

Runtime lookup goes through `paths.asset()`, which returns `None` for a missing
file: a lost icon degrades the tray, it never stops the daemon (NFR-7).
`asset_dir()` resolves `sys._MEIPASS/assets` when frozen, else the repo
`assets/`. A bare `pip install` (no `assets/` in the wheel) therefore gets
`None` and the default tray icon — acceptable because the v0.2 deliverable is
the frozen exe; bundling `assets/` as package data is a follow-up.

### The tray shares the pump thread; its actions are injected (FR-8, NFR-6)

`TrayIcon` registers a window class and creates a **hidden top-level** window
(never `ShowWindow`n). It is not a message-only window: those do not receive
the broadcast `TaskbarCreated` message Explorer sends after a restart, and
re-adding the icon on that message is NFR-8. `Shell_NotifyIcon(NIM_ADD)` names
`WM_APP+1` as the callback message; the `WndProc` turns

- `WM_APP+1` with `WM_RBUTTONUP` → build a fresh popup menu and `TrackPopupMenu`
  (no `TPM_RETURNCMD`, so the choice comes back as `WM_COMMAND` and rejoins the
  one dispatch path);
- `WM_APP+1` with a left click / double click → `on_settings`;
- `WM_COMMAND` → `_dispatch(id)`, a plain dict from the three menu ids to
  `on_settings` / `on_toggle_pause` / `_quit`;
- `WM_QUERYENDSESSION` / `WM_CLOSE` / `WM_DESTROY` → `_quit`, which calls
  `on_quit` and then `PostQuitMessage(0)` so `pump_messages()` returns and the
  existing `finally: controller.stop()` runs the FR-6 shutdown;
- `TaskbarCreated` → `Shell_NotifyIcon(NIM_ADD)` again.

The three actions are constructor arguments, so `_dispatch`, the tooltip text
and the `TaskbarCreated` re-add are unit-tested by calling `_wndproc` directly
with the Win32 calls stubbed — no window, no real shell. Every handler —
including `_quit`, which is reached straight off the ctypes `WndProc` boundary
by `WM_CLOSE` / `WM_QUERYENDSESSION` — runs inside `_invoke`, which logs and
swallows exceptions: a raising callback must never take down the message pump
or skip the `PostQuitMessage(0)` that follows it.

**Known limitation: `WM_QUERYENDSESSION` and the FR-6 shutdown budget.**
`_quit` returns from `WM_QUERYENDSESSION` immediately (allowing the session to
end), but the actual cleanup — `Controller.stop()`, up to a 2 s worker join
plus a possible in-flight `set_level(OFF)` COM call — only runs after
`pump_messages()` returns, in `main()`'s `finally` block. Windows can
terminate a process that has not exited within its own shutdown budget during
a real logoff, so the "backlight off on exit" guarantee (FR-6) is not
airtight under `WM_QUERYENDSESSION` specifically (`WM_CLOSE` / Ctrl+C, which
are not time-boxed by Windows, are unaffected). Tightening this would mean
either shortening `stop()`'s join timeout or doing the `set_level` call
synchronously inside the handler, both a change to the core shutdown path
that needs its own review rather than a sweep-time patch.

Two Win32 lifecycle traps are handled explicitly:

- **Icon handle.** `LoadImageW` is called without `LR_SHARED`, so each icon
  load owns a real `HICON` that must be released. `_notify` only reloads when
  `_icon_paused_state` (which state the currently-held `HICON` reflects)
  differs from `_paused`, or none is loaded yet — a tooltip-only `NIM_MODIFY`
  (the 2 s `WM_TIMER` poll, below) touches neither disk nor GDI. When it does
  reload, `_destroy_icon` runs first and `remove()` calls it once more, so a
  long session that toggles Pause or survives several Explorer restarts does
  not leak a GDI handle per event.
- **`remove()` re-entrancy.** `DestroyWindow` dispatches `WM_DESTROY`
  synchronously and that handler calls back into `remove()`. `remove()`
  therefore does the `NIM_DELETE` first (it needs `_hwnd`), then clears
  `_hwnd`, then calls `DestroyWindow`; the re-entrant call returns at the
  `if self._hwnd is None` guard, so there is exactly one `NIM_DELETE` and one
  "tray icon removed" log line.
- **Menu dismissal.** `TrackPopupMenu` is bracketed by `SetForegroundWindow`
  *and* a trailing `PostMessage(hwnd, WM_NULL, 0, 0)` (MSDN Q135788) or the
  menu fails to close on the first click outside it.

`app._install_tray` builds the `TrayIcon` on the pump thread right before
`pump_messages()`. If `install()` raises `OSError` it is logged and the daemon
runs headless (NFR-7). `on_toggle_pause` calls `Controller.toggle_pause()` and
feeds the result to `TrayIcon.set_paused`, which pushes a `NIM_MODIFY` with the
new tooltip. **Pause is not persisted** (PRD open question 6): it is an
in-session `Controller._paused` flag.

`pause()` runs on the pump thread and does **only** `self._paused = True` — it
issues no backlight call, so every COM call stays on the worker thread that owns
the `CoInitialize` apartment and the cached WMI objects. The worker's next
`tick()` (≤ 50 ms) sees `_paused` and calls `_apply(on=False)` from the right
thread, and keeps re-asserting off every tick so a WMI-failure back-off that was
mid-flight at `pause()` is still retried. `_apply` also refuses `on=True`
whenever `_paused` is set, so a tick that read `_paused` as `False`, was
preempted by `pause()`, and resumes with `on=True` cannot relight the keyboard.
`_apply` is therefore worker-thread-only and needs no lock. *(A second copy of
the pause flag lives in `TrayIcon._paused` for the tooltip/menu label, kept in
sync by the `on_toggle_pause` closure; folding it into a single source of truth
is deferred until the settings window adds another pause path.)*

### Config is written atomically and re-read live (FR-11, FR-4)

`config.save(cfg, path=None)` serialises the four scalar keys by hand — a
TOML-writer dependency would only add weight to the frozen bundle for four
lines of output — into a `mkstemp` sibling and `os.replace`s it onto
`config.toml`, so a concurrent reader never sees a partial file and a failed
write leaves the old file intact. It `validated()`s first, so an out-of-range
`Config` never reaches disk. It writes a fixed banner plus the four keys and
nothing else: **a save from the settings window does not preserve comments or
unrecognised keys** a user hand-added (`device_id` *is* round-tripped). The file
banner says so; the hand-edit path is for the keys the window also owns.

The daemon picks changes up on its own. `Controller.__init__` takes a
`config_path` (the file `main()` loaded) and an `overrides` dict — the
`--timeout` / `--on-level` values, if any. Every 20th worker tick (~1 s,
`CONFIG_POLL_TICKS`) `_reload_config_if_changed` calls `_read_config()` once —
the **raw bytes** — and compares them to the last seen bytes: content, not
`(mtime, size)` (which two equal-length writes on a coarse FAT / exFAT /
network clock share) and not a re-open at parse time (which a
delete-and-recreate editor can turn into a silent default config). On a change
it parses **those same bytes** with `config.load_bytes`, re-applies `overrides`
on top (an explicit CLI value is never undone, but a hand-edit to the *other*
keys still lands), and swaps `_cfg` under `_cfg_lock`. Readers go through the
`config` property, which takes the same lock; `_cfg` is a frozen dataclass
swapped by one reference assignment, so a `timeout` / `on_level` change simply
takes effect on the next tick with no COM call (NFR-2). A `device_id` change
retargets `self._backlight.device_id` **and** clears `_is_on`, so the next tick
re-issues `set_level` against the new device rather than waiting for an idle
transition — safe because `_reload_config_if_changed` only ever runs on the
worker thread that owns the backend. A malformed or unreadable file is logged
once (deduped on the message, like the backlight-failure path) and the running
config kept; a later good save logs "config file parses again" and resumes.

`config.load(path)` is now `read_bytes` + `config.load_bytes(data)`; a missing
file still yields defaults, but the parse/validate body is reusable by the
byte-holding reload watcher above.

### The settings window is a separate, unelevated process (FR-9, FR-13, NFR-5)

`app_settings.py` is a small `customtkinter` window launched from the tray's
*Settings* entry (and by `asus-kbd-backlight --settings`). It `config.load()`s,
shows three controls, writes the file back through `config.save` and exits. It
never speaks to the daemon — the file is the channel, and the daemon's live
reload (above) applies the change within ~1 s.

**Toolkit — `customtkinter`, as PRD §13 specified.** 0.2.0 shipped a
plain-`tkinter`/`ttk` version (the R-7 fallback, to avoid the dependency). It
looked like a 1990s dialog: `tk.Scale`'s Motif-era trough and square slider,
`LabelFrame(relief="solid")` hard 1-px boxes with the title cut into the line,
a grey `clam` checkbox — and, worst, no DPI awareness, so Windows
bitmap-stretched the whole window at 125/150 % scaling into something blurry
and chunky. `customtkinter` fixes each of those in the toolkit rather than in
hand-drawn `Canvas` code: rounded, outlined `CTkFrame` cards; `CTkSlider` with
a filled track and `number_of_steps`, so the seconds slider rests on whole
seconds and the brightness slider only on its three detents; a `CTkSwitch`;
per-monitor DPI awareness and scaling (`ScalingTracker`); and a dark native
title bar once `set_appearance_mode("dark")` is on. The cost is one pure-Python
dependency (~1 MB plus `darkdetect`/`packaging`) and a
`--collect-data customtkinter` in `build.ps1` for its theme JSON and fonts —
only the settings process imports it.

Layout follows PRD §13: a *Backlight* card (seconds slider + clamping entry,
3-stop brightness slider with its ticks, the chosen one highlighted) and a
*Startup* card (the autostart switch), each under a letter-spaced
`text-muted` header (`letter_spaced` inserts hair spaces — Tk has no letter
tracking); 24 px outer padding, 16 px between cards, 16 × 14 px card padding;
the version left and Cancel / Save right in the footer, Save the only
accent-filled control. Esc cancels. Sizes are customtkinter's unscaled pixels.

The window opens **centred in the work area of the monitor under the
pointer** (`centered_in` + `_work_area_at_pointer`: `MonitorFromPoint` →
`GetMonitorInfoW.rcWork`), never *on* the pointer — opened from the tray that
is the screen's bottom-right corner, and half the window, Save included, would
land under the taskbar or off-screen. Coordinates may be negative on a monitor
left of the primary one. `_center` positions with `wm_geometry` because
`CTk.geometry` would rescale coordinates that are already physical pixels.

Two data-safety rules: a timeout the slider can't show exactly (`300` or `2.5`
s — legal in the file) is displayed clamped/rounded but **kept as-is on Save**
unless the user touched the timeout controls (`build_config`'s
`timeout_touched`: a slider drag or any edit of the box — tracked, not
inferred from the value, so deliberately picking the edge value 60 for a
file's 300 still saves 60), so opening
Settings to flip the autostart switch never rewrites it; `clamp_timeout` rounds
half-up and treats `inf`/`nan` as unparseable. And errors are shown, not just
logged — the windowed exe has no console, so a config that can't be loaded
(unreadable, not TOML, out of range) or a failed save pops a message box naming
the file and the reason (`_show_error`); a failed save keeps the window open.
A window that fails to open at all (Tk or customtkinter broken) is reported
the same way — `MessageBoxW` needs no Tk. A second click on Settings restores
the open window if it is minimized before raising it (`SetForegroundWindow`
alone leaves a minimized window minimized). Upstream of all this,
`Config.validated` rejects a non-finite timeout: TOML has `inf`/`nan` literals,
and an infinite timeout never turns the light off while `nan` compares false
with everything, so the light would never turn on.

**The native frame is dressed too, via DWM — not replaced.** customtkinter's
dark mode only sets `DWMWA_USE_IMMERSIVE_DARK_MODE` (Windows' generic
dark-grey caption). `style_native_frame` adds, on Windows 11,
`DWMWA_CAPTION_COLOR` = `bg`, `DWMWA_BORDER_COLOR` = `border`,
`DWMWA_TEXT_COLOR` = `text-muted` and rounded corners, so the caption blends
into the window instead of framing it in a lighter band; each attribute is set
independently and failures are ignored (Windows 10 keeps the dark-grey
caption; pre-20H1 builds get the old dark-mode id 19). Colours go through
`colorref`, because a Win32 `COLORREF` is `0x00BBGGRR` — reversed from the hex
string. A frameless `overrideredirect` window with a hand-drawn title bar was
rejected: it loses Alt+Tab, Aero Snap, the taskbar button and correct
per-monitor DPI moves. The caption shows our `icon.ico` (`iconbitmap`, set
before customtkinter's deferred default-icon timer, which then stands down)
instead of Tk's feather. `resizable(False, False)` only greys the maximize box
out, so `_make_dialog_frame` clears `WS_MINIMIZEBOX | WS_MAXIMIZEBOX` from the
window style (`dialog_style`) and asks for a frame redraw: the caption shows
Close alone, like any small fixed dialog.

Before handing a look change to the user, `scripts/snap-settings.py` (local,
gitignored, not shipped) renders the window to a PNG with `PrintWindow` — built
in-process from the source tree, or `--attach`ed to a frozen
`asus-kbd-backlight.exe --settings --no-elevate --config <scratch>` which it
then closes with `WM_CLOSE` (the Cancel path). It only ever touches the
unelevated settings window — never the daemon, the hook or the hardware.

**Everything testable is a pure function, and the rest is tested with a real
window.** `PALETTE`, `clamp_timeout` (`"600"→60`, `0→1`, `3.6→4`,
junk→fallback), `index_to_level` / `level_to_index` (detent 0/1/2 ↔
`on_level` 1/2/3), `contrast_ratio`, `letter_spaced` and `build_config`
(carrying `device_id` over untouched — it has no control, PRD open question 7)
live at module scope. The widget-level tests build the actual `SettingsWindow`
(withdrawn, with an injected `save`) and skip when Tk cannot start; the look
itself is still the user's call (TEST-PLAN).

**De-elevation (the NFR-5 trap).** The daemon is elevated, so a plain
`subprocess.Popen` would hand the child the admin token and the window would
write `config.toml` with an admin-owned ACL. `elevate.spawn_settings` therefore
branches on `is_admin()`: unelevated (a dev with `--no-elevate`) a plain `Popen`
is already right; elevated it hands the launch to Explorer (below). It never
uses the `runas` verb — that *raises* integrity, the opposite of what NFR-5
wants. Either way the child is started through `paths.windowless_executable()`:
the windowed `asus-kbd-backlight.exe` (even when the daemon is the console
debug build) or `pythonw.exe` from source, so no console window opens behind
the settings window. The same helper picks the program the logon task runs. A named mutex `AKB_SETTINGS_SINGLETON` (in `app_settings.py`) makes a
second launch focus the open window instead of opening another.

**Getting Explorer to do the launch, not a duplicated token.** The first
implementation duplicated Explorer's (medium-integrity) token and called
`CreateProcessWithTokenW` directly — the textbook Win32 mechanism for this.
On real hardware it consistently failed with `ERROR_ACCESS_DENIED`, even after
explicitly enabling every privilege the API documents: `SeDebugPrivilege`
(needed just to *open* the shell's token — UAC's split-token design makes the
daemon's elevated token and Explorer's a different security context even for
the same signed-in user) and `SeImpersonatePrivilege` (needed for the call
itself). It never worked, and the pattern — steal a shell's token, spawn a
process with it — is also a well-known technique security software watches
for. It was replaced with the Microsoft-documented alternative (Aaron
Margosis's "ShellExecute from an explorer window"): `_spawn_via_explorer`
writes a `.lnk` shortcut (`win32com.client`'s `WScript.Shell.CreateShortCut`)
carrying the real target/arguments, then runs `explorer.exe <path-to-lnk>`.
Explorer is single-instance and already running at medium integrity, so the
*existing* Explorer process reads the shortcut and does the actual launch —
no token duplication, no elevated privilege of any kind needed here. The
`.lnk` is cleaned up a few seconds later (best-effort; a leftover file in
`%TEMP%` is harmless) since Explorer reads it asynchronously and deleting it
immediately would race that read.

### Start with Windows is a scheduler task the daemon reconciles (FR-12, R-8)

The daemon needs Administrator rights, and the Startup folder / `HKCU\Run`
can only start it unelevated, so it would ask for UAC at every logon. The
supported
autostart is a Task Scheduler entry with *Run with highest privileges*
(`RunLevel = HIGHEST`) and an *At log on* trigger — it starts the daemon
elevated at logon with **no** prompt.

`autostart.py` drives it through `win32com` `Schedule.Service` (pywin32 is
already a dependency; no `schtasks.exe` quoting). `reconcile(enabled) -> bool`
is called by the **elevated daemon** at startup and after every live reload —
never by the settings window (NFR-5), which only flips the `autostart` key and
lets the daemon react. It runs **only for a default-location, elevated run**
(`_manages_autostart`: admin, not `--dry-run`, on Windows, no explicit
`--config`) — the scheduled task has no `--config`, so managing it from a run
pointed at another file would register a task that reads a *different* config
at logon; a one-off `--config` test run must not leave a task or a
freshly-written file behind; and a `--no-elevate` dev run without admin rights
would just have every `Schedule.Service` call fail (autostart.py's own
contract is that the *elevated* process owns the task).

Every reconcile — the first one at startup and every one a live reload
triggers — goes through `app._make_autostart_reconciler`, which dispatches to
a throwaway thread: `Schedule.Service` COM plus an on-disk task write can
block for tens of ms, sometimes much longer under load, and this daemon is
literally the process the registered logon trigger launches, so FR-1 (forced
off at startup) and the keyboard hook must not wait on it. The reconciler also
serialises the spawned threads via a lock plus a sequence number: two
reconciles landing close together (the startup one and an almost-immediate
hand-edit, or two settings-window saves inside one poll window) must converge
on the most recently *requested* state, not whichever thread happens to
*finish* last — a thread that loses the race for the lock checks, once it gets
in, whether a newer request has already superseded it and no-ops if so.

The decision:

- `enabled` and the task is absent → register it;
- `enabled` and it exists → re-register it, so a moved exe or a switched Python
  self-heals (`action_target()` is recomputed every time through
  `paths.windowless_executable()`: the windowed exe when frozen — even if the
  daemon itself is the debug build — else `pythonw -m asus_kbd_backlight`, so
  no console flashes at logon);
- not `enabled` and it exists → delete it.

Any COM failure is logged and swallowed **and `reconcile` returns `False`** — a
scheduler hiccup must not take the daemon down (R-8), but `--install-task` /
`--uninstall-task` (which wrap `reconcile(True)` / `reconcile(False)`) turn that
`False` into a non-zero exit so a script or a person sees the failure. Those
sub-commands do **not** self-elevate (like `--set`): they run in place and
report, rather than handing the result to a detached elevated process. First
run with no config file: `config.load` returns `autostart=True`, `config.save`
writes the file so the checkbox state is visible, and the task is registered
once, silently (PRD open question 10).

`_task_exists` lists the root folder's tasks (`GetTasks(TASK_ENUM_HIDDEN)`,
names compared case-insensitively) instead of calling `GetTask` and decoding a
"not found" error: pywin32 reports a failing automation call as
`DISP_E_EXCEPTION` with the real code buried in the excepinfo, which is easy to
misread. A failure of the listing itself — access denied, the service
unreachable — propagates, so `reconcile(False)` reports failure instead of
skipping the delete and claiming success. The task runs at priority 4 (normal) rather than
Task Scheduler's default 7 (below normal): the daemon services the low-level
keyboard hook, and a starved hook thread can blow `LowLevelHooksTimeout`.
`--install-task` / `--uninstall-task` never self-elevate (so their exit code
reaches the caller), and without admin rights they exit 1 with a clear message
rather than attempt a registration that is bound to be denied.

The COM object is injectable, so `reconcile`'s create/refresh/delete/no-op
decision is unit-tested against a fake `Schedule.Service`; the real
registration and the logon itself are manual (TEST-PLAN).

### Failure handling (NFR-4)

`WmiBacklight` wraps every COM failure in `BacklightError`. `Controller._apply`
catches it, logs to stderr, and returns **without** updating `_is_on`, so the
next tick retries. A hardware fault therefore degrades to "keeps trying" rather
than "desynced model" or "crash with the hook still installed". `Controller.stop`
always runs `_apply(False)` then `hook.uninstall()` in a `finally` block.

## Open items (from PRD §10)

- Timeout default 3 s vs 5 s — needs field use.
- Wake on key-down only vs key-up too — currently any `HC_ACTION`.
- Disable on AC power — not implemented; would be a config flag + power-status
  poll.
- Lone modifier keys waking the backlight — currently they do.

## Testing

`tests/` covers `config.py` (defaults, overrides, validation, hex `device_id`
parsing) and the `Controller` state machine against `NullBacklight` — verifying
that `DEVS` equivalents fire only on transitions and that startup forces off.
The ctypes hook and real WMI path are not unit-tested; they require Windows,
elevation, and physical hardware, and are exercised manually per the
verified-models table in the README.
