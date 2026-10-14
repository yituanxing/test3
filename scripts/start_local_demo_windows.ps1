# ZD Windows localhost launcher. Save as UTF-8 with BOM for Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
try {
    $projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    Set-Location $projectRoot
    if (-not (Test-Path 'app/main.py')) { throw 'app/main.py not found. Extract the complete ZIP first.' }
    $env:ZD_LEGACY_MODE = '0'
    $env:ZD_DATA_DIR = Join-Path $projectRoot 'data/real-only'
    New-Item -Path $env:ZD_DATA_DIR -ItemType Directory -Force | Out-Null

    $py = Join-Path $projectRoot '.venv-win/Scripts/python.exe'
    if (-not (Test-Path $py)) {
        Write-Host 'Setting up a local Windows virtual environment...'
        $started = $false
        foreach ($candidate in @(
            @{exe='py';args=@('-3.11')},
            @{exe='py';args=@('-3')},
            @{exe='python';args=@()}
        )) {
            if (-not (Get-Command $candidate.exe -ErrorAction SilentlyContinue)) { continue }
            & $candidate.exe @($candidate.args) -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"' 2>$null
            if ($LASTEXITCODE -ne 0) { continue }
            & $candidate.exe @($candidate.args) -m venv .venv-win
            if ($LASTEXITCODE -eq 0 -and (Test-Path $py)) { $started=$true; break }
        }
        if (-not $started) {
            throw 'Cannot create .venv-win. Install Python 3.11+ from python.org and ensure the py launcher is available.'
        }
    }
    & $py -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'Installed .venv-win is from unsupported Python; delete .venv-win and retry.' }

    Write-Host 'Installing the local demo dependencies (internet required on first run)...'
    & $py -m pip install --disable-pip-version-check -r requirements.txt -r requirements-business.txt
    if ($LASTEXITCODE -ne 0) { throw 'pip dependency install failed; inspect the full error above.' }

    Write-Host 'Create demo_state password on first run (at least 12 characters).'
    & $py -m scripts.demo_bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'Local database or admin account initialization failed.' }

    Write-Host 'Now start the real API server. Keep this window OPEN.' -ForegroundColor Green
    Write-Host 'Then open http://127.0.0.1:8001/login in your browser.' -ForegroundColor Green
    Write-Host 'User: demo_state; password: the one you created (not a preset).' -ForegroundColor Green
    Write-Host 'The server binds to 127.0.0.1 only.' -ForegroundColor Cyan
    & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8001
    if ($LASTEXITCODE -ne 0) { throw 'Uvicorn ended with an error. Port 8001 may be in use; see above.' }
    exit 0
}
catch {
    Write-Host '================ STARTUP FAILED ================' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host 'Try: cd <extracted-folder>/zd-demo ; .venv-win/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001'
    exit 1
}
