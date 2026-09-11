# asus-kbd-backlight

Keyboard-only idle timeout for the keyboard backlight on ASUS laptops.

The backlight turns on when you type and turns off a few seconds after you stop. Moving the mouse or touching the touchpad does **not** wake it.

- Runs in the **system tray** — right-click for Settings / Pause / Quit; the tooltip shows the current timeout.
- **Elevates itself** on start (one UAC prompt); declining exits cleanly.
- A dark **settings window** for the timeout and brightness, applied live — no restart, and hand-edits to `config.toml` are picked up the same way.
- **Starts with Windows** by default, via a Task Scheduler entry it manages itself (no logon UAC prompt).

---

> # ⚠️ REQUIRED: TURN OFF "TURN OFF BACKLIGHT AFTER …" IN ARMOURY CRATE
>
> **Armoury Crate → Lighting → Advanced Settings → disable "Turn off backlight after (Battery / AC)".**
>
> While that option is on, Armoury Crate runs its own idle timer and **re‑lights the keyboard on any input, including mouse and touchpad**, the moment its countdown expires — it will fight this tool and you will get flicker or a keyboard that lights up when you move the mouse.
>
> With it **off**, Armoury Crate stops managing the backlight entirely and this tool has sole control. Do the same in G‑Helper if you use it, or just close both.

---

## Why this exists

ASUS laptops expose only four backlight steps: 0% / 33% / 66% / 100%. The 33% minimum is a firmware default and, per ASUS support, cannot be lowered further in static mode. In a dark room it is too bright.

The idle timeouts built into Armoury Crate and G-Helper do not help, because they measure idleness globally: any input event wakes the backlight, including a touchpad tap. So the keyboard stays lit while you are only reading.

This tool measures idleness of the **keyboard alone**, using a `WH_KEYBOARD_LL` hook — a keyboard-only hook that pointer devices do not pass through.

