@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo   SmartJob SearchAgent - local start
echo ============================================
echo.

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo [WARN] Virtual environment .venv not found. Running with current Python.
)

if "%DATABASE_URL%"=="" (
    set "DATABASE_URL=sqlite:///smartjob.local.sqlite3"
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found.
    pause
    exit /b 1
)

echo [INFO] Checking port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo [INFO] Killing old process on port 8000, PID %%a
    taskkill /PID %%a /F
)

echo [INFO] Running migrations...
python -m alembic upgrade head

echo.
echo [INFO] Starting app on http://127.0.0.1:8000
echo [INFO] Database: %DATABASE_URL%
echo.

start "" http://127.0.0.1:8000/dashboard

python -m uvicorn "app.main:create_app" --factory --host 127.0.0.1 --port 8000

echo.
echo [INFO] Server stopped.
pause