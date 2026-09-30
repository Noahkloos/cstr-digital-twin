# Eenmalige installatie + starten van de CSTR Digital Twin (Windows PowerShell).
#   .\start.ps1
# Als PowerShell scripts blokkeert:  powershell -ExecutionPolicy Bypass -File .\start.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Virtuele omgeving aanmaken..."
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path "static\vendor\chart.umd.js")) {
    Write-Host "Frontend-bibliotheken downloaden (voor offline gebruik)..."
    try { .\.venv\Scripts\python.exe scripts\download_vendor.py } catch { Write-Host "Mislukt - de CDN wordt gebruikt." }
}
Write-Host "Dashboard: http://127.0.0.1:8000   API-docs: http://127.0.0.1:8000/docs"
.\.venv\Scripts\python.exe run.py
