@echo off
setlocal enableextensions enabledelayedexpansion
cd /d "%~dp0"

set "PROJECT_NAME=auto_crypto_quant"

docker info >nul 2>&1
if %errorlevel%==0 (
  echo Stopping Docker service...
  docker compose -p %PROJECT_NAME% down
  echo Stopped.
  pause
  exit /b 0
)

if exist ".run.pid" (
  set /p LOCAL_PID=<.run.pid
  if not "!LOCAL_PID!"=="" (
    taskkill /PID !LOCAL_PID! /F >nul 2>&1
    if !errorlevel!==0 (
      del /f /q ".run.pid" >nul 2>&1
      echo Stopped local Python service. PID=!LOCAL_PID!
      pause
      exit /b 0
    )
  )
)

echo Docker not available and no tracked local PID was found.
echo If you started manually, stop the python process or close its terminal.
pause
