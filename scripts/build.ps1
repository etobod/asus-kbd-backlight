# Build a standalone Windows executable with PyInstaller.
# Output: .\asus-kbd-backlight.exe (one file, no console window). Not committed.
#
#   py -m pip install pyinstaller
#   .\scripts\build.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m PyInstaller --onefile --noconsole --clean --noconfirm `
    --name asus-kbd-backlight `
    --paths src `
    --distpath build\dist --workpath build\work --specpath build `
    src\asus_kbd_backlight\__main__.py

Copy-Item build\dist\asus-kbd-backlight.exe .\asus-kbd-backlight.exe -Force
Write-Host "Built .\asus-kbd-backlight.exe" -ForegroundColor Green
