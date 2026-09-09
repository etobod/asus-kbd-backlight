# Build standalone Windows executables with PyInstaller. Output (not committed):
#   .\asus-kbd-backlight.exe        one file, NO console window - for autostart
#   .\asus-kbd-backlight-debug.exe  one file, WITH console + live log - for testing
#
#   py -m pip install pyinstaller
#   .\scripts\build.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$common = @(
    "--onefile", "--clean", "--noconfirm",
    "--paths", "src",
    "--distpath", "build\dist", "--workpath", "build\work", "--specpath", "build",
    # lazily imported in backlight.py, so PyInstaller cannot see them by itself
    "--hidden-import", "win32com.client",
    "--hidden-import", "pythoncom",
    "--hidden-import", "pywintypes"
)

python -m PyInstaller @common --noconsole --name asus-kbd-backlight `
    src\asus_kbd_backlight\__main__.py
Copy-Item build\dist\asus-kbd-backlight.exe .\asus-kbd-backlight.exe -Force

python -m PyInstaller @common --console --name asus-kbd-backlight-debug `
    src\asus_kbd_backlight\__main__.py
Copy-Item build\dist\asus-kbd-backlight-debug.exe .\asus-kbd-backlight-debug.exe -Force

Write-Host "Built .\asus-kbd-backlight.exe and .\asus-kbd-backlight-debug.exe" -ForegroundColor Green
