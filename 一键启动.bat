@echo off
setlocal enableextensions enabledelayedexpansion
cd /d "%~dp0"

set "PROJECT_NAME=auto_crypto_quant"

echo [1/7] Check .env
if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env from .env.example
)

set "APP_PORT=18000"
for /f "tokens=2 delims==" %%a in ('findstr /B /C:"APP_PORT=" ".env"') do set "APP_PORT=%%a"
if "%APP_PORT%"=="" set "APP_PORT=18000"
echo [2/7] Target port: %APP_PORT%

set "USE_DOCKER=0"
docker info >nul 2>&1
if %errorlevel%==0 set "USE_DOCKER=1"

if "%USE_DOCKER%"=="1" (
  echo [3/7] Start with Docker
  docker compose -p %PROJECT_NAME% up -d --build
  if errorlevel 1 (
    echo Docker start failed.
    pause
    exit /b 1
  )
) else (
  echo [3/7] Docker unavailable, start with local Python
  set "PY_CMD="
  if "!PY_CMD!"=="" call :try_python ".venv\Scripts\python.exe"
  if "!PY_CMD!"=="" call :try_python "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
  if "!PY_CMD!"=="" call :try_python "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
  if "!PY_CMD!"=="" call :try_python "%LocalAppData%\Programs\Python\Python312\python.exe"
  if "!PY_CMD!"=="" call :try_python "%LocalAppData%\Programs\Python\Python311\python.exe"
  if "!PY_CMD!"=="" call :try_python "%LocalAppData%\Programs\Python\Launcher\py.exe -3"
  if "!PY_CMD!"=="" call :try_python "py -3"
  if "!PY_CMD!"=="" call :try_python "python"
  if "!PY_CMD!"=="" (
    echo Python not found in PATH or common install locations.
    echo USERPROFILE=%USERPROFILE%
    echo LOCALAPPDATA=%LOCALAPPDATA%
    echo Tried: %USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe
    echo Tried: %USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe
    echo Please install Python 3.11+ or start Docker Desktop.
    pause
    exit /b 1
  )

  echo [4/7] Setup venv and install requirements
  echo Using Python: !PY_CMD!
  if not exist ".venv\Scripts\activate.bat" (
    !PY_CMD! -m venv .venv
  )
  call ".venv\Scripts\activate.bat"
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Pip install failed.
    pause
    exit /b 1
  )

  echo [5/7] Launch local app and save PID
  powershell -NoProfile -Command "$p = Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList 'run.py' -WorkingDirectory '%cd%' -PassThru; Set-Content -Path '.run.pid' -Value $p.Id -Encoding ascii"
)

echo [6/7] Waiting for health check...
set /a RETRY=0
:wait_loop
set /a RETRY+=1
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%APP_PORT%/api/health' -TimeoutSec 2; if($r.StatusCode -eq 200){ exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel%==0 goto ready
if %RETRY% GEQ 40 goto failed
timeout /t 1 >nul
goto wait_loop

:ready
echo [7/7] Service is ready. Open browser.
start "" "http://127.0.0.1:%APP_PORT%"
echo Start complete.
pause
exit /b 0

:failed
echo Service health check timeout.
if "%USE_DOCKER%"=="1" (
  echo ---- Docker logs ----
  docker compose -p %PROJECT_NAME% logs --tail=80
)
pause
exit /b 1

:try_python
set "CAND=%~1"
%CAND% --version >nul 2>&1
if !errorlevel!==0 set "PY_CMD=%CAND%"
exit /b 0
