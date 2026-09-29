@echo off
rem Transcribe every audio/video file in a folder.
rem   batch.cmd "D:\Recordings"            (drag a folder onto this file works too)
rem   batch.cmd "D:\Recordings" --recursive --skill meeting-notes
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
if "%~1"=="" (set /p "FOLDER=Folder to transcribe: ") else (set "FOLDER=%~1")
uv run --frozen transcribeme batch "%FOLDER%" %2 %3 %4 %5 %6 %7 %8 %9
pause
