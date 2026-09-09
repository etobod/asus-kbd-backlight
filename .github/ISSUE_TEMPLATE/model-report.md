---
name: Model report
about: Report whether this tool works on your ASUS model (positive or negative)
title: "[model] <exact model string>"
labels: model-report
---

**Exact model string** (`wmic csproduct get name`, or Settings → System → About):

**BIOS version:**

**"Turn off backlight after …" disabled in Armoury Crate?** yes / no

**Behaviour:**
- [ ] `--set 3` reaches level 3 and the backlight stays on
- [ ] backlight lights on typing and turns off after the timeout
- [ ] backlight briefly lights then drops / won't hold — does not work here

**If it works — what values:**
- `device_id`:
- level mapping (0–3):

**Notes:**
