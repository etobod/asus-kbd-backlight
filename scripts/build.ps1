# Build standalone Windows executables with PyInstaller. Output (not committed):
#   .\asus-kbd-backlight.exe        one file, NO console, self-elevating - the user artifact
#   .\asus-kbd-backlight-debug.exe  one file, WITH console + live log - for testing
#
# Only the user artifact carries --uac-admin (FR-7). The debug build stays
# manifest-free so it can be started unelevated and re-launch itself through
# ShellExecuteW, which is the code path a source checkout takes.
#
#   py -m pip install pyinstaller
#   .\scripts\build.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# Resolve a Python interpreter without assuming it is on PATH, and skip the
# Microsoft Store "App execution alias" stub under WindowsApps (it prints
# "Python was not found" and exits 0, which would make every build a silent no-op).
function Test-RealPython($exe) {
    if (-not $exe) { return $false }
    try { & $exe -c "import sys" 2>$null } catch { return $false }
    return ($LASTEXITCODE -eq 0)
}

$py = $null
$candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
)
foreach ($name in "py", "python3", "python") {
    $src = (Get-Command $name -ErrorAction SilentlyContinue).Source
    if ($src -and $src -notmatch '\\WindowsApps\\') { $candidates += $src }
}
foreach ($c in $candidates) {
    if ((Test-Path $c) -and (Test-RealPython $c)) { $py = $c; break }
}
if (-not $py) { throw "No usable Python interpreter found (Store alias stubs are ignored)." }
Write-Host "Using interpreter: $py"

$root = (Get-Location).Path

if (-not ((Test-Path "assets\icon.ico") -and (Test-Path "assets\icon-paused.ico"))) {
    & $py scripts\make-icon.py
    if ($LASTEXITCODE -ne 0) { throw "make-icon.py failed" }
}

# --add-data / --icon sources are resolved relative to the .spec file, which
# --specpath puts under build\; give them absolute paths so they still point at
# the repo's assets\.
$common = @(
    "--onefile", "--clean", "--noconfirm",
    "--paths", "src",
    "--distpath", "build\dist", "--workpath", "build\work", "--specpath", "build",
    # tray + settings-window icons (FR-10), resolved at runtime via sys._MEIPASS
    "--icon", "$root\assets\icon.ico",
    "--add-data", "$root\assets;assets",
    # lazily imported (backlight.py / autostart.py), so PyInstaller can miss them
    "--hidden-import", "win32com.client",
    "--hidden-import", "pythoncom",
    "--hidden-import", "pywintypes",
    # the settings window imports tkinter inside a method; pull in the toolkit
    # (and its Tcl/Tk data via PyInstaller's tkinter hook) explicitly
    "--hidden-import", "tkinter",
    # first-party modules reached only through function-level imports in app.py
    "--hidden-import", "asus_kbd_backlight.app_settings",
    "--hidden-import", "asus_kbd_backlight.autostart",
    "--hidden-import", "asus_kbd_backlight.tray",
    "--hidden-import", "asus_kbd_backlight.elevate"
)

& $py -m PyInstaller @common --noconsole --uac-admin --name asus-kbd-backlight `
    src\asus_kbd_backlight\__main__.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for asus-kbd-backlight" }
Copy-Item build\dist\asus-kbd-backlight.exe .\asus-kbd-backlight.exe -Force

& $py -m PyInstaller @common --console --name asus-kbd-backlight-debug `
    src\asus_kbd_backlight\__main__.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for asus-kbd-backlight-debug" }
Copy-Item build\dist\asus-kbd-backlight-debug.exe .\asus-kbd-backlight-debug.exe -Force

Write-Host "Built .\asus-kbd-backlight.exe and .\asus-kbd-backlight-debug.exe" -ForegroundColor Green
