# ============================================================================
# TEAM-GRADE Setup and Run Script
# Installs all dependencies and starts the system
# ============================================================================

$ErrorActionPreference = "Stop"
$WarningPreference = "SilentlyContinue"

Write-Host "
╔════════════════════════════════════════════════════════════════════╗
║         TEAM-GRADE System Setup and Launch                         ║
╚════════════════════════════════════════════════════════════════════╝
" -ForegroundColor Cyan

# Set working directory
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
Write-Host "✓ Working directory: $ProjectRoot`n" -ForegroundColor Green

# ============================================================================
# 1. REFRESH PATH & VERIFY PYTHON
# ============================================================================

Write-Host "[ 1/4 ] Verifying Python environment..." -ForegroundColor Yellow

$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

$python = python --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Python available: $python`n" -ForegroundColor Green
} else {
    Write-Host "✗ Python not found. Please install Python 3.9+`n" -ForegroundColor Red
    exit 1
}

# ============================================================================
# 2. INSTALL PYTHON DEPENDENCIES
# ============================================================================

Write-Host "[ 2/4 ] Installing Python dependencies..." -ForegroundColor Yellow

$Dependencies = @(
    "fastapi",
    "uvicorn",
    "pydantic",
    "google-cloud-firestore",
    "google-cloud-storage",
    "firebase-admin",
    "yt-dlp",
    "opencv-python",
    "numpy",
    "pillow",
    "requests",
    "python-dotenv",
    "pyyaml",
    "pytest"
)

Write-Host "Installing: $($Dependencies -join ', ')" -ForegroundColor Cyan

foreach ($dep in $Dependencies) {
    Write-Host "  → Installing $dep..." -NoNewline
    python -m pip install $dep -q 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " ✓" -ForegroundColor Green
    } else {
        Write-Host " ⚠ (may not be critical)" -ForegroundColor Yellow
    }
}

Write-Host "`n✓ Python dependencies installed`n" -ForegroundColor Green

# ============================================================================
# 3. VERIFY FFMPEG
# ============================================================================

Write-Host "[ 3/4 ] Verifying FFmpeg..." -ForegroundColor Yellow

$ffmpegCheck = ffmpeg -version 2>&1 | Select-Object -First 1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ FFmpeg available: $ffmpegCheck`n" -ForegroundColor Green
} else {
    Write-Host "⚠ FFmpeg not in PATH yet" -ForegroundColor Yellow
    Write-Host "  Attempting to find and add to PATH..." -ForegroundColor Cyan
    
    # Search common locations
    $ffmpegLocations = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe",
        "$env:ProgramFiles\ffmpeg\bin\ffmpeg.exe",
        "$env:ProgramFiles (x86)\ffmpeg\bin\ffmpeg.exe"
    )
    
    $ffmpegFound = $null
    foreach ($location in $ffmpegLocations) {
        if (Test-Path $location) {
            $ffmpegFound = Split-Path $location
            break
        }
    }
    
    if ($ffmpegFound) {
        Write-Host "  Found FFmpeg at: $ffmpegFound" -ForegroundColor Green
        $env:Path += ";$ffmpegFound"
        [Environment]::SetEnvironmentVariable("Path", $env:Path, "User")
        Write-Host "✓ FFmpeg added to PATH`n" -ForegroundColor Green
    } else {
        Write-Host "⚠ FFmpeg not found. Install with: winget install ffmpeg`n" -ForegroundColor Yellow
    }
}

# ============================================================================
# 4. START SERVICES
# ============================================================================

Write-Host "[ 4/4 ] Starting services..." -ForegroundColor Yellow

$processingDir = "$ProjectRoot\team-grade-processing"

# Start API server
Write-Host "  → Starting API server on port 8000..." -ForegroundColor Cyan
Push-Location $processingDir
Start-Process -FilePath python -ArgumentList "-c `"import uvicorn; uvicorn.run('api.server:app', host='0.0.0.0', port=8000, reload=False, log_level='info')`"" -NoNewWindow
Pop-Location

Start-Sleep -Seconds 3

# Start pipeline worker
Write-Host "  → Starting pipeline worker..." -ForegroundColor Cyan
Push-Location $processingDir
Start-Process -FilePath python -ArgumentList "-c `"from ingest.ingest_pipeline_worker import PipelineWorker; w = PipelineWorker(); w.run_worker()`"" -NoNewWindow
Pop-Location

Start-Sleep -Seconds 2

# Verify services
Write-Host "`n  Verifying services..." -ForegroundColor Cyan
$attempts = 0
$maxAttempts = 5

while ($attempts -lt $maxAttempts) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8000/" -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Host "  ✓ API server responding" -ForegroundColor Green
            break
        }
    } catch {
        $attempts++
        if ($attempts -lt $maxAttempts) {
            Start-Sleep -Seconds 1
        }
    }
}

if ($attempts -eq $maxAttempts) {
    Write-Host "  ⚠ API server not responding yet (may still be starting)" -ForegroundColor Yellow
}

# ============================================================================
# SUMMARY
# ============================================================================

Write-Host "
╔════════════════════════════════════════════════════════════════════╗
║                    ✓ Setup Complete!                              ║
╚════════════════════════════════════════════════════════════════════╝

🚀 Services running:
   • API Server:         http://localhost:8000
   • UI Interface:       http://localhost:8000/ui/
   • Pipeline Worker:    processing videos in background

📝 Next steps:
   1. Open http://localhost:8000/ui/ in your browser
   2. Submit a YouTube URL
   3. Watch progress through 9 stages
   
💡 To check status:
   • API logs:     team-grade-processing/api.log
   • Worker logs:  team-grade-processing/logs/pipeline_worker.log

Press Ctrl+C in any terminal to stop services.
" -ForegroundColor Green
