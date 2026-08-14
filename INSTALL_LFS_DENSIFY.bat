@echo off
cd /d "%~dp0"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" set "POWERSHELL_EXE=powershell"
"%POWERSHELL_EXE%" -ExecutionPolicy Bypass -File "%~dp0scripts\install_lfs_densify.ps1" %*
