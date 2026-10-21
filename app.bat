@echo off
rem Opens Clip Studio in your browser. Keep this window open while you use it.
cd /d "%~dp0"
if not exist .venv (
  echo Run setup.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python -m rlclip app %*
pause
