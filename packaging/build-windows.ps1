# Build a Windows GUI executable (dist\omshare\omshare.exe) with PyInstaller.
#
# RUN THIS ON WINDOWS (PowerShell):
#   cd omshare
#   .\packaging\build-windows.ps1
#   .\dist\omshare\omshare.exe
#
# Optional: place an icon at packaging\omshare.ico to brand the app.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$venv = ".build-venv"

Write-Host "==> Creating build venv ($venv)"
& $py -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$venv\Scripts\pip.exe" install --quiet ".[gui]" pyinstaller

$iconArgs = @()
if (Test-Path "packaging\omshare.ico") { $iconArgs = @("--icon", "packaging\omshare.ico") }

Write-Host "==> Building dist\omshare\omshare.exe"
& "$venv\Scripts\pyinstaller.exe" --noconfirm --clean --windowed `
  --name omshare @iconArgs `
  packaging\omshare_gui.py

Write-Host ""
Write-Host "Done. Run: .\dist\omshare\omshare.exe"
