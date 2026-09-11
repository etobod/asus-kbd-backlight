# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-09-11

The settings window, made to look like it belongs on a 2026 desktop, and a
clean way to open it: no console window, no UAC prompt, never elevated.

### Changed
- **The settings window is rebuilt on `customtkinter`** (as the PRD originally
  specified). The plain-`tkinter` version looked like a 1990s dialog: square
  Motif-style sliders, hard boxed frames, and — with no DPI awareness — a
  blurry, bitmap-stretched window at 125/150 % display scaling. Now: rounded
  "Backlight" / "Startup" cards, filled slider tracks that snap to whole
  seconds and to the three brightness stops, a toggle switch, crisp text at
  any scaling, and a dark title bar. Esc closes it. It opens centred on the
  screen that has the pointer (it used to be centred *on* the pointer — from
  the tray, half the window ended up under the taskbar). A config that can't
  be loaded, or a save that fails, now shows a message box instead of doing
  nothing; a hand-set timeout the slider can't show (e.g. 300 s) is left
  alone on Save unless you change it. The title bar and border are dark — in
  the window's own navy on Windows 11 — and show the app's icon instead of
  Tk's feather; the caption has only a Close button. New dependency:
  `customtkinter` (settings process only; bundled with `--collect-data`).

### Fixed
- `timeout = inf` / `nan` (TOML allows both; `1e999` parses to inf) is now
  rejected like any other invalid timeout — `inf` meant the light never went
  off, `nan` that it never came on. So is an integer timeout too large for a
  float, which used to raise past the live reload's error handling and stop
  the worker thread. A settings window that fails to open now
  says so in a message box instead of silently doing nothing, and clicking
  Settings again restores the open window if it was minimized.
- A relative `--config` now means the same file in every process: the
  elevated re-launch keeps the caller's working directory (it used to start in
  `System32`), the path is made absolute up front, and the settings window
  starts in the daemon's working directory. The logon task now runs at normal priority instead of Task
  Scheduler's below-normal default. `--install-task` / `--uninstall-task` / `--set` exit
  1 with a clear message when not run as administrator, and an access-denied
  task lookup is reported as a failure instead of "not installed".
- Opening Settings no longer pops up a console window, and the released exe
  no longer carries a `requireAdministrator` manifest. Settings (and the logon
  task) now start through the console-less program — the windowed exe even
  when the daemon is the debug build, `pythonw.exe` from source. Without the
  manifest the exe self-elevates once at start like a source checkout does;
  with it, opening Settings from the tray would have raised a UAC prompt every
  time and run the window elevated (NFR-5).
- Opening Settings from the elevated tray never opened the window: the
  original de-elevation mechanism (duplicate Explorer's token, then
  `CreateProcessWithTokenW`) consistently failed with `ERROR_ACCESS_DENIED`
  even after every privilege the Win32 API documents was explicitly enabled
  (`SeDebugPrivilege` to open the shell's token across UAC's split-token
  boundary, `SeImpersonatePrivilege` for the call itself) — and that pattern
  is also a well-known "token theft" signature some security software blocks
  outright. Replaced with the Microsoft-documented alternative: write a
  `.lnk` shortcut carrying the real command line, then hand it to
  `explorer.exe`, which is already running unelevated and does the actual
  launch itself. No token duplication, no special privileges — the
  `_shell_token` / `_enable_privilege` / `_spawn_with_token` machinery (and
  the `ctypes.get_last_error()`/`use_last_error=True` and handle-overflow bugs
  found while diagnosing it) is gone with it.

## [0.2.0] - 2026-09-11

The v0.2 tray UI: the program elevates itself, lives in the system tray with a
working menu, has a dark settings window that writes `config.toml` and is
picked up live, and manages a Task Scheduler autostart entry.

### Added
- **Start with Windows (FR-12).** A Task Scheduler entry (`asus-kbd-backlight`,
  *Run with highest privileges*, *At log on*) starts the daemon elevated at
  logon with no UAC prompt. A default-location daemon run creates / refreshes /
  removes it to match the `autostart` config key (default `true`) — on startup
  and on every live reload (off the worker tick, on its own thread) — so the
  **Start with Windows** checkbox is all the user touches. New `--install-task`
  / `--uninstall-task` flags wrap the same code, run unelevated, and exit
  non-zero if the scheduler call fails. The action path is re-derived each time,
  so moving the exe self-heals. A run with an explicit `--config` leaves the
  task alone. Replaces the hand-rolled Task Scheduler instructions in the
  README.
