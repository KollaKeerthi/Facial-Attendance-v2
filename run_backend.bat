@echo off
if not exist logs mkdir logs
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --log-level info 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath logs\backend.log -Append"
