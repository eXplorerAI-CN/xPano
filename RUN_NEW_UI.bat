@echo off
setlocal
set "UI_DIR=%~dp0xpano-ui"
set "TAURI_BIN=%UI_DIR%\node_modules\.bin\tauri.CMD"

if not exist "%UI_DIR%\package.json" (
  echo ERROR: Cannot find "%UI_DIR%\package.json".
  echo Please run this BAT from the xPano project root: %~dp0
  pause
  exit /b 1
)

cd /d "%UI_DIR%"
echo Starting xPano new UI from:
echo %CD%

if not exist "%TAURI_BIN%" (
  where pnpm >nul 2>nul
  if errorlevel 1 (
    echo ERROR: pnpm was not found in PATH and local UI dependencies are missing.
    echo Please install or enable pnpm, then try again.
    pause
    exit /b 1
  )

  echo Local Tauri CLI was not found:
  echo "%TAURI_BIN%"
  echo Installing UI dependencies with pnpm install...
  call pnpm.cmd install
  if errorlevel 1 (
    echo ERROR: pnpm install failed.
    pause
    exit /b 1
  )
)

echo Using local Tauri CLI:
echo "%TAURI_BIN%"
call "%TAURI_BIN%" dev
if errorlevel 1 pause
