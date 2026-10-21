@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo Installing the AI voice remover (large download, a few minutes)...
.venv\Scripts\python -m pip install -r requirements-voice.txt
echo.
echo Done. The first clip will also download the voice model (~80 MB) once.
pause
