@echo off
setlocal
set PORT=8899
set URL=http://127.0.0.1:%PORT%/api/action
set CT=Content-Type: application/json
set ARGS=%*

if "%ARGS%"=="" (
  echo usage: paw ^<command^> [args...]
  echo   paw click 960 540
  echo   paw type hello
  echo   paw shot D:/a.png
  echo   paw stop
  exit /b 2
)

if /i "%ARGS%"=="stop" (
  curl.exe -s -X POST %URL% -H "%CT%" -d "{\"line\":\"stop\"}" >nul 2>nul
  echo stopped
  exit /b 0
)

curl.exe -s -m 2 -o nul -w "%%{http_code}" -X POST %URL% -H "%CT%" -d "{\"line\":\"size\"}" 2>nul | findstr /c:"200" >nul
if not errorlevel 1 goto ready

echo [paw] starting service in background...
start "" /min python "%~dp0paw.py" start %PORT%
if errorlevel 1 exit /b 1
ping -n 4 127.0.0.1 >nul

:ready
curl.exe -s -X POST %URL% -H "%CT%" -d "{\"line\":\"%ARGS%\"}"
echo.
