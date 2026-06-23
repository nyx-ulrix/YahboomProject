@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  Yahboom Dashboard - Install All Dependencies
echo ============================================================
echo.

REM --- Prerequisites: Node.js ---
where node >nul 2>&1
if errorlevel 1 (
  echo ERROR: Node.js is not installed or not on PATH.
  echo        Download and install from https://nodejs.org/
  goto :fail
)
for /f "tokens=*" %%v in ('node -v 2^>nul') do echo Node.js %%v

REM --- Prerequisites: Python (used by setup-backend.mjs as "python") ---
where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python 3 is not available as "python" on PATH.
  echo        Install from https://www.python.org/ and enable "Add to PATH",
  echo        or ensure the Python launcher works:  python --version
  goto :fail
)
for /f "tokens=*" %%v in ('python --version 2^>nul') do echo %%v
echo.

REM --- [1/2] Frontend (Vite + React) ---
echo [1/2] Installing frontend dependencies...
where pnpm >nul 2>&1
if not errorlevel 1 (
  echo Using pnpm ^(recommended for this project^)...
  call pnpm install
) else (
  echo pnpm not found; using npm instead.
  echo Tip: npm install -g pnpm  then re-run this script for pnpm-lock installs.
  call npm install
)
if errorlevel 1 goto :fail
echo.

REM --- [2/2] Backend (Flask + Python packages in backend/.venv) ---
echo [2/2] Setting up Python backend virtual environment...
call node scripts\setup-backend.mjs
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  All dependencies installed successfully.
echo ============================================================
echo.
echo  Frontend dev server:  pnpm dev   ^(or npm run dev^)
echo  Backend API server:   npm run dev:backend
echo.
echo  Optional: copy .env.example to .env if you need custom settings.
echo.
pause
exit /b 0

:fail
echo.
echo Installation failed. Fix the errors above and run setup.bat again.
pause
exit /b 1
