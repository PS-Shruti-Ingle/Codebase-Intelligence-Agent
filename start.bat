@echo off
title Codebase Intelligence Agent

echo ================================================
echo   Codebase Intelligence Agent — Launcher
echo ================================================
echo.

REM ── Change to script directory ───────────────────────────
cd /d "%~dp0"

REM ── Check Python ──────────────────────────────────────────
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo         Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

REM ── Check Node ────────────────────────────────────────────
node --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Node.js is not installed or not in PATH.
    echo         Install Node.js 18+ from https://nodejs.org
    pause
    exit /b 1
)

REM ── Check Git ─────────────────────────────────────────────
git --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git is not installed or not in PATH.
    echo         Install Git from https://git-scm.com
    pause
    exit /b 1
)

REM ── Create .env if it doesn't exist ───────────────────────
if not exist "api\.env" (
    echo [INFO] Creating api\.env from template…
    copy "api\.env.example" "api\.env" >nul
    echo [WARN] Please edit api\.env and add your GROQ_API_KEY.
    echo.
)

REM ── Install Python dependencies ───────────────────────────
echo [1/3] Installing Python dependencies…
pip install -r server/requirements.txt -q
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install Python dependencies.
    pause
    exit /b 1
)
echo       Done.

REM ── Install Node dependencies ─────────────────────────────
echo [2/3] Installing Node.js dependencies…
cd api
call npm install --silent
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] npm install failed.
    pause
    exit /b 1
)
cd ..
echo       Done.

REM ── Create repos directory ────────────────────────────────
if not exist "repos" mkdir repos

REM ── Start server ─────────────────────────────────────────
echo [3/3] Starting Codebase Intelligence Agent…
echo.
echo  Open your browser at:  http://localhost:3000
echo  Press Ctrl+C to stop.
echo.

cd api
node server.js
