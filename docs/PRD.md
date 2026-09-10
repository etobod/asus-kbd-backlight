# PRD — asus-kbd-backlight

**Status:** draft
**Version:** 0.2
**Date:** 2026-09-10

**Changelog**

- 0.1 — core daemon: keyboard-only idle hook, WMI backlight control, file config. Shipped and verified on TUF Gaming A14 (FA401).
- 0.2 — adds a tray UI: automatic elevation, tray icon, settings window, live-applied config (FR-7 … FR-11, sections 12–13).

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
- A GUI beyond a tray icon and a two-field settings window (sections 12–13). No dashboard, no live graphs, no per-app profiles. The config file stays the source of truth; the settings window is a thin editor over it.

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

---

*The following are v0.2 (tray UI). FR-1 … FR-6 continue to hold unchanged.*

### FR-7 — Elevate automatically
As a user, I want the program to request administrator rights by itself when I start it, so that I never have to remember "Run as administrator".

*Acceptance:* double-clicking the executable raises exactly one UAC prompt. If granted, the program runs fully. If declined, it exits immediately with a visible message and does **not** run in a half-working state. Launching `python -m asus_kbd_backlight` from an unelevated shell re-launches itself elevated once (no prompt loop). The settings window (FR-9) does not raise a second prompt.

### FR-8 — Run in the system tray
As a user, I want the program to sit in the notification area with no window, so that it stays running and out of the way.

*Acceptance:* after start there is no taskbar button and no console — only a tray icon. Hovering shows a tooltip with the app name and current state (running / paused, and the active timeout). Right-click opens a menu: **Settings**, **Pause** / **Resume**, **Quit**. Double-click (or left-click) opens Settings. **Quit** performs the FR-6 clean shutdown.

### FR-9 — Settings window
As a user, I want a small settings window reachable from the tray, so that I can change the behaviour without opening a config file.

*Acceptance:* the window has exactly two controls — "Keep the backlight on for **[N]** seconds after the last keystroke" (integer, 1–60) and "Brightness while typing" (**33% / 66% / 100%**). **Save** writes the config and applies it live (FR-11); **Cancel** or closing the window discards changes and returns to the tray without quitting the daemon. Out-of-range input is rejected inline with a message; Save stays disabled until the input is valid. Opening Settings twice focuses the existing window rather than opening a second.

### FR-10 — Application icon
As a user, I want the program to have its own recognisable icon, so that I can spot it in the tray, Task Manager and Task Scheduler.

*Acceptance:* a multi-resolution `.ico` (16/24/32/48/256 px) is embedded in the executable and used for the tray icon and the settings-window title bar. The tray icon visibly differs between **running** and **paused**.

### FR-11 — Live configuration
As a user, I want a changed setting to take effect straight away, so that I can tune the timeout and feel the result without restarting anything.

*Acceptance:* saving in the settings window changes the running behaviour within 1 s, with no restart, no visible flicker and no dropped keystrokes. The config file remains the single source of truth; editing it by hand while the daemon runs is picked up the same way.

## 7. Non-functional requirements

- **NFR-1** — The keyboard hook performs no blocking work. Backlight control happens off the hook thread.
- **NFR-2** — A control call is issued only on state change, never on a polling cadence. *(v0.2: unchanged for the backlight; a 1 s config-file stat is allowed and is not a control call.)*
- **NFR-3** — The program does not log keystroke content. Only an event timestamp is recorded. This is critical: the tool installs a global keyboard hook and must be auditable on this point.
- **NFR-4** — A hardware communication failure must not leave the backlight in an undefined state, nor crash the process without unregistering the hook.
- **NFR-5** — The settings window and all config writes run **without** elevation. Only backlight control and the keyboard hook need admin rights.
- **NFR-6** — The tray icon shares the single Win32 message loop that already services the keyboard hook. No second event loop runs in the daemon process; the settings window is a separate, transient process.
- **NFR-7** — If the tray icon cannot be created or is lost (Explorer restart), the daemon keeps working headless and re-adds the icon when possible.
- **NFR-8** — The UI adds < 150 ms to startup and leaves idle CPU under 0.5%.

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
| R-7 | A GUI toolkit bloats or breaks the PyInstaller bundle | Medium | Use stdlib `tkinter`; keep the settings window a separate, minimal process; the daemon itself pulls in no GUI toolkit (tray is raw Win32 via pywin32, already a dependency). |
| R-8 | `requireAdministrator` manifest blocks unelevated autostart (Startup folder, `HKCU\Run`) | Medium | Task Scheduler with *Run with highest privileges* is the supported autostart and already matches FR-5; document it, ship a helper to create the task. |
| R-9 | A second event loop starves the low-level hook and trips `LowLevelHooksTimeout`, dropping keystrokes | High | One message loop for hook + tray (NFR-6); the settings window runs out of process. |

