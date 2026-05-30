# Local Three-Laptop Deployment

## Architecture

```text
Entry laptop OBS  ->  Main laptop MediaMTX  ->  IN recognition worker
Exit laptop OBS   ->  Main laptop MediaMTX  ->  OUT recognition worker

Recognition workers -> PostgreSQL or Neon -> FastAPI -> React dashboard
```

Everything stays on the same Wi-Fi. The camera laptops only publish video. The
main laptop receives both RTMP streams, runs OpenVINO recognition, stores
attendance in PostgreSQL/Neon through FastAPI, and serves the dashboard.

## Main laptop setup

1. Choose a database.

Local PostgreSQL fallback:

```text
postgresql://postgres:postgres@localhost:5432/attendance
```

Neon:

```powershell
setx DATABASE_URL "postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"
```

After `setx`, open a new terminal so Windows loads the new environment variable.
Do not commit the real Neon URL; `.env` is ignored and `.env.example` only shows
the format.

Or create a local `.env` file in the project root:

```text
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

2. Install Python dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

3. Install dashboard dependencies:

```powershell
cd dashboard
npm install
cd ..
```

4. Download MediaMTX for Windows and run it from the project folder with:

```powershell
.\run_mediamtx.bat
```

## OBS setup on camera laptops

Both camera laptops must be on the same Wi-Fi as the main laptop.

In OBS:

1. Add the laptop webcam as a video source.
2. Set output to a light stream:

```text
Resolution: 640x360
FPS: 5 or 10
Bitrate: 800-1200
Audio: Disabled
```

3. Open Settings -> Stream.
4. Set Service to Custom.
5. For the entry laptop, use:

```text
Server: rtmp://MAIN_LAPTOP_IP:1935/live
Stream Key: entry
```

6. For the exit laptop, use:

```text
Server: rtmp://MAIN_LAPTOP_IP:1935/live
Stream Key: exit
```

7. Click Start Streaming on both laptops.

The main laptop reads:

```text
rtmp://127.0.0.1:1935/live/entry
rtmp://127.0.0.1:1935/live/exit
```

## Run the system

Open separate terminals on the main laptop:

```powershell
.\run_backend.bat
.\run_dashboard.bat
.\run_mediamtx.bat
```

The run scripts use `DATABASE_URL` from the environment or `.env`. If neither is
set, the Python code falls back to local PostgreSQL.

Dashboard:

```text
http://MAIN_LAPTOP_IP:5173
```

FastAPI:

```text
http://MAIN_LAPTOP_IP:8000/docs
```

Then start the recognition workers in two more terminals:

```powershell
.\run_entry_worker.bat
.\run_exit_worker.bat
```

## Employee setup

Add employees from the dashboard. The `Gallery Label` must match the filename
in `my_gallery\my_gallery` without the extension.

Example:

```text
my_gallery\my_gallery\Keerthi.jpg
Gallery Label: Keerthi
```

## Attendance behavior

- Entry stream records `in_time`.
- Exit stream records `out_time`.
- Duration is calculated from `out_time - in_time`.
- Duplicate entry frames are ignored after the first IN.
- Exit without an open IN is flagged for HR verification.
- Low-confidence events are flagged for HR verification.
- Spoof attempts are shown on the verification page.
