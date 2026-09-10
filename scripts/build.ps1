# Build standalone Windows executables with PyInstaller. Output (not committed):
#   .\asus-kbd-backlight.exe        one file, NO console window - for autostart
#   .\asus-kbd-backlight-debug.exe  one file, WITH console + live log - for testing
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

$common = @(
    "--onefile", "--clean", "--noconfirm",
    "--paths", "src",
    "--distpath", "build\dist", "--workpath", "build\work", "--specpath", "build",
    # lazily imported in backlight.py, so PyInstaller cannot see them by itself
    "--hidden-import", "win32com.client",
    "--hidden-import", "pythoncom",
    "--hidden-import", "pywintypes"
)

& $py -m PyInstaller @common --noconsole --name asus-kbd-backlight `
    src\asus_kbd_backlight\__main__.py
Copy-Item build\dist\asus-kbd-backlight.exe .\asus-kbd-backlight.exe -Force

& $py -m PyInstaller @common --console --name asus-kbd-backlight-debug `
    src\asus_kbd_backlight\__main__.py
Copy-Item build\dist\asus-kbd-backlight-debug.exe .\asus-kbd-backlight-debug.exe -Force

Write-Host "Built .\asus-kbd-backlight.exe and .\asus-kbd-backlight-debug.exe" -ForegroundColor Green