This tool does **not** and cannot make the backlight dimmer than 33%. That limit is in firmware. If 33% is still too bright for you even with idle-off working, see [Going below 33%](#going-below-33) below.

## Privacy: this installs a global keyboard hook

Read this before running anything.

To know when you last typed, the program installs a system-wide low-level keyboard hook. That is the same Windows mechanism a keylogger uses, so you should not take anyone's word for what it does with it — including mine.

What the hook callback does, in full:

```python
def _proc(nCode, wParam, lParam):
    global last_key
    if nCode == 0:
        last_key = time.monotonic()          # timestamp only
    return user32.CallNextHookEx(None, nCode, wParam, lParam)
```

It assigns a timestamp and passes the event on. It does not read the virtual key code, does not accumulate anything, does not write to disk, and the program opens no network connections at all. There is no telemetry, no update check, no crash reporting.

Verify it yourself: the callback is the only place `lParam` is touched, in [`src/asus_kbd_backlight/hook.py`](src/asus_kbd_backlight/hook.py). It is about fifteen lines. Please read them.

Your antivirus may still flag the hook. That is a reasonable heuristic doing its job, not a false alarm to wave away — decide for yourself whether you trust the source you just read.

## Requirements

- Windows 10 or 11
- Python 3.10+
- ASUS System Control Interface driver installed
- **Administrator privileges.** Required twice over: writing through ATKACPI needs elevation, and an unelevated hook cannot see keys typed into elevated windows, so the backlight would stay dark while you work in an admin console.
- **"Turn off backlight after …" disabled in Armoury Crate** (Lighting → Advanced Settings), and any equivalent in G-Helper. See the callout at the top — this is not optional. While Armoury Crate's idle timer is on, it re-lights the keyboard on mouse/touchpad input and fights this tool for the same state.

## Install

```bash
pip install asus-kbd-backlight
```

The program needs Administrator rights and asks for them itself: start it normally and answer the single UAC prompt. `asus-kbd-backlight.exe` and `python -m asus_kbd_backlight` both start unelevated and re-launch themselves elevated once — so the settings window, which runs from the same program, can stay unelevated and opens with no prompt. Declining the prompt exits cleanly rather than leaving a half-working hook.

`--dry-run` runs the logic against a no-op backlight and never prompts.
`--set LEVEL` (set the backlight once and exit — for probing `device_id`)
needs an elevated prompt; without one it exits 1 with a message. Like the other
one-shot flags, run it from `asus-kbd-backlight-debug.exe` or
`python -m asus_kbd_backlight` to see its output — the windowed exe has no
console.

### Start with Windows

On by default. The app keeps a Task Scheduler entry (`asus-kbd-backlight`, *Run
with highest privileges*, *At log on*) that starts it elevated at logon with no
UAC prompt, and the elevated daemon creates or removes that entry to match the
**Start with Windows** checkbox in the settings window (config key `autostart`).
Untick it and Save, or run `asus-kbd-backlight --uninstall-task` from an
elevated prompt (*Run as administrator*), to stop it launching at logon;
`--install-task` puts it back. Both exit non-zero if they fail — use
`asus-kbd-backlight-debug.exe` or `python -m asus_kbd_backlight` to see the
message, since the windowed exe has no console. You never need to touch Task
Scheduler by hand. (Running with an explicit `--config` does not manage the
task — the scheduled entry always uses the default config location.)

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `timeout` | `3.0` | Seconds of keyboard idleness before turning off |
| `on_level` | `1` | Brightness while typing: 1 = 33%, 2 = 66%, 3 = 100% |
| `autostart` | `true` | Start elevated at logon via a Task Scheduler entry the app manages |
| `device_id` | `0x00050021` | ASUS ACPI endpoint for backlight brightness |

Edits are applied live — save the file (or use the settings window) and the
running daemon picks the change up within about a second, no restart.

### Settings window

Right-click the tray icon → **Settings** (or run `asus-kbd-backlight --settings`)
for a small dark window: a "stay on after typing" slider (1–60 s, with a
typeable box beside it), a brightness slider with three stops (33 / 66 / 100 %),
a **Start with Windows** checkbox, and the version. **Save** writes
`config.toml` and the change takes effect within a second; **Cancel** or closing
the window leaves everything running. `device_id` has no control here — it is a
hardware value, edited in the file on the rare occasion it is needed.

The window runs **without** Administrator rights even though the daemon is
elevated, so the config file keeps a normal owner. Opening Settings a second
time just focuses the window already open.

## Verified models

The ACPI endpoint and level mapping are not guaranteed to be identical across the ASUS range. This table lists what is actually known to work.

| Model | Device ID | Levels | Status | Reported by |
|---|---|---|---|---|
| TUF Gaming A14 (FA401) | `0x00050021` | 0–3 | Working | maintainer |

**Please add yours.** If you get this running on a model that is not listed, open a PR adding a row, or an issue with:

- exact model string (`wmic csproduct get name`, or Settings → System → About)
- BIOS version
- the `device_id` and level mapping that worked

Negative results are just as useful as positive ones. A row saying *"Zenbook S16 — firmware re-lights the backlight, does not work"* saves the next person an evening.

### Known to have the underlying brightness problem, not yet verified with this tool

Reported by users in ASUS and G-Helper forum threads: Zenbook S14, Zenbook S16 (2024), ProArt P16 (2024), some ROG Strix models. These are listed as leads for testing, not as supported hardware.

## Going below 33%

Out of scope for this project, but since people arrive here looking for it: the only known way is a hardware modification — a resistor in series on the keyboard backlight FFC cable. A 22 Ω resistor takes measured current on a Zenbook S16 from 104/170/259 mA down to 28/45/68 mA across the three levels; 33 Ω or 47 Ω goes darker still.

Documented, with photos and a parts list, in [g-helper discussion #3731](https://github.com/seerge/g-helper/discussions/3731). It voids your warranty. It is not my mod and I have not tried it.

## Documents

- [PRD](docs/PRD.md) — requirements, non-goals, risks
- [DESIGN](docs/DESIGN.md) — implementation notes

## License

MIT
