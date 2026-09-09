# asus-kbd-backlight

Keyboard-only idle timeout for the keyboard backlight on ASUS laptops.

The backlight turns on when you type and turns off a few seconds after you stop. Moving the mouse or touching the touchpad does **not** wake it.

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

Run with `pythonw.exe` to avoid a console window. For autostart, add a task in Task Scheduler with *Run with highest privileges* checked.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `timeout` | `3.0` | Seconds of keyboard idleness before turning off |
| `on_level` | `1` | Brightness while typing: 1 = 33%, 2 = 66%, 3 = 100% |
| `device_id` | `0x00050021` | ASUS ACPI endpoint for backlight brightness |

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
