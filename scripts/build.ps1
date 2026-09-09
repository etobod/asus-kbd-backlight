# Build standalone Windows executables with PyInstaller. Output (not committed):
#   .\asus-kbd-backlight.exe        one file, NO console window - for autostart
#   .\asus-kbd-backlight-debug.exe  one file, WITH console + live log - for testing
#
#   py -m pip install pyinstaller
#   .\scripts\build.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# Resolve a Python interpreter without assuming it is on PATH.
$py = $null
foreach ($c in "python", "python3") {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py -and (Get-Command py -ErrorAction SilentlyContinue)) { $py = "py" }
if (-not $py) {
    $guess = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    if (Test-Path $guess) { $py = $guess }
}
if (-not $py) { throw "No Python interpreter found (tried python, python3, py, and the default install path)." }
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
