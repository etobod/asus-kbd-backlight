# TEST-PLAN — asus-kbd-backlight

Maps every requirement in [PRD.md](PRD.md) to how it is verified. Maintained by
the `qa` agent. **A** = automated (`tests/`), **M** = manual (needs real
hardware, elevation, a message loop, or the GUI).

## v0.1 — core daemon

| Req | Verifies | Kind | Status |
|---|---|---|---|
| FR-1 | Backlight forced off at startup, independent of prior state | A | ✅ `test_controller.test_startup_forces_off` |
| FR-2 | Keyboard-only wake; mouse/touchpad ignored | A (hook is keyboard-only by construction) + M (real pointer input) | ⚠️ partial — `test_hook` proves the callback is keyboard-only; no test injects pointer events |
| FR-3 | Off after the configured idle interval; steady while typing | A | ✅ `test_controller.test_tick_tracks_idle_timeout` — ⚠️ no "continuous typing = no flicker" case |
| FR-4 | timeout / on_level / device_id configurable outside code | A | ✅ `test_config` (11) |
| FR-5 | No console; documented elevated autostart | M | ⛔ manual only |
| FR-6 | Clean shutdown: hook removed, control returned | A + M | ✅ `test_run_turns_backlight_off_on_exit`, `test_stop_warns_when_worker_hangs` |
| NFR-1 | Hook does no blocking work | A (structural) | ⚠️ implied by `test_hook` (callback is trivial); not directly asserted |
| NFR-2 | Control call only on state change | A | ✅ `test_controller.test_apply_only_on_transition` |
| NFR-3 | Hook records timestamp only, never key content | A (structural) | ✅ `test_hook.test_callback_records_timestamp_only` |
| NFR-4 | Hardware failure: no undefined state, no crash-without-cleanup | A | ✅ `test_apply_swallows_non_backlighterror`, `test_apply_retry_dedup_and_recovery`, `test_apply_backs_off_on_persistent_failure`, `test_backlight.test_wmibacklight_wraps_com_failure_and_leaves_cache_empty` |
| — | `DEVS` Control_status = `0x80 \| level` (the enable-bit bug) | A | ✅ `test_backlight.test_encode_control_status_sets_bit7` (0→0x80 … 3→0x83) |
| — | `--set` one-shot path | A | ⛔ not covered (`_bind` now wraps errors, so no raw traceback) |
| — | `_coerce_device_id` hex / int / bare-hex / garbage | A | ✅ `test_config` — int, `0x`-hex, bare `"00050021"`, garbage→ValueError |

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

1. FR-2 — inject a real pointer event and assert `last_key` doesn't move (needs
   `SendInput` mouse events on the message-pump thread; likely M, not A).
2. FR-3 — "continuous typing keeps it steady, no flicker" (drive `tick()` with a
   sliding `last_key` and assert no off→on churn).
3. `Config.validated` rejection cases (`device_id` ≤ 0 and > `0xFFFFFFFF`).
4. `--set` one-shot path (`main(["--set", "2", "--dry-run"])` returns 0, sets level).
5. v0.2 UI targets — all still ⛔ (feature not built).

Closed 2026-09-10 (main agent, from the `/loc-review` findings): NFR-3
structural, `0x80|level` regression (`encode_control_status`), NFR-4
catch-all + retry-dedup + backoff, FR-6 `stop()`/`_run` off-on-exit,
`_coerce_device_id` bare-hex + garbage, config unknown-key warning.
