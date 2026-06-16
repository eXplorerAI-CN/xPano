@echo off
setlocal
cd /d "%~dp0"
wscript.exe "%~dp0xPano.vbs"
exit /b 0
