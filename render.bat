@echo off
rem Re-render after editing output\<video>\captions.csv (captions, music, skip, start/end).
cd /d "%~dp0"
if "%~1"=="" (
  echo Drag the original recording onto render.bat after editing its captions.csv
  pause
  exit /b 1
)
for %%F in (%*) do .venv\Scripts\python -m rlclip render "%%~F"
pause
