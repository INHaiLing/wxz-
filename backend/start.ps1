param([ValidateRange(1024, 65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
$backendPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $backendPython) -or -not (Test-Path -LiteralPath '.env')) {
    throw 'Run .\setup.ps1 first.'
}
& $backendPython manage.py runserver "127.0.0.1:$Port"
if ($LASTEXITCODE -ne 0) { throw 'Backend server exited with an error.' }
} finally {
    Pop-Location
}
