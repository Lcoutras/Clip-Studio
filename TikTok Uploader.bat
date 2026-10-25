@echo off
rem Uploads new clips from output\Exports to TikTok. Leave this window open; close it to stop.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m rlclip tiktok start
pause
