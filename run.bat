@echo off
REM ============================================================================
REM TEAM-GRADE Quick Startup Script
REM Starts API and Worker with proper environment setup
REM ============================================================================

SETLOCAL ENABLEDELAYEDEXPANSION

CD /D "%~dp0"
echo.
echo ════════════════════════════════════════════════════════════════════
echo                  TEAM-GRADE System Startup
echo ════════════════════════════════════════════════════════════════════
echo.

REM Activate virtual environment if it exists
if exist ".venv\Scripts\activate.bat" (
    echo [1/3] Activating Python virtual environment...
    call .venv\Scripts\activate.bat
    echo ✓ Virtual environment activated
    echo.
)

REM Install dependencies
echo [2/3] Ensuring all dependencies are installed...
python -m pip install -q google-cloud-firestore yt-dlp opencv-python numpy pillow requests python-dotenv fastapi uvicorn pydantic 2>nul
echo ✓ Dependencies checked
echo.

REM Start services
echo [3/3] Starting services...
echo.
echo Starting API server on port 8000...
cd team-grade-processing
start /B python -c "import uvicorn; uvicorn.run('api.server:app', host='0.0.0.0', port=8000, reload=False, log_level='info')"
timeout /t 2 /nobreak

echo Starting pipeline worker...
start /B python -c "from ingest.ingest_pipeline_worker import PipelineWorker; w = PipelineWorker(); w.run_worker()"
timeout /t 2 /nobreak

cd ..
echo.
echo ════════════════════════════════════════════════════════════════════
echo ✓ Setup Complete!
echo.
echo 🚀 Services running:
echo    • API Server:      http://localhost:8000
echo    • UI Interface:    http://localhost:8000/ui/
echo    • Pipeline Worker: processing videos
echo.
echo 📝 Next steps:
echo    1. Open http://localhost:8000/ui/ in your browser
echo    2. Submit a YouTube URL
echo    3. Watch progress through 9 stages
echo.
echo ════════════════════════════════════════════════════════════════════
echo.
