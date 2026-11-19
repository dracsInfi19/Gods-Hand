@echo off
cd /d "%~dp0"
echo Starting Gods Hand dashboard...
python -m sosmesh.simulator > nul
start "" http://localhost:8000
cd dashboard
python -m http.server 8000
pause
