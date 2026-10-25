@echo off
rem Asks TikTok what happened to every clip the uploader sent (waiting in your inbox, posted, failed).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m rlclip tiktok status
pause
