@echo off
REM TEAM-GRADE Ingestion API + UI Startup Script
REM This script starts the API server

echo.
echo ========================================
echo TEAM-GRADE Ingestion API Startup
echo ========================================
echo.

REM Check if FastAPI is installed
python -m pip list | findstr "fastapi" >nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r api/requirements.txt
    echo.
)

echo Starting API server on http://localhost:8000
echo.
echo API Documentation: http://localhost:8000/docs
echo API Health Check: http://localhost:8000/health
echo.
echo Open UI at: file:///[absolute-path]/ui/index.html
echo Or use an HTTP server to serve the UI folder
echo.
echo Press Ctrl+C to stop the server
echo.

python api/server.py
