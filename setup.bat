@echo off
setlocal
cd /d "%~dp0"
echo === Rocket League Clipper setup ===

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo Installing ffmpeg...
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  echo.
  echo ffmpeg installed. Close this window and run setup.bat again.
  pause
  exit /b 1
)

set "PY="
py -3.12 --version >nul 2>nul && set "PY=py -3.12"
if not defined PY (python --version >nul 2>nul && set "PY=python")
if not defined PY (
  echo Installing Python 3.12...
  winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
  echo.
  echo Python installed. Close this window and run setup.bat again.
  pause
  exit /b 1
)

if not exist .venv (
  echo Creating environment...
  %PY% -m venv .venv || goto :fail
)
.venv\Scripts\python -m pip install --upgrade pip >nul
.venv\Scripts\python -m pip install -r requirements.txt || goto :fail

echo.
echo Setup complete.
echo  - Put music in the music\hype, music\funny and music\chill folders
echo  - Drag a recording (or a folder of them) onto clip.bat
echo  - For your older recordings with voices mixed in, also run setup-voice-removal.bat
pause
exit /b 0

:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
