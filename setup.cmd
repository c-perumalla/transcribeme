@echo off
rem Double-click to set up TranscribeMe on Windows. Safe to re-run.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
echo.
pause
