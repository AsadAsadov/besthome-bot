@echo on
setlocal EnableExtensions EnableDelayedExpansion

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

REM --- Python 3.12 ---
set "PY=C:\Users\besthome.az\AppData\Local\Programs\Python\Python312\python.exe"

if not exist logs mkdir logs

REM --- tarix və saat formatı ---
for /f "tokens=1-3 delims=." %%a in ("%date%") do (
    set DD=%%a
    set MM=%%b
    set YYYY=%%c
)

for /f "tokens=1-2 delims=:" %%a in ("%time%") do (
    set HH=%%a
    set MN=%%b
)

set "LOGFILE=%cd%\logs\run_%YYYY%-%MM%-%DD%_%HH%-%MN%.log"

echo ================================ >> "%LOGFILE%"
echo START %date% %time% >> "%LOGFILE%"
echo ================================ >> "%LOGFILE%"

echo [1/4] Running estatebase_sync.py
echo [1/4] Running estatebase_sync.py >> "%LOGFILE%"
"%PY%" estatebase_sync.py --days -3 >> "%LOGFILE%" 2>&1
set "ERR=!errorlevel!"
echo estatebase_sync.py finished with errorlevel=!ERR!
echo estatebase_sync.py finished with errorlevel=!ERR! >> "%LOGFILE%"
if not "!ERR!"=="0" (
    echo ERROR in estatebase_sync.py
    pause
    exit /b !ERR!
)

echo [2/4] Running auto_zip.py
echo [2/4] Running auto_zip.py >> "%LOGFILE%"
"%PY%" auto_zip.py >> "%LOGFILE%" 2>&1
set "ERR=!errorlevel!"
echo auto_zip.py finished with errorlevel=!ERR!
echo auto_zip.py finished with errorlevel=!ERR! >> "%LOGFILE%"
if not "!ERR!"=="0" (
    echo ERROR in auto_zip.py
    pause
    exit /b !ERR!
)

echo [3/4] Running upload_gdrive.py besthome.zip
echo [3/4] Running upload_gdrive.py besthome.zip >> "%LOGFILE%"
"%PY%" upload_gdrive.py besthome.zip >> "%LOGFILE%" 2>&1
set "ERR=!errorlevel!"
echo upload_gdrive.py finished with errorlevel=!ERR!
echo upload_gdrive.py finished with errorlevel=!ERR! >> "%LOGFILE%"
if not "!ERR!"=="0" (
    echo ERROR in upload_gdrive.py
    pause
    exit /b !ERR!
)

echo [4/4] Running notify_bot.py
echo [4/4] Running notify_bot.py >> "%LOGFILE%"
"%PY%" notify_bot.py >> "%LOGFILE%" 2>&1
set "ERR=!errorlevel!"
echo notify_bot.py finished with errorlevel=!ERR!
echo notify_bot.py finished with errorlevel=!ERR! >> "%LOGFILE%"
if not "!ERR!"=="0" (
    echo ERROR in notify_bot.py
    pause
    exit /b !ERR!
)

echo FINISH %date% %time% >> "%LOGFILE%"
echo OK
pause
exit /b 0