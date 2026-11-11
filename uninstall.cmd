@echo off
rem Session Orchestrator uninstall. Add -KeepData to keep the board folder.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall %*
pause
