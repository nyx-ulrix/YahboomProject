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
echo  Yahboom Dashboard - Run Dashboard
echo ============================================================
echo.
echo Starting Flask backend and Vite frontend...
echo.
echo The dashboard will be available at:
echo   - Frontend:  http://localhost:5173  (Vite dev server)
echo   - Backend:   http://localhost:3000  (Flask API)
echo.
echo Two terminal windows will open - one for each service.
echo Press Ctrl+C in each window to stop.
echo.

REM --- Launch backend in a separate window ---
start "Yahboom Dashboard - Backend" cmd /k "cd /d "%cd%" && npm run dev:backend"

REM --- Small delay to let backend start ---
timeout /t 2 /nobreak

REM --- Launch frontend in a separate window ---
start "Yahboom Dashboard - Frontend" cmd /k "cd /d "%cd%" && npm run dev"

echo.
echo Both services are now running in separate windows.
echo.
