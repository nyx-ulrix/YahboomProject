@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM --- Change to dashboard directory ---
cd "Yahboom Dashboard"
if errorlevel 1 (
  echo ERROR: Could not navigate to "Yahboom Dashboard" folder.
  pause
  exit /b 1
)

echo ============================================================
echo  Yahboom Dashboard - Install All Dependencies
echo ============================================================
echo.

REM --- Check for Node.js ---
where node >nul 2>&1
if errorlevel 1 (
  echo ERROR: Node.js is not installed or not on PATH.
  echo        Download and install from https://nodejs.org/
  echo.
  pause
  exit /b 1
)
for /f "tokens=*" %%v in ('node -v 2^>nul') do echo [OK] %%v

REM --- Check for Python ---
where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python 3 is not available as "python" on PATH.
  echo        Install from https://www.python.org/ and enable "Add to PATH",
  echo        or ensure the Python launcher works: python --version
  echo.
  pause
  exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>nul') do echo [OK] %%v
echo.

REM --- [1/2] Frontend Dependencies (NPM/Pnpm) ---
echo [1/2] Installing frontend dependencies...
where pnpm >nul 2>&1
if not errorlevel 1 (
  echo Using pnpm (recommended for this project)...
  call pnpm install
) else (
  echo Using npm as package manager...
  call npm install
)
if errorlevel 1 (
  echo ERROR: Failed to install frontend dependencies.
  echo.
  pause
  exit /b 1
)
echo [OK] Frontend dependencies installed.
echo.

REM --- [2/2] Backend Virtual Environment & Dependencies ---
echo [2/2] Setting up Python backend virtual environment...
call node scripts\setup-backend.mjs
if errorlevel 1 (
  echo ERROR: Failed to set up backend.
  echo.
  pause
  exit /b 1
)
echo [OK] Backend dependencies installed.
echo.

echo ============================================================
echo  SUCCESS: All dependencies installed!
echo ============================================================
echo.
echo Next, run: run-dashboard.bat
echo.
pause
