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
