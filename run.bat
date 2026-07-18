@echo on
setlocal EnableExtensions EnableDelayedExpansion

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

REM Usage: run.bat [--interactive]
set "INTERACTIVE=0"
if /I "%~1"=="--interactive" set "INTERACTIVE=1"

set "PY=C:\Users\besthome.az\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set "TS=%%i"
set "LOGFILE=%cd%\logs\run_%TS%.log"

echo ================================ >> "%LOGFILE%"
echo START %TS% >> "%LOGFILE%"
echo ================================ >> "%LOGFILE%"

echo [1/3] Syncing EstateBase to local besthome.db
echo [1/3] Syncing EstateBase to local besthome.db >> "%LOGFILE%"
"%PY%" estatebase_sync.py --days -3 >> "%LOGFILE%" 2>&1
set "ERR=!errorlevel!"
echo estatebase_sync.py finished with errorlevel=!ERR!
echo estatebase_sync.py finished with errorlevel=!ERR! >> "%LOGFILE%"
if not "!ERR!"=="0" goto :fail

echo [2/3] Deploying besthome.db directly to VPS
echo [2/3] Deploying besthome.db directly to VPS >> "%LOGFILE%"
"%PY%" deploy_besthome_db.py >> "%LOGFILE%" 2>&1
set "ERR=!errorlevel!"
echo deploy_besthome_db.py finished with errorlevel=!ERR!
echo deploy_besthome_db.py finished with errorlevel=!ERR! >> "%LOGFILE%"
if not "!ERR!"=="0" goto :fail

echo [3/3] Sending success notification
echo [3/3] Sending success notification >> "%LOGFILE%"
if exist notify_bot.py (
    "%PY%" notify_bot.py >> "%LOGFILE%" 2>&1
    set "ERR=!errorlevel!"
) else (
    set "ERR=0"
)
echo notify_bot.py finished with errorlevel=!ERR!
echo notify_bot.py finished with errorlevel=!ERR! >> "%LOGFILE%"
if not "!ERR!"=="0" goto :fail

echo FINISH %TS% >> "%LOGFILE%"
echo OK
if "%INTERACTIVE%"=="1" pause
exit /b 0

:fail
echo ERROR errorlevel=!ERR!
echo ERROR errorlevel=!ERR! >> "%LOGFILE%"
if "%INTERACTIVE%"=="1" pause
exit /b !ERR!
