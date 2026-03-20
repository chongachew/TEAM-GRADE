# TEAM-GRADE Worker Launcher
# Activates venv and runs the ingestion worker

param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectRoot,

    [Parameter(Mandatory=$true)]
    [string]$VenvPath
)

# ASCII display constants
$CHECK = "[OK]"
$FAIL = "[FAIL]"
$WORK = "[WORK]"
$SEPARATOR = "=" * 70

Write-Host "`n$SEPARATOR" -ForegroundColor Cyan
Write-Host "TEAM-GRADE Worker Startup" -ForegroundColor Cyan
Write-Host "$SEPARATOR`n" -ForegroundColor Cyan

# Verify venv exists
$activateScript = Join-Path $VenvPath "Scripts\Activate.ps1"
if (-not (Test-Path $activateScript)) {
    Write-Host "$FAIL Virtual environment not found at: $VenvPath" -ForegroundColor Red
    Write-Host "$SEPARATOR`n" -ForegroundColor Red
    exit 1
}

# Activate venv
Write-Host "$WORK Activating virtual environment..." -ForegroundColor Yellow
try {
    & $activateScript
    Write-Host "$CHECK Virtual environment activated" -ForegroundColor Green
} catch {
    Write-Host "$FAIL Failed to activate venv: $_" -ForegroundColor Red
    exit 1
}

# Change to project root
Write-Host "$WORK Changing directory to project root..." -ForegroundColor Yellow
try {
    Set-Location $ProjectRoot
    Write-Host "$CHECK Working directory set: $ProjectRoot" -ForegroundColor Green
} catch {
    Write-Host "$FAIL Failed to change directory: $_" -ForegroundColor Red
    exit 1
}

# Navigate to team-grade-processing
$processingDir = Join-Path $ProjectRoot "team-grade-processing"
if (-not (Test-Path $processingDir)) {
    Write-Host "$FAIL team-grade-processing directory not found" -ForegroundColor Red
    exit 1
}

Set-Location $processingDir

# Display startup info
Write-Host "`n$SEPARATOR" -ForegroundColor Cyan
Write-Host "Starting Worker..." -ForegroundColor Cyan
Write-Host "$SEPARATOR`n" -ForegroundColor Cyan

# Run worker
try {
    Write-Host "$WORK Launching ingestion worker module..." -ForegroundColor Yellow
    python -m ingest.ingest_worker
} catch {
    Write-Host "$FAIL Failed to start worker: $_" -ForegroundColor Red
    exit 1
}

# Keep window open if script exits
Write-Host "`n$SEPARATOR" -ForegroundColor Yellow
Write-Host "[INFO] Worker has stopped. This window will remain open." -ForegroundColor Yellow
Write-Host "$SEPARATOR`n" -ForegroundColor Yellow
Read-Host "Press Enter to close this window"
