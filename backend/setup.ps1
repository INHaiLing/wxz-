param([switch]$SkipAdmin, [string]$PythonVersion = '3.14')
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & py "-$PythonVersion" -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create Python environment.' }
}
$backendPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $backendPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Failed to install backend dependencies.' }
if (-not (Test-Path -LiteralPath '.env')) {
    & $backendPython scripts/initialize_env.py
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create local configuration.' }
}
& $backendPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& $backendPython manage.py seed_demo
if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed.' }
& $backendPython manage.py setup_roles
if ($LASTEXITCODE -ne 0) { throw 'Role initialization failed.' }
& $backendPython manage.py setup_business_roles
if ($LASTEXITCODE -ne 0) { throw 'Business role initialization failed.' }
& $backendPython manage.py seed_product
if ($LASTEXITCODE -ne 0) { throw 'Product initialization failed.' }
if (-not $SkipAdmin) {
    & $backendPython manage.py shell -c "from accounts.models import User; import sys; sys.exit(0 if User.objects.filter(is_superuser=True,is_active=True).exists() else 1)" --verbosity 0
    if ($LASTEXITCODE -eq 1) {
        & $backendPython manage.py createsuperuser
        if ($LASTEXITCODE -ne 0) { throw 'Create an administrator before using the admin site.' }
    } elseif ($LASTEXITCODE -ne 0) { throw 'Failed to check administrator state.' }
}
Write-Host 'Ready. From the project root, run .\backend\start.ps1, then open http://127.0.0.1:8000/admin/'
} finally {
    Pop-Location
}
