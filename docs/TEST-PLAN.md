# TEST-PLAN — asus-kbd-backlight

Maps every requirement in [PRD.md](PRD.md) to how it is verified. Maintained by
the `qa` agent. **A** = automated (`tests/`), **M** = manual (needs real
hardware, elevation, a message loop, or the GUI).

## v0.1 — core daemon

| Req | Verifies | Kind | Status |
|---|---|---|---|
| FR-1 | Backlight forced off at startup, independent of prior state | A | ✅ `test_controller.test_startup_forces_off` |
| FR-2 | Keyboard-only wake; mouse/touchpad ignored | A (hook is keyboard-only by construction) + M (real pointer input) | ⚠️ partial — no test asserts pointer events don't advance `last_key` |
| FR-3 | Off after the configured idle interval; steady while typing | A | ✅ `test_controller.test_tick_tracks_idle_timeout` — ⚠️ no "continuous typing = no flicker" case |
| FR-4 | timeout / on_level / device_id configurable outside code | A | ✅ `test_config` (5) |
| FR-5 | No console; documented elevated autostart | M | ⛔ manual only |
| FR-6 | Clean shutdown: hook removed, control returned | A (stop path) + M | ⚠️ partial — `Controller.stop()` not unit-tested |
| NFR-1 | Hook does no blocking work | A (structural) | ⛔ not asserted |
| NFR-2 | Control call only on state change | A | ✅ `test_controller.test_apply_only_on_transition` |
| NFR-3 | Hook records timestamp only, never key content | A (structural) | ⛔ not asserted — **high priority** |
| NFR-4 | Hardware failure: no undefined state, no crash-without-cleanup | A (mock `BacklightError`) | ⛔ not covered |
| — | `DEVS` Control_status = `0x80 \| level` (the enable-bit bug) | A | ⛔ not covered — **high priority**, regression guard |
| — | `--set` one-shot path | A | ⛔ not covered |
| — | `_coerce_device_id` rejects garbage; hex vs int | A | ✅ int/hex ✅ ; ⛔ garbage-raises |

## v0.2 — tray UI (not yet implemented)

| Req | Verifies | Kind | Status |
|---|---|---|---|
| FR-7 | One UAC prompt; decline = clean exit; `-m` self-relaunch once, no loop | A (relaunch guard via `AKB_ELEVATED`) + M (UAC) | ⛔ |
| FR-8 | Tray icon, tooltip state, menu actions, double-click → Settings | M | ⛔ |
| FR-9 | Two-group layout; seconds slider+entry sync & clamp; brightness 3 detents only; version shown | A (slider→on_level map, entry clamp) + M (visual) | ⛔ |
| FR-10 | Icon embedded; running vs paused differ | M | ⛔ |
| FR-11 | Save applies < 1 s, no restart, no dropped keys; hand-edit picked up | A (config mtime watch + swap under lock) | ⛔ |
| FR-12 | `autostart` default true; daemon reconciles Task Scheduler entry; no prompt at logon | A (reconcile logic w/ mocked `Schedule.Service`) + M (actual logon) | ⛔ |
| FR-13 | Night theme by default; no pure #FFF/#000; palette tokens applied | M (visual) | ⛔ |
| NFR-5 | Settings window / config writes never elevate | A (settings entrypoint asserts no elevation call) | ⛔ |
| NFR-6 | One message loop in the daemon; settings out of process | A (structural) + M | ⛔ |
| NFR-7 | Tray lost (Explorer restart) → keeps working, re-adds icon | M | ⛔ |
| NFR-8 | UI adds < 150 ms startup; idle CPU < 0.5% | M (measure) | ⛔ |

## Known gaps to close next (priority order)

1. NFR-3 structural test — hook callback touches only a timestamp.
2. `0x80 | level` regression test (extract `_control_status(level)` as a pure
   function in `backlight.py` if needed — that is a `src/` change, so it is a
   request to the main agent, not the `qa` agent).
3. NFR-4 — `BacklightError` caught, logged once, `_is_on` unchanged, retried.
4. `Controller.stop()` and the `--set` path.
5. `_coerce_device_id` / `Config.validated` rejection cases (device_id ≤ 0, > 0xFFFFFFFF).
6. FR-2 — a fake hook proving pointer events don't move `last_key`.
