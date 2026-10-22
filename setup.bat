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

echo Creating the Clip Studio desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Clip Studio.lnk'));" ^
  "$s.TargetPath=(Resolve-Path '.venv\Scripts\pythonw.exe').Path; $s.Arguments='-m rlclip studio';" ^
  "$s.WorkingDirectory=(Get-Location).Path; $s.Description='Clip Studio'; $s.Save()"

echo.
echo Setup complete.
echo  - Open Clip Studio from the shortcut on your desktop (or double-click "Clip Studio.vbs")
echo  - Put music in the music\hype, music\funny and music\chill folders
echo  - For your older recordings with voices mixed in, also run setup-voice-removal.bat
pause
exit /b 0

:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