- **Settings window (FR-9, FR-13).** `asus-kbd-backlight --settings` (and the
  tray's *Settings* entry) opens a small dark window — deep-navy ground, muted
  rose accent, the fixed PRD §13 palette — with a 1–60 s "stay on" slider synced
  to a clamping entry box, a 3-detent brightness slider (33 / 66 / 100 %), a
  "Start with Windows" checkbox and a version footer. Save writes `config.toml`
  and the running daemon applies it live (FR-11); Cancel / X leaves the daemon
  untouched. A named mutex focuses an already-open window instead of opening a
  second. Built on stdlib `tkinter` — no new dependency (R-7 resolved).
- **The settings window is spawned de-elevated (NFR-5).** The daemon runs as
  Administrator; `elevate.spawn_settings` starts the window with a
  medium-integrity token borrowed from the shell
  (`CreateProcessWithTokenW`), so it and every `config.toml` write stay
  unelevated. It never uses `runas`.
- **Live configuration (FR-11) and `autostart` key (FR-12 prep).** `config.toml`
  gains `autostart` (default `true`). Saving the file — by hand or from the
  settings window — is applied by the running daemon within ~1 s: the worker
  re-stats the file every 20th tick and swaps the config under a lock, with no
  backlight/COM call for a `timeout` / `on_level` change. A malformed edit is
  logged once and the previous config kept until it parses again. New
  `config.save()` writes atomically (temp file + `os.replace`). A `--timeout` /
  `--on-level` override on the CLI disables the watch so a reload can't undo it.
- **System-tray icon and menu (FR-8, FR-10).** The daemon now shows a tray icon
  whose right-click menu is Settings / Pause · Resume / Quit; left / double
  click opens Settings. The tooltip shows the version, the running / paused
  state and the current idle timeout, and updates live (a `WM_TIMER` poll) when
  the timeout is changed in the file or the settings window. **Pause** forces
  the backlight off and swaps in a desaturated icon until **Resume** (not
  persisted across restarts). The tray window shares the existing message loop
  (NFR-6), re-adds itself after an Explorer restart (NFR-7), and any failure to
  create it drops to headless rather than aborting. `Quit` performs the FR-6
  clean shutdown.
- **Self-elevation (FR-7).** Starting the program unelevated re-launches it once
  through `ShellExecuteW "runas"`; the child gets `--no-elevate` on its command
  line *and* `AKB_ELEVATED=1` in its environment, so a prompt loop is impossible.
  Declining the prompt logs one line and exits non-zero instead of running
  half-working. `--dry-run`, `--set` and `--settings` never elevate (their
  output stays on the caller's terminal). New `--settings` / `--no-elevate`
  flags.
- **Application icons (FR-10).** `assets/icon.ico` and `assets/icon-paused.ico`
  (16/24/32/48/256 px), generated by `scripts/make-icon.py` with nothing but the
  standard library; the windowed build embeds the icon and a
  `requireAdministrator` manifest (`--uac-admin`) and ships `assets/` alongside.

## [0.1.0] - 2026-09-10

### Added
- Initial project skeleton: `WH_KEYBOARD_LL` idle hook, WMI backlight backend
  (`AsusAtkWmi_WMNB` / `DEVS`), TOML configuration, `Controller` state machine,
  CLI entry point (`asus-kbd-backlight`, `python -m asus_kbd_backlight`).
- PRD and DESIGN documents under `docs/`.
- `--dry-run` mode using a no-op backlight backend.
- Unit tests for configuration and the controller state machine.
- `scripts/build.ps1` now produces two one-file executables: `asus-kbd-backlight.exe`
  (no console, for autostart) and `asus-kbd-backlight-debug.exe` (console + live log).
- Logging to stderr and to a rotating file at
  `%APPDATA%\asus-kbd-backlight\asus-kbd-backlight.log`; `--debug` for verbose output.
- Startup warning when not running elevated; repeated backlight failures are
  logged once instead of every poll.
- Console control handler so Ctrl+C / Ctrl+Break / closing the window shuts the
  hook down cleanly.

### Fixed
- Backlight would not turn on: `DEVS` was sent a bare level `1..3`, which only
  writes the level register (a readback reports it) while the illumination
  stays dark. `Control_status` now carries bit 7 (`0x80 | level`), matching the
  Linux `asus-wmi` driver and G-Helper. Verified on TUF Gaming A14 (FA401).
- `DEVS` is invoked with `ExecMethod_` on the queried instance (the class
  object is rejected with "Invalid method Parameter(s)") and all COM calls run
  on the worker thread, fixing an `Invalid parameter` / `CO_E_NOTINITIALIZED`
  pair seen on the first real run.
- `hook.install()` raised `OSError [WinError 126]` (notably in the frozen
  build): `GetModuleHandleW` had no `restype`, so its 64-bit handle was
  truncated to 32 bits and rejected as an `hMod`. Handle-returning calls now
  declare their types, and the low-level hook is installed with a NULL module
  handle (with the real handle as a fallback).
