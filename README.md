# Facial Attendance v2

A local face-recognition attendance system for tracking employee entry and exit events from camera streams. The project uses OpenVINO models for face detection, landmarks, face re-identification, and optional anti-spoofing; FastAPI for the backend API; PostgreSQL or Neon for storage; and a React dashboard for attendance monitoring and employee management.

## Features

- Real-time face recognition using OpenVINO models.
- Separate entry and exit camera workers.
- Automatic IN and OUT attendance session tracking.
- Employee management from the dashboard.
- Face image upload for employee gallery enrollment.
- HR verification view for low-confidence matches, unmatched exits, and spoof attempts.
- Optional OBS plus MediaMTX streaming setup for multiple laptops on the same Wi-Fi.
- Local PostgreSQL fallback or hosted Neon PostgreSQL support.

## Project Structure

```text
app/                    OpenVINO recognition worker and attendance client
backend/                FastAPI application and database setup
dashboard/              React/Vite dashboard
models/                 OpenVINO model files
my_gallery/my_gallery/  Employee face images used for recognition
logs/                   Runtime logs and captured snapshots
tools/                  Helper scripts
mediamtx-bin/           Local MediaMTX executable
```

## Requirements

- Windows
- Python 3.10 or newer
- Node.js and npm
- PostgreSQL, or a Neon PostgreSQL connection string
- Webcam, RTMP/RTSP camera stream, or OBS stream
- OpenVINO model files in the `models/` folder

Python packages are listed in `requirements.txt`. Dashboard packages are listed in `dashboard/package.json`.

## Setup

1. Create and activate a Python virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

2. Install dashboard dependencies:

```powershell
cd dashboard
npm install
cd ..
```

3. Configure the database.

Create a `.env` file in the project root, or set `DATABASE_URL` in Windows. For Neon:

```text
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

For local PostgreSQL, the app falls back to:

```text
postgresql://postgres:postgres@localhost:5432/attendance
```

The backend creates the required tables automatically when it starts.

## Running Locally

Open separate terminals from the project root and run:

```powershell
.\run_backend.bat
.\run_dashboard.bat
```

Then open:

```text
Dashboard: http://localhost:5173
FastAPI docs: http://localhost:8000/docs
Health check: http://localhost:8000/api/health
```

To run recognition from the default webcam instead of the full streaming setup:

```powershell
.\run.bat
```

## Entry and Exit Camera Streaming

For a two-camera attendance flow, this project can receive RTMP streams through MediaMTX. Start MediaMTX on the main laptop:

```powershell
.\run_mediamtx.bat
```

On the entry and exit camera laptops, configure OBS:

```text
Service: Custom
Entry server: rtmp://MAIN_LAPTOP_IP:1935/live
Entry stream key: entry
Exit server: rtmp://MAIN_LAPTOP_IP:1935/live
Exit stream key: exit
```

Recommended OBS settings:

```text
Resolution: 640x360
FPS: 5 or 10
Bitrate: 800-1200 kbps
Audio: Disabled
```

The main laptop reads these streams:

```text
rtmp://127.0.0.1:1935/live/entry
rtmp://127.0.0.1:1935/live/exit
```

Start the recognition workers in two more terminals:

```powershell
.\run_entry_worker.bat
.\run_exit_worker.bat
```

For a four-camera CCTV or OBS pilot, edit `config\cameras.json`, start
MediaMTX, and run:

```powershell
.\run_camera_workers.bat
```

To pre-compute the face gallery descriptors and make worker startup faster:

```powershell
.\run_enroll.bat
```

To start the local backend, dashboard, MediaMTX, and four configured workers in
one command:

```powershell
.\run_all.bat
```

OBS test laptops can publish to:

```text
rtmp://MAIN_LAPTOP_IP:1935/live/entry-1
rtmp://MAIN_LAPTOP_IP:1935/live/entry-2
rtmp://MAIN_LAPTOP_IP:1935/live/exit-1
rtmp://MAIN_LAPTOP_IP:1935/live/exit-2
```

For CCTV/NVR deployments, prefer each camera's low-latency substream RTSP URL
at 720p and 8-10 FPS, and place those URLs in `config\cameras.json`.

This version uses PostgreSQL/Neon, not SQLite, so SQLite WAL mode is not needed.
Concurrent worker writes go through FastAPI/PostgreSQL.

## Employee Enrollment

1. Open the dashboard.
2. Go to `Employees`.
3. Add the employee details.
4. Set `Gallery Label` to match the face image filename without the extension.
5. Upload a JPG or PNG face image.
6. Restart the recognition workers so the gallery is rebuilt.

Example:

```text
Image file: my_gallery\my_gallery\Keerthi.jpg
Gallery Label: Keerthi
```

Use clear, front-facing images for best recognition accuracy.

## Attendance Logic

- Entry worker records `in_time`.
- Exit worker records `out_time`.
- Duration is calculated from `out_time - in_time`.
- Duplicate entry detections are ignored after the first open IN session.
- Exit without an open IN session is marked for HR verification.
- Low-confidence recognitions are marked for HR verification.
- Spoof events are shown in the verification page when anti-spoofing is enabled.

## Useful Scripts

```text
run_backend.bat                 Start FastAPI backend on port 8000
run_dashboard.bat               Start React dashboard on port 5173
run_mediamtx.bat                Start MediaMTX streaming server
run_entry_worker.bat            Start entry recognition worker
run_exit_worker.bat             Start exit recognition worker
run_entry_worker_preview.bat    Start entry worker with preview window
run_exit_worker_preview.bat     Start exit worker with preview window
watch_logs.bat                  Watch backend and worker logs
set_camera_urls_example.bat     Example camera URL environment setup
```

## Logs and Snapshots

Runtime logs are written to:

```text
logs/backend.log
logs/entry_worker.log
logs/exit_worker.log
```

Snapshots for events and spoof attempts are stored under:

```text
logs/snapshots/
```

## Troubleshooting

- If the dashboard cannot load data, make sure `run_backend.bat` is running and visit `http://localhost:8000/api/health`.
- If database connection fails, verify `DATABASE_URL` or start local PostgreSQL with database `attendance`.
- If recognition does not identify an employee, check that the `Gallery Label` matches the image filename exactly.
- If new face images are not recognized, restart the entry and exit workers.
- If camera streams do not connect, start MediaMTX first and confirm OBS is streaming to the correct `MAIN_LAPTOP_IP`.
- If logs are needed, run `watch_logs.bat` from the project root.

## Deployment Notes

For a detailed three-laptop local deployment workflow, see `LOCAL_DEPLOYMENT.md`.
