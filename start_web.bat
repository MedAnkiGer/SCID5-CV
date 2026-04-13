@echo off
cd /d "%~dp0"
echo Starting SCID-5-CV Web UI on http://localhost:8000
python -m uvicorn app:app --reload --port 8000
pause
