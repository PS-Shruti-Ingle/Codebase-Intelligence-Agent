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

REM ── Auto-detect Git in common paths ─────────────────────────
if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%"
if exist "%LOCALAPPDATA%\Programs\Git\bin\git.exe" set "PATH=%LOCALAPPDATA%\Programs\Git\bin;%PATH%"
if exist "C:\Program Files\Git\cmd\git.exe" set "PATH=C:\Program Files\Git\cmd;%PATH%"
if exist "C:\Program Files (x86)\Git\cmd\git.exe" set "PATH=C:\Program Files (x86)\Git\cmd;%PATH%"

REM ── Check Git (and auto-download portable Git if missing) ─────
git --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] Git not found. Downloading and configuring portable Git...
    python -c "import urllib.request, zipfile, os; zip_p = os.path.join(os.environ['TEMP'], 'mingit.zip'); dest = os.path.expandvars(r'%%LOCALAPPDATA%%\Programs\Git'); urllib.request.urlretrieve('https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/MinGit-2.45.2-64-bit.zip', zip_p); os.makedirs(dest, exist_ok=True); zipfile.ZipFile(zip_p).extractall(dest); os.remove(zip_p)"
    if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "PATH=%LOCALAPPDATA%\Programs\Git\cmd;%PATH%"
)

git --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git could not be configured automatically.
    echo         Please install Git from https://git-scm.com
    pause
    exit /b 1
)

REM ── Create .env if it doesn't exist ───────────────────────
if not exist "api\.env" (
    echo [INFO] Creating api\.env from template…
    copy "api\.env.example" "api\.env" >nul
    echo [WARN] Please edit api\.env and add your GEMINI_API_KEY.
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
