# Django Project Launcher Script for PowerShell
# Usage: .\launch.ps1 [command]
# Commands: setup, run, migrate, shell, test, reset

param(
    [string]$Command = "run"
)

$ProjectName = "Django Business Management System"
$PythonCmd = "python"
$ManagePy = "manage.py"
$VenvDir = "venv"

# Always run relative to the script's own folder, not wherever it was
# double-clicked/invoked from — otherwise venv/manage.py lookups can silently
# fail if the working directory is different (e.g. launched from Explorer).
Set-Location -Path $PSScriptRoot

# Function to write colored output
function Write-Status {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING] $Message" -ForegroundColor Yellow
}

# Renamed from Write-Error: overriding the built-in cmdlet made real
# PowerShell errors (and $ErrorActionPreference behavior) unpredictable.
function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Write-Header {
    Write-Host "================================" -ForegroundColor Blue
    Write-Host " $ProjectName" -ForegroundColor Blue
    Write-Host "================================" -ForegroundColor Blue
}

# Check if virtual environment exists
function Test-VirtualEnvironment {
    if (-not (Test-Path $VenvDir)) {
        Write-Warning "Virtual environment not found. Creating one..."
        & $PythonCmd -m venv $VenvDir
        Write-Status "Virtual environment created."
    }
}

# Activate virtual environment
function Enable-VirtualEnvironment {
    if (Test-Path $VenvDir) {
        if (Test-Path "$VenvDir\Scripts\Activate.ps1") {
            # MUST be dot-sourced ("."), not "&"-invoked. "&" runs the
            # activation script in a child scope, so $env:PATH / $env:VIRTUAL_ENV
            # changes were discarded the moment it returned — every later
            # "python"/"pip" call kept hitting the GLOBAL interpreter instead
            # of the venv. That's what caused "run" to silently no-op/flash:
            # manage.py runserver would fail immediately (missing deps/django)
            # in a console window that doesn't stay open on its own.
            . "$VenvDir\Scripts\Activate.ps1"
        } else {
            Write-ErrorMsg "Virtual environment activation script not found!"
            Wait-Exit 1
        }
        Write-Status "Virtual environment activated."
    } else {
        Write-ErrorMsg "Virtual environment not found!"
        Wait-Exit 1
    }
}

# Install requirements
function Install-Requirements {
    if (Test-Path "requirements.txt") {
        Write-Status "Installing requirements..."
        pip install -r requirements.txt
        Write-Status "Requirements installed successfully."
    } else {
        Write-Warning "requirements.txt not found. Skipping dependency installation."
    }
}

# Run migrations
function Invoke-Migrations {
    Write-Status "Running database migrations..."
    & $PythonCmd $ManagePy makemigrations
    & $PythonCmd $ManagePy migrate
    Write-Status "Migrations completed."
}

# Collect static files
function Invoke-CollectStatic {
    Write-Status "Collecting static files..."
    & $PythonCmd $ManagePy collectstatic --noinput
    Write-Status "Static files collected."
}

# Setup project
function Initialize-Project {
    Write-Header
    Write-Status "Setting up Django project..."

    Test-VirtualEnvironment
    Enable-VirtualEnvironment
    Install-Requirements
    Invoke-Migrations

    if (Test-Path "apps\customers\management\commands\populate_cities.py") {
        Write-Status "Populating cities data..."
        & $PythonCmd $ManagePy populate_cities
    }
    if (Test-Path "apps\authentication\management\commands\create_superuser.py") {
        Write-Status "create admin user..."
        & $PythonCmd $ManagePy create_superuser
    }

    Invoke-CollectStatic

    Write-Host ""
    Write-Status "Setup completed! You can now run the development server."
    Write-Status "Use: .\launch.ps1 run"
}

# Run development server
function Start-DevelopmentServer {
    Write-Header
    Enable-VirtualEnvironment
    Write-Status "Starting Django development server..."
    Write-Status "Server will be available at: http://127.0.0.1:8000"
    Write-Status "Admin panel: http://127.0.0.1:8000/admin"
    Write-Host ""
    # Was "& $PythonCmd $ManagePy runserver" with nothing after it: if this
    # process ever exits (venv wasn't really active -> immediate ImportError,
    # or port already in use), the window running the script closes instantly
    # with no chance to read the error. Capture the exit code and pause.
    & $PythonCmd $ManagePy runserver
    if ($LASTEXITCODE -ne 0) {
        Write-ErrorMsg "runserver exited with code $LASTEXITCODE (see output above)."
        Wait-Exit $LASTEXITCODE
    }
}

# Run Django shell
function Start-DjangoShell {
    Enable-VirtualEnvironment
    Write-Status "Starting Django shell..."
    & $PythonCmd $ManagePy shell
}

# Run tests
function Invoke-Tests {
    Enable-VirtualEnvironment
    Write-Status "Running tests..."
    & $PythonCmd $ManagePy test
}

# Reset database
function Reset-Database {
    Write-Warning "This will delete your database and all data!"
    $confirmation = Read-Host "Are you sure? (y/N)"
    if ($confirmation -eq 'y' -or $confirmation -eq 'Y') {
        Enable-VirtualEnvironment
        Write-Status "Removing database..."
        if (Test-Path "db.sqlite3") {
            Remove-Item "db.sqlite3" -Force
        }
        Write-Status "Removing migration files..."
        Get-ChildItem -Path . -Recurse -Filter "*.py" | Where-Object {
            $_.FullName -like "*\migrations\*" -and $_.Name -ne "__init__.py"
        } | Remove-Item -Force
        Get-ChildItem -Path . -Recurse -Filter "*.pyc" | Where-Object {
            $_.FullName -like "*\migrations\*"
        } | Remove-Item -Force
        Invoke-Migrations
        Write-Status "Database reset completed."
    } else {
        Write-Status "Database reset cancelled."
    }
}

# Show help
function Show-Help {
    Write-Header
    Write-Host "Available commands:"
    Write-Host "  setup    - Initial project setup (install deps, migrate, populate data)"
    Write-Host "  run      - Start development server"
    Write-Host "  migrate  - Run database migrations"
    Write-Host "  shell    - Open Django shell"
    Write-Host "  test     - Run tests"
    Write-Host "  reset    - Reset database (WARNING: destructive)"
    Write-Host "  help     - Show this help message"
    Write-Host ""
    Write-Host "Usage: .\launch.ps1 [command]"
    Write-Host "If no command is provided, 'run' is used by default."
}

# Keep the console window open on error/exit when the script was launched by
# double-clicking (no parent PowerShell console to fall back to), instead of
# letting the window vanish immediately ("flashed and closed").
function Wait-Exit {
    param([int]$Code = 0)
    if ($Host.Name -eq "ConsoleHost") {
        Write-Host ""
        Read-Host "Press Enter to close this window"
    }
    exit $Code
}

# Main script logic, wrapped so any unhandled error still pauses instead of
# flashing the window shut.
try {
    switch ($Command.ToLower()) {
        "setup" {
            Initialize-Project
        }
        "run" {
            Start-DevelopmentServer
        }
        "migrate" {
            Enable-VirtualEnvironment
            Invoke-Migrations
        }
        "shell" {
            Start-DjangoShell
        }
        "test" {
            Invoke-Tests
        }
        "reset" {
            Reset-Database
        }
        "help" {
            Show-Help
        }
        default {
            Write-ErrorMsg "Unknown command: $Command"
            Show-Help
            Wait-Exit 1
        }
    }
    Wait-Exit 0
} catch {
    Write-ErrorMsg "Unhandled error: $($_.Exception.Message)"
    Wait-Exit 1
}