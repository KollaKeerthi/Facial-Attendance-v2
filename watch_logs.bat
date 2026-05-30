@echo off
if not exist logs mkdir logs
if not exist logs\backend.log type nul > logs\backend.log
if not exist logs\entry_worker.log type nul > logs\entry_worker.log
if not exist logs\exit_worker.log type nul > logs\exit_worker.log
powershell -NoProfile -Command "Get-Content logs\backend.log,logs\entry_worker.log,logs\exit_worker.log -Wait -Tail 30"
