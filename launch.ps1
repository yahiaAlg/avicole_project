# launch.ps1 — Local launcher for the Django app (Windows / PowerShell)
# Run this AFTER build.sh has installed dependencies, migrated, and seeded the DB.
#
# Usage:
#   .\launch.ps1
#   .\launch.ps1 -Port 8080
#   .\launch.ps1 -SkipVenv

param(
    [string]$Port = "8000",
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"

Write-Host "==> Starting local launcher" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Activate virtual environment (skip with -SkipVenv if you manage env yourself)
# ---------------------------------------------------------------------------
if (-not $SkipVenv) {
    $venvActivate = ".\venv\Scripts\Activate.ps1"
    if (Test-Path $venvActivate) {
        Write-Host "==> Activating virtual environment" -ForegroundColor Cyan
        & $venvActivate
    }
    else {
        Write-Host "==> No venv found at .\venv — continuing with system Python" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# Load environment variables from .env (PostgreSQL credentials, SECRET_KEY, etc.)
# ---------------------------------------------------------------------------
$envFile = ".env"
if (Test-Path $envFile) {
    Write-Host "==> Loading environment variables from .env" -ForegroundColor Cyan
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $key, $value = $line -split "=", 2
            [System.Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim())
        }
    }
}
else {
    Write-Host "==> No .env file found — relying on existing environment variables" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Sanity check: confirm PostgreSQL env vars are present (optional but helpful)
# ---------------------------------------------------------------------------
if (-not $env:DB_NAME -or -not $env:DB_USER -or -not $env:DB_PASSWORD) {
    Write-Host "==> DB_NAME/DB_USER/DB_PASSWORD not fully set — Django will fall back to SQLite" -ForegroundColor Yellow
}
else {
    Write-Host "==> Using PostgreSQL database '$env:DB_NAME' on $($env:DB_HOST ?? 'localhost'):$($env:DB_PORT ?? '5432')" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Run the Django development server
# ---------------------------------------------------------------------------
Write-Host "==> Launching Django dev server on http://127.0.0.1:$Port" -ForegroundColor Cyan
python manage.py runserver "0.0.0.0:$Port"
