# Build script for TEAM-GRADE Launcher
# Compiles launcher.py into a standalone .exe using PyInstaller

param(
    [switch]$Clean,
    [switch]$Help
)

# ASCII display constants
$CHECK = "[OK]"
$FAIL = "[FAIL]"
$WORK = "[WORK]"
$SEPARATOR = "=" * 70

# Display help
if ($Help) {
    Write-Host "`n$SEPARATOR" -ForegroundColor Cyan
    Write-Host "TEAM-GRADE Launcher Build Script" -ForegroundColor Cyan
    Write-Host "$SEPARATOR" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage: .\build_launcher.ps1 [options]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -Clean  : Remove old build artifacts before building"
    Write-Host "  -Help   : Display this help message"
    Write-Host ""
    Write-Host "Examples:"
    Write-Host "  .\build_launcher.ps1              # Build normally"
    Write-Host "  .\build_launcher.ps1 -Clean       # Clean build"
    Write-Host ""
    Write-Host "Output:"
    Write-Host "  dist\TEAM-GRADE-Launcher.exe"
    Write-Host "$SEPARATOR`n" -ForegroundColor Cyan
    exit 0
}

Write-Host "`n$SEPARATOR" -ForegroundColor Cyan
Write-Host "TEAM-GRADE Launcher Build" -ForegroundColor Cyan
Write-Host "$SEPARATOR`n" -ForegroundColor Cyan

# Get script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir

# Find venv
$venvPath = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"
$activateScript = Join-Path $venvPath "Scripts\Activate.ps1"

# Verify venv exists
Write-Host "$WORK Checking virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path $pythonExe)) {
    Write-Host "$FAIL Python executable not found at: $pythonExe" -ForegroundColor Red
    Write-Host "$SEPARATOR`n" -ForegroundColor Red
    exit 1
}
Write-Host "$CHECK Virtual environment found" -ForegroundColor Green

# Activate venv
Write-Host "$WORK Activating virtual environment..." -ForegroundColor Yellow
try {
    & $activateScript
    Write-Host "$CHECK Virtual environment activated" -ForegroundColor Green
} catch {
    Write-Host "$FAIL Failed to activate venv: $_" -ForegroundColor Red
    exit 1
}

# Check PyInstaller is installed
Write-Host "$WORK Checking PyInstaller installation..." -ForegroundColor Yellow
try {
    $output = & python -c "import PyInstaller; print(PyInstaller.__version__)" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller not found"
    }
    Write-Host "$CHECK PyInstaller version: $output" -ForegroundColor Green
} catch {
    Write-Host "$WARN PyInstaller not found, installing..." -ForegroundColor Yellow
    & pip install pyinstaller > $null 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$FAIL Failed to install PyInstaller" -ForegroundColor Red
        exit 1
    }
    Write-Host "$CHECK PyInstaller installed" -ForegroundColor Green
}

# Clean old builds if requested
if ($Clean) {
    Write-Host "$WORK Cleaning old build artifacts..." -ForegroundColor Yellow

    $buildDir = Join-Path $scriptDir "build"
    $distDir = Join-Path $scriptDir "dist"
    $specCache = Join-Path $scriptDir ".spec.cache"

    if (Test-Path $buildDir) {
        Remove-Item $buildDir -Recurse -Force
        Write-Host "$CHECK Removed build directory" -ForegroundColor Green
    }

    if (Test-Path $distDir) {
        Remove-Item $distDir -Recurse -Force
        Write-Host "$CHECK Removed dist directory" -ForegroundColor Green
    }

    # Remove __pycache__
    Get-ChildItem -Path $scriptDir -Directory -Name "__pycache__" -Recurse | ForEach-Object {
        Remove-Item -Path (Join-Path $scriptDir $_) -Recurse -Force
    }
    Write-Host "$CHECK Cleaned __pycache__ directories" -ForegroundColor Green
}

# Run PyInstaller
Write-Host "`n$WORK Running PyInstaller..." -ForegroundColor Yellow
Write-Host "$SEPARATOR" -ForegroundColor Cyan

$specFile = Join-Path $scriptDir "launcher.spec"
& python -m PyInstaller $specFile `
    --distpath (Join-Path $scriptDir "dist") `
    --workpath (Join-Path $scriptDir "build") `
    -y

if ($LASTEXITCODE -ne 0) {
    Write-Host "`n$SEPARATOR" -ForegroundColor Red
    Write-Host "$FAIL PyInstaller build failed" -ForegroundColor Red
    Write-Host "$SEPARATOR`n" -ForegroundColor Red
    exit 1
}

Write-Host "`n$SEPARATOR" -ForegroundColor Green
Write-Host "$CHECK Build completed successfully!" -ForegroundColor Green
Write-Host "$SEPARATOR`n" -ForegroundColor Green

# Verify output exists
$exePath = Join-Path $scriptDir "dist\TEAM-GRADE-Launcher.exe"
if (Test-Path $exePath) {
    $fileSize = (Get-Item $exePath).Length / 1MB
    Write-Host "$CHECK Output: $exePath" -ForegroundColor Green
    Write-Host "$CHECK File size: $([Math]::Round($fileSize, 1)) MB" -ForegroundColor Green
    Write-Host "`nYou can now double-click TEAM-GRADE-Launcher.exe to launch the system!" -ForegroundColor Cyan
} else {
    Write-Host "$FAIL Expected output not found: $exePath" -ForegroundColor Red
    exit 1
}

Write-Host "`n$SEPARATOR`n" -ForegroundColor Green
