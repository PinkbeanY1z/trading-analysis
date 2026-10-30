param([switch]$Mock)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if ($Mock) { $env:MARKET_PROVIDER = 'mock'; $env:AGENT_MODE = 'mock' } else { $env:MARKET_PROVIDER = 'twelve_data'; $env:AGENT_MODE = 'deepseek' }
& ./.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 3000
