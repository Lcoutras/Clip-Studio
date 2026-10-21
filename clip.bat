@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag a recording or a folder of recordings onto clip.bat
  echo or run:  clip.bat "C:\Users\you\Videos\game.mp4"
  pause
  exit /b 1
)
for %%F in (%*) do .venv\Scripts\python -m rlclip run "%%~F"
pause
