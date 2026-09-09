# Contributing

## Dev setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Checks

```powershell
ruff check .
ruff format --check .
pytest -q
```

`pytest` runs without hardware: the WMI path is replaced by `NullBacklight`
(`--dry-run` / non-Windows), and the ctypes hook is not unit-tested.

## Trying it on real hardware

Run the EC check in the README first. Then:

```powershell
# from an elevated shell
python -m asus_kbd_backlight --dry-run   # logic only, backlight untouched
python -m asus_kbd_backlight             # live
```

## Reporting a model

Open an issue using the **Model report** template, or a PR adding a row to the
verified-models table in `README.md`. Negative results (EC wakes the backlight,
different `device_id`, different level mapping) are wanted too.

## Privacy-sensitive code

`src/asus_kbd_backlight/hook.py` installs a global keyboard hook. Any change to
the hook callback must keep it to "timestamp only, no key data" (PRD NFR-3) and
must be called out explicitly in the PR description.
