@echo off
setlocal
set "UI_DIR=%~dp0xpano-ui"
set "TAURI_BIN=%UI_DIR%\node_modules\.bin\tauri.CMD"
set "VENV_DIR=%~dp0.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%~dp0requirements.txt"

if not exist "%UI_DIR%\package.json" (
  echo ERROR: Cannot find "%UI_DIR%\package.json".
  echo Please run this BAT from the xPano project root: %~dp0
  pause
  exit /b 1
)

cd /d "%UI_DIR%"
echo Starting xPano new UI from:
echo %CD%

if not exist "%VENV_PYTHON%" (
  where py >nul 2>nul
  if not errorlevel 1 (
    echo Creating project Python environment with py...
    py -3 -m venv "%VENV_DIR%"
  ) else (
    where python >nul 2>nul
    if errorlevel 1 (
      echo ERROR: Python was not found in PATH.
      echo Please install Python 3.10+ or add Python to PATH.
      pause
      exit /b 1
    )
    echo Creating project Python environment with python...
    python -m venv "%VENV_DIR%"
  )
  if errorlevel 1 (
    echo ERROR: Failed to create Python virtual environment.
    pause
    exit /b 1
  )
)

echo Checking xPano Python dependencies...
"%VENV_PYTHON%" -c "import cv2, numpy, PIL, piexif" >nul 2>nul
if errorlevel 1 (
  if not exist "%REQ_FILE%" (
    echo ERROR: Cannot find "%REQ_FILE%".
    pause
    exit /b 1
  )
  "%VENV_PYTHON%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --prefer-binary -r "%REQ_FILE%"
  if errorlevel 1 (
    echo Tsinghua mirror failed, retrying with default PyPI...
    "%VENV_PYTHON%" -m pip install --prefer-binary -r "%REQ_FILE%"
  )
  if errorlevel 1 (
    echo ERROR: Failed to install xPano Python dependencies.
    pause
    exit /b 1
  )
)

set "XPANO_PYTHON=%VENV_PYTHON%"
echo Using xPano Python:
echo "%XPANO_PYTHON%"

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
