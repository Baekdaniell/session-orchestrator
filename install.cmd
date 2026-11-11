@echo off
rem Session Orchestrator install / update. Arguments are passed to install.ps1 (e.g. -Workspace "C:\work\repo").
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
