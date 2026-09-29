@echo off
rem Double-click to start TranscribeMe, then open http://127.0.0.1:8000
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul || (echo uv not found - run setup.cmd first. & pause & exit /b 1)
start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
uv run --frozen transcribeme serve %*
pause
