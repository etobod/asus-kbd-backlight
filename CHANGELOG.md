# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- `hook.install()` raised `OSError [WinError 126]` (notably in the frozen
  build): `GetModuleHandleW` had no `restype`, so its 64-bit handle was
  truncated to 32 bits and rejected as an `hMod`. Handle-returning calls now
  declare their types, and the low-level hook is installed with a NULL module
  handle (with the real handle as a fallback).
