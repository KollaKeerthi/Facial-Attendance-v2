@echo off
if not exist logs mkdir logs
.\.venv\Scripts\python.exe tools\run_camera_workers.py --config config\cameras.json %*