## 10. Open questions

1. ~~Does R-1 hold on the target device?~~ **Resolved:** on TUF A14 (FA401) the backlight holds once `DEVS` sets bit 7 and Armoury Crate's idle timer is off. No EC override observed.
2. Is 3 s the right default, or does 5 s work better in practice?
3. Should the backlight wake on `WM_KEYDOWN` only, or on key release as well?
4. Should the mechanism be disabled on AC power, where battery saving is irrelevant?
5. Should modifier keys pressed alone (Shift, Ctrl) wake the backlight?
6. Should **Pause** (FR-8) persist across restarts, or always start running?
7. Should the settings window expose `device_id` under an "Advanced" toggle, or keep it file-only?
8. Should there be a "Start with Windows" checkbox that writes the Task Scheduler entry, or is that left to a documented manual step / helper script?
9. Two icon files (running / paused) or one icon with a drawn overlay?

## 11. Out of scope for v0.2, worth revisiting

- Quick timeout presets in the tray menu (e.g. 3 s / 5 s / 10 s) without opening Settings.
- Power-source-dependent profiles.
- An installer / MSI and auto-update.
- Localisation of the settings window.
- Documenting the hardware modification (series resistor) as a separate chapter, for users for whom 33% is too bright even with working idle-off.
- Replacing the WMI layer with a direct `DeviceIoControl` call on `\\.\ATKACPI`, if the latency targets in section 3 prove unreachable otherwise.

## 12. v0.2 UI — user stories

Format: *As a … I want … so that …*, with the acceptance criteria on the matching FR.

**Elevation (FR-7)**

- As a night-shift laptop user, I want to start the app by double-clicking it and just clicking "Yes" once, so that I don't have to right-click → Run as administrator every time.
- As someone who declined the UAC prompt by reflex, I want the app to tell me it can't run without admin and close cleanly, so that I'm not left with a silently broken tray icon.
- As a developer, I want `python -m asus_kbd_backlight` to relaunch itself elevated, so that I can iterate without keeping an admin terminal open.

**Tray (FR-8, FR-10)**

- As a user who keeps many things running, I want the app to be a single tray icon with no taskbar clutter, so that it's present but invisible until I need it.
- As a user, I want to hover the icon and see whether it's running or paused and what the current timeout is, so that I can confirm it's doing its job without opening anything.
- As a user giving a presentation, I want a one-click **Pause** from the tray so the keyboard stays lit (or stays off) predictably, and **Resume** to go back to normal.
- As a user, I want **Quit** from the tray to also hand keyboard control back to Windows, so that stopping the app never leaves my backlight stuck.
- As a user scanning Task Manager or Task Scheduler, I want the app to have its own icon and name, so that I can recognise and trust the entry.

**Settings (FR-9, FR-11)**

- As a user who finds 3 s too short, I want to open Settings from the tray, type `6`, and click Save, so that the backlight now stays on 6 s after I stop typing — immediately, without restarting.
- As a user in a very dark room, I want to pick 33% in Settings; as a user in a dim room, 66% or 100% — from a plain dropdown, not a hex value.
- As a user who fat-fingered `600` into the seconds box, I want the window to stop me with a clear message instead of saving a useless value.
- As a user, I want closing the settings window to leave the app running in the tray, so that I don't accidentally kill it by clicking the X.
- As a user who prefers editing text files, I want hand-edits to `config.toml` to be picked up while the app runs, so that the GUI and the file never disagree.

## 13. v0.2 UI — implementation plan

Two processes:

- **Daemon** (elevated, long-lived): the existing `Controller` + keyboard hook, plus a hidden tray window, all on **one** Win32 message loop (`hook.pump_messages`). The worker thread gains a 1 s config-file `stat` check for live reload.
- **Settings dialog** (unelevated, transient): a small `tkinter` window launched as a child process; writes `config.toml` and exits.

### FR-7 — elevation

