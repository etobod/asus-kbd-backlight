# PRD — asus-kbd-backlight

**Status:** draft
**Version:** 0.1
**Date:** 2026-09-09

---

## 1. Problem

ASUS laptops control keyboard backlight in four discrete steps: 0% / 33% / 66% / 100%. The 33% minimum is a firmware default, and per ASUS support it cannot be lowered further in static mode. In a dark room that level is too bright and distracting.

The built-in idle timeouts (Armoury Crate, G-Helper) do not solve this, because they measure idleness globally — any input event, including a touchpad tap or mouse movement, wakes the backlight. As a result the keyboard stays lit while the user is only reading or scrolling.

## 2. Goal

The keyboard backlight is lit only while the user is actually typing, and turns itself off shortly after the last keystroke.

## 3. Success metrics

| Metric | Target |
|---|---|
| Lit time during a read-only session (30 min, mouse/touchpad only) | 0 s |
| Delay between keystroke and backlight turning on | < 100 ms |
| Delay between timeout expiry and backlight turning off | < 500 ms |
| Impact on typing latency | imperceptible (no blocking work in the hook) |
| Idle CPU usage | < 0.5% |

## 4. Non-goals

Deliberately out of scope:

- Lowering brightness below 33%. Not achievable in software; it requires a hardware modification (a series resistor on the backlight FFC cable).
- Per-key RGB, Aura effects, or colour control.
- Replacing Armoury Crate or G-Helper in any other respect (fan curves, power profiles, charge limits).
- Support for untested models. The project makes no compatibility claim across the ASUS line.
- Controlling backlight on external keyboards.
- A GUI. Configuration lives in a file.

## 5. Users

**Primary:** an ASUS laptop owner with a backlit keyboard who works at night or in a dark room, and is comfortable running a script with administrator privileges.

**Secondary:** owners of other ASUS models (Zenbook, ProArt, Vivobook) who find the repository while searching for the same problem and want to adapt `DEV_ID` and the level mapping to their hardware.

## 6. Functional requirements

### FR-1 — Turn off at startup
As a user, I want the backlight turned off when the program starts, regardless of its prior state.

*Acceptance:* after launch the backlight is off, whether or not it was lit beforehand. The program does not depend on reading state back from firmware.

### FR-2 — Keyboard-only wake
As a user, I want the backlight to turn on when any key is pressed, and **not** to react to mouse or touchpad input.

*Acceptance:* a keystroke turns the backlight on. Mouse movement, clicks, touchpad taps and scrolling do not.

### FR-3 — Idle timeout
As a user, I want the backlight to turn off after a configured interval following the last keystroke.

*Acceptance:* default timeout is 3 s. Continuous typing keeps the backlight steady, with no flicker.

### FR-4 — Configuration
As a user, I want to change the timeout and brightness level without editing source code.

*Acceptance:* timeout, brightness level and device identifier are configurable outside the code.

### FR-5 — Background operation
As a user, I want the program to run invisibly and start with the system.

*Acceptance:* no console window. A documented way to add it to startup with elevated privileges.

### FR-6 — Clean shutdown
As a user, I want closing the program to restore normal keyboard behaviour.

*Acceptance:* the hook is unregistered and backlight control returns to the system.

## 7. Non-functional requirements

- **NFR-1** — The keyboard hook performs no blocking work. Backlight control happens off the hook thread.
- **NFR-2** — A control call is issued only on state change, never on a polling cadence.
- **NFR-3** — The program does not log keystroke content. Only an event timestamp is recorded. This is critical: the tool installs a global keyboard hook and must be auditable on this point.
- **NFR-4** — A hardware communication failure must not leave the backlight in an undefined state, nor crash the process without unregistering the hook.

## 8. Assumptions and dependencies

- Windows 10/11.
- The ASUS ACPI/WMI interface (`AsusAtkWmi_WMNB`, method `DEVS`) is present and controls brightness under `Device_ID = 0x00050021`.
- ASUS System Control Interface driver installed.
- Administrator privileges — required both to write through ATKACPI and for the hook to observe key events originating in elevated windows.
- The backlight is driven by a software layer rather than autonomously by the embedded controller (EC).

## 9. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| R-1 | The EC wakes the backlight independently of software | **Critical — invalidates the project** | Verify before implementing: close Armoury Crate and G-Helper, turn the backlight off with Fn+↓, then tap the touchpad. If it lights up anyway, the project is not feasible in this form. |
| R-2 | Conflict with Armoury Crate / G-Helper competing for the same state | High | Require disabling backlight management in those apps; document in README. |
| R-3 | State read-back (`DSTS`) unimplemented or returning garbage | Low | The design does not depend on read-back; state is tracked internally (see FR-1). |
| R-4 | A BIOS/firmware update changes `Device_ID` or the level mapping | Medium | Identifier is configurable (FR-4). |
| R-5 | Antivirus flags the global keyboard hook as a keylogger | Medium | NFR-3, open source, explicit disclosure in README. |
| R-6 | Other ASUS models use a different mapping | Low | Scope limited explicitly (section 4); verified-model list in README. |

## 10. Open questions

1. Does R-1 hold on the target device? **Blocks everything else.**
2. Is 3 s the right default, or does 5 s work better in practice?
3. Should the backlight wake on `WM_KEYDOWN` only, or on key release as well?
4. Should the mechanism be disabled on AC power, where battery saving is irrelevant?
5. Should modifier keys pressed alone (Shift, Ctrl) wake the backlight?

## 11. Out of scope for v1, worth revisiting

- A system tray icon with pause and quick timeout adjustment.
- Power-source-dependent profiles.
- Documenting the hardware modification (series resistor) as a separate chapter, for users for whom 33% is too bright even with working idle-off.
- Replacing the WMI layer with a direct `DeviceIoControl` call on `\\.\ATKACPI`, if the latency targets in section 3 prove unreachable otherwise.
