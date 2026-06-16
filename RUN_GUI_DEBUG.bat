@echo off
setlocal
cd /d "%~dp0"
set "LOG=%~dp0xpano_gui_error.log"

if exist "%LOG%" del "%LOG%"

echo Starting xPano GUI in debug mode...
echo.
python.exe "%~dp0app.py" >> "%LOG%" 2>&1

if errorlevel 1 (
    echo.
    echo xPano GUI failed. Error log:
    echo %LOG%
    echo.
    type "%LOG%"
    echo.
) else (
    echo xPano GUI closed normally.
)

pause