- Built exe: PyInstaller `--uac-admin` embeds a `requestedExecutionLevel level="requireAdministrator"` manifest. Windows shows the prompt at launch; no in-process code.
- `python -m` / unfrozen: in `main()`, if `not IsUserAnAdmin()` and the command is not `--settings` and `AKB_ELEVATED` is unset — `ShellExecuteW(None, "runas", sys.executable, "<argv>", None, SW_SHOWNORMAL)`, set `AKB_ELEVATED=1` in the child's environment to break any loop, then exit. On `ShellExecuteW` returning `SE_ERR_CANCELLED` (user said No), print a one-line message and exit non-zero.
- The settings child is always spawned with `--settings --no-elevate`.

### FR-8 / FR-10 — tray

- pywin32 `win32gui`: register a window class, `CreateWindow` a hidden (message-only) window, `Shell_NotifyIcon(NIM_ADD, …)` with `uCallbackMessage = WM_APP + 1` and the loaded icon.
- The daemon's `WndProc` handles: `WM_APP+1` → on `WM_RBUTTONUP` build a `CreatePopupMenu` (Settings / Pause‑Resume / Quit) and `TrackPopupMenu`; on `WM_LBUTTONDBLCLK` → spawn the settings child. `WM_COMMAND` → dispatch the menu item. The registered `TaskbarCreated` message → re-add the icon (NFR-7). `WM_QUERYENDSESSION` / `WM_CLOSE` → `Controller.stop()` then `PostQuitMessage`.
- Icon asset: `assets/icon.ico` (active) and `assets/icon-paused.ico`. Bundled with `--add-data "assets;assets"`; at runtime resolve via `sys._MEIPASS` when frozen, else the repo path. Load with `win32gui.LoadImage(0, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)`. Swapping the icon on Pause/Resume is `Shell_NotifyIcon(NIM_MODIFY, …)`.
- `Controller` gains `pause()` (force off, stop asserting on) / `resume()` and a `status` string for the tooltip (`NIM_MODIFY` with a new `szTip`).

### FR-9 / FR-11 — settings + live apply

- Tray "Settings" → `subprocess.Popen([exe_or_python, "--settings", "--no-elevate"])`. A named mutex (`AKB_SETTINGS_SINGLETON`) makes a second launch focus the first window instead of opening another.
- Dialog (`app_settings.py`, ~120 lines, `tkinter`): `config.load()` → a `Spinbox` (1–60) bound to `timeout` and a `ttk.Combobox` of `33% / 66% / 100%` mapped to `on_level` 1/2/3. Validation on `<KeyRelease>`; Save disabled while invalid. Save → `config.save(Config(timeout, on_level, device_id))` → close. Cancel / window‑close → exit with no write.
- `config.save(cfg, path=None)`: new function. Serialise the three keys (via `tomli-w`, new dep, or a 6-line hand-writer), write to `config.toml.tmp`, `os.replace` onto `config.toml` (atomic).
- Live reload: the worker loop already ticks every 50 ms; every ~1 s it also compares `config.toml`'s `st_mtime`/`st_size` to the last seen values and, on change, `config.load()` + swaps `Controller._cfg` under a `Lock`. No hook or COM churn (FR-11, NFR-6). A malformed file on reload is logged and the previous config kept.

### Packaging

- `pyproject.toml`: add `tomli-w` (config write). `tkinter` is stdlib. `pywin32` already present.
- `scripts/build.ps1`: add `--icon assets/icon.ico`, `--uac-admin`, `--add-data "assets;assets"`. The `noconsole` build becomes the single user artifact; `--debug` / `--settings` are flags on it. Keep a `--console` debug build without `--uac-admin` for troubleshooting.
- New: `scripts/make-icon.ps1` (or a committed `assets/icon.ico`) — generate the multi-res `.ico` from `assets/icon.svg` (ImageMagick / Inkscape). Design brief: a single backlit keycap with a soft glow; the paused variant desaturated.
- Optional `scripts/install-task.ps1`: registers the Task Scheduler entry (*Run with highest privileges*, at logon) — the supported autostart (R-8).

### Rollout order

1. `assets/icon.ico` + `--icon` + `--uac-admin` — "double-click just works" (FR-7, FR-10 partial).
2. Tray window + menu + Pause / Resume / Quit on the existing loop (FR-8).
3. `config.save` + `--settings` dialog + file-watch live reload (FR-9, FR-11).
4. Paused-state icon + live tooltip, `TaskbarCreated` recovery (FR-10 complete, NFR-7).
