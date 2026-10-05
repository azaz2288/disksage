param([string]$Python='python')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & $Python -m pip install pyinstaller==6.17.0
    if ($LASTEXITCODE -ne 0) {throw 'PyInstaller installation failed'}
    & $Python -m PyInstaller --noconfirm --windowed --name DiskSage --paths $projectRoot --hidden-import app.main --collect-all uvicorn --collect-all fastapi --add-data "$projectRoot/app/web;app/web" app/desktop.py
    if ($LASTEXITCODE -ne 0) {throw 'Desktop build failed'}
    Write-Output 'Unsigned portable build: dist/DiskSage/DiskSage.exe'
} finally { Pop-Location }
