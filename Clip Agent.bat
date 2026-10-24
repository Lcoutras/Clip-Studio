@echo off
rem Opens the Clip Agent: Claude Code with the clip manager job description in the agent folder.
cd /d "%~dp0agent"
where claude >nul 2>nul
if errorlevel 1 (
  echo Claude Code is not installed yet. See agent\README.md - in PowerShell run:
  echo     irm https://claude.ai/install.ps1 ^| iex
  echo then close this window and open Clip Agent again.
  pause
  exit /b 1
)
if not exist "..\.venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
claude "/process-recordings"
