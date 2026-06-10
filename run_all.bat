@echo off
if not exist logs mkdir logs
start "Attendance Backend" cmd /k ".\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8010 --reload --log-level info"
start "Attendance Dashboard" cmd /k "cd /d dashboard && set ""VITE_API_BASE=http://localhost:8010"" && npm.cmd run dev -- --host 127.0.0.1 --port 5175"
start "MediaMTX" cmd /k ".\run_mediamtx.bat"
start "Camera Workers" cmd /k ".\run_camera_workers.bat"
echo Started backend, dashboard, MediaMTX, and camera workers.
echo Dashboard: http://127.0.0.1:5175
