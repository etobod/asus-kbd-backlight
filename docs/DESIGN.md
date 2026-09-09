# DESIGN — asus-kbd-backlight

Implementation notes for the requirements in [PRD.md](PRD.md). Status: draft,
tracks code version 0.1.0.

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
   hook and runs `GetMessage`/`DispatchMessage`. A low-level hook is only
   serviced while its installing thread pumps messages, so installation and the
   pump must share a thread. This thread blocks until `WM_QUIT`.
2. **Hook callback**, invoked by Windows on the pump thread. Does exactly one
   thing: `last_key = time.monotonic()`. No key code is read (NFR-3).
3. **Worker thread** (`Controller._run`). Every 50 ms it computes
   `idle_for = now - last_key` and calls `_apply(on = idle_for < timeout)`.
   `_apply` is a no-op unless the boolean differs from the last applied state,
   so `DEVS` is called only on transitions (NFR-2).

## Module map

| Module | Responsibility |
|---|---|
| `hook.py` | `KeyboardIdleHook` (ctypes `WH_KEYBOARD_LL`), `pump_messages` |
| `backlight.py` | `WmiBacklight` (real), `NullBacklight` (dry-run / non-Windows), `get_backlight` factory |
| `config.py` | `Config` dataclass, TOML load from `%APPDATA%\asus-kbd-backlight\config.toml`, validation |
| `app.py` | `Controller` state machine + CLI (`main`) |
| `__main__.py` | `python -m asus_kbd_backlight` entry point |

## Key decisions

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
