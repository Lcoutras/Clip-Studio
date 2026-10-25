@echo off
rem Puts clips that reached TikTok but were never posted (e.g. the notification was deleted) back in the upload queue.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m rlclip tiktok resend
pause
