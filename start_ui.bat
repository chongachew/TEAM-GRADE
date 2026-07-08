@echo off
REM TEAM-GRADE Upload UI Server Launcher
REM Windows batch script to start the UI server

setlocal enabledelayedexpansion

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM Check if virtual environment exists
if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo ######################################
    echo ERROR: Virtual environment not found
    echo ######################################
    echo.
    echo Please run from TEAM-GRADE directory:
    echo   cd C:\Users\ricky\OneDrive\Desktop\Team-Grade\TEAM-GRADE
    echo.
    pause
    exit /b 1
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ######################################
    echo ERROR: Python not found
    echo ######################################
    echo.
    pause
    exit /b 1
)

REM Clear screen and show header
cls
echo.
echo ======================================================================
echo   TEAM-GRADE UPLOAD UI SERVER
echo ======================================================================
echo.
echo   Starting HTTP server on port 8080...
echo.
echo   Access UI:  http://localhost:8080/upload.html
echo   API:        http://localhost:8000/api (must be running)
echo.
echo   Press Ctrl+C to stop the server
echo ======================================================================
echo.

REM Start the UI server
python start_ui.py

if errorlevel 1 (
    echo.
    echo ######################################
    echo ERROR: Failed to start UI server
    echo ######################################
    echo.
    pause
    exit /b 1
)

pause
