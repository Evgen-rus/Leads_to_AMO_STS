@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" amo_browser.py --open-browser
if errorlevel 1 pause
