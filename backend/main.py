import base64
from datetime import date, datetime
from typing import Optional

import psycopg
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.db import PROJECT_ROOT, connect, init_db, seconds_to_hhmm, snapshot_url


app = FastAPI(title='Face Attendance API')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

snapshots_dir = PROJECT_ROOT / 'logs' / 'snapshots'
snapshots_dir.mkdir(parents=True, exist_ok=True)
app.mount('/snapshots', StaticFiles(directory=str(snapshots_dir)), name='snapshots')
gallery_dir = PROJECT_ROOT / 'my_gallery' / 'my_gallery'
gallery_dir.mkdir(parents=True, exist_ok=True)


class EmployeeIn(BaseModel):
    employee_code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    department: Optional[str] = None
    role: Optional[str] = None
    gallery_label: str = Field(..., min_length=1)
    active: bool = True


class EmployeePatch(BaseModel):
    employee_code: Optional[str] = None
    name: Optional[str] = None
    department: Optional[str] = None
    role: Optional[str] = None
    gallery_label: Optional[str] = None
    active: Optional[bool] = None


class RecognitionEvent(BaseModel):
    name: str
    confidence: float
    direction: str
    camera_name: str
    stream_url: Optional[str] = None
    verification_confidence: float = 0.65
    snapshot_base64: Optional[str] = None


class SpoofEvent(BaseModel):
    direction: str
    camera_name: str
    stream_url: Optional[str] = None
    snapshot_base64: Optional[str] = None


def save_snapshot(kind, direction, name, snapshot_base64):
    if not snapshot_base64:
        return None
    folder = snapshots_dir / kind / direction
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = ''.join(ch for ch in name if ch.isalnum() or ch in ('-', '_')) or kind
    target = folder / f'{safe_name}_{datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")}.jpg'
    target.write_bytes(base64.b64decode(snapshot_base64))
    return str(target)


def ensure_camera(conn, name, direction, stream_url):
    return conn.execute(
        """INSERT INTO cameras (name, direction, stream_url)
           VALUES (%s, %s, %s)
           ON CONFLICT (name) DO UPDATE
           SET direction = EXCLUDED.direction,
               stream_url = COALESCE(EXCLUDED.stream_url, cameras.stream_url),
               active = TRUE
           RETURNING id""",
        (name, direction, stream_url),
    ).fetchone()['id']


def ensure_employee(conn, gallery_label):
    label = gallery_label.strip() or 'Unknown'
    return conn.execute(
        """INSERT INTO employees (name, gallery_label)
           VALUES (%s, %s)
           ON CONFLICT (gallery_label) DO UPDATE
           SET name = COALESCE(NULLIF(employees.name, ''), EXCLUDED.name)
           RETURNING id, name""",
        (label, label),
    ).fetchone()


@app.on_event('startup')
def startup():
    init_db()


@app.get('/api/health')
def health():
    return {'ok': True}


@app.post('/api/recognition/mark')
def recognition_mark(event: RecognitionEvent):
    if event.direction not in ('in', 'out'):
        raise HTTPException(status_code=400, detail='direction must be in or out')

    now = datetime.now().astimezone()
    today = now.date()
    time_str = now.strftime('%H:%M:%S')
    snapshot_path = save_snapshot('events', event.direction, event.name, event.snapshot_base64)
    needs_verification = event.confidence < event.verification_confidence
    verification_reason = 'low_confidence' if needs_verification else None
    changed = False

    with connect() as conn:
        camera_id = ensure_camera(conn, event.camera_name, event.direction, event.stream_url)
        employee = ensure_employee(conn, event.name)
        employee_id = employee['id']
        event_id = conn.execute(
            """INSERT INTO attendance_events
               (employee_id, camera_id, direction, event_type, event_time,
                confidence, snapshot_path, needs_verification, verification_reason)
               VALUES (%s, %s, %s, 'recognized', %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                employee_id,
                camera_id,
                event.direction,
                now,
                event.confidence,
                snapshot_path,
                needs_verification,
                verification_reason,
            ),
        ).fetchone()['id']

        if event.direction == 'in':
            row = conn.execute(
                """INSERT INTO attendance_sessions
                   (employee_id, attendance_date, in_time, status, in_event_id)
                   VALUES (%s, %s, %s, 'inside', %s)
                   ON CONFLICT (employee_id, attendance_date) DO UPDATE
                   SET in_time = CASE
                           WHEN attendance_sessions.in_time IS NULL
                           THEN EXCLUDED.in_time
                           ELSE attendance_sessions.in_time
                       END,
                       in_event_id = CASE
                           WHEN attendance_sessions.in_event_id IS NULL
                           THEN EXCLUDED.in_event_id
                           ELSE attendance_sessions.in_event_id
                       END,
                       status = CASE
                           WHEN attendance_sessions.in_time IS NULL
                           THEN 'inside'
                           ELSE attendance_sessions.status
                       END,
                       updated_at = NOW()
                   WHERE attendance_sessions.in_time IS NULL
                   RETURNING id""",
                (employee_id, today, now, event_id),
            ).fetchone()
            changed = row is not None
        else:
            row = conn.execute(
                """UPDATE attendance_sessions
                   SET out_time = %s,
                       out_event_id = %s,
                       duration_seconds = GREATEST(
                           0,
                           EXTRACT(EPOCH FROM (%s - in_time))::INTEGER
                       ),
                       status = 'outside',
                       updated_at = NOW()
                   WHERE employee_id = %s
                     AND attendance_date = %s
                     AND in_time IS NOT NULL
                     AND out_time IS NULL
                   RETURNING id""",
                (now, event_id, now, employee_id, today),
            ).fetchone()
            changed = row is not None
            if not changed:
                conn.execute(
                    """UPDATE attendance_events
                       SET event_type = 'unmatched_exit',
                           needs_verification = TRUE,
                           verification_reason = 'exit_without_open_entry'
                       WHERE id = %s""",
                    (event_id,),
                )
        conn.commit()

    return {'ok': True, 'changed': changed, 'time': time_str}


@app.post('/api/recognition/spoof')
def recognition_spoof(event: SpoofEvent):
    if event.direction not in ('in', 'out'):
        raise HTTPException(status_code=400, detail='direction must be in or out')

    now = datetime.now().astimezone()
    snapshot_path = save_snapshot('spoof', event.direction, 'spoof', event.snapshot_base64)
    with connect() as conn:
        camera_id = ensure_camera(conn, event.camera_name, event.direction, event.stream_url)
        conn.execute(
            """INSERT INTO spoof_attempts (camera_id, event_time, snapshot_path)
               VALUES (%s, %s, %s)""",
            (camera_id, now, snapshot_path),
        )
        conn.commit()
    return {'ok': True}


@app.get('/api/employees')
def list_employees():
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, employee_code, name, department, role, gallery_label,
                      active, created_at
               FROM employees
               ORDER BY active DESC, name ASC"""
        ).fetchall()
    return rows


@app.post('/api/employees', status_code=201)
def create_employee(employee: EmployeeIn):
    try:
        with connect() as conn:
            row = conn.execute(
                """INSERT INTO employees
                   (employee_code, name, department, role, gallery_label, active)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, employee_code, name, department, role,
                             gallery_label, active, created_at""",
                (
                    employee.employee_code,
                    employee.name,
                    employee.department,
                    employee.role,
                    employee.gallery_label,
                    employee.active,
                ),
            ).fetchone()
            conn.commit()
        return row
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail='Employee code or gallery label already exists') from exc


@app.patch('/api/employees/{employee_id}')
def update_employee(employee_id: int, patch: EmployeePatch):
    fields = patch.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail='No fields to update')

    allowed = ['employee_code', 'name', 'department', 'role', 'gallery_label', 'active']
    assignments = []
    values = []
    for field in allowed:
        if field in fields:
            assignments.append(f'{field} = %s')
            values.append(fields[field])
    values.append(employee_id)

    try:
        with connect() as conn:
            row = conn.execute(
                f"""UPDATE employees
                    SET {', '.join(assignments)}
                    WHERE id = %s
                    RETURNING id, employee_code, name, department, role,
                              gallery_label, active, created_at""",
                values,
            ).fetchone()
            conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail='Employee code or gallery label already exists') from exc

    if row is None:
        raise HTTPException(status_code=404, detail='Employee not found')
    return row


@app.delete('/api/employees/{employee_id}')
def delete_employee(employee_id: int, hard: bool = False):
    with connect() as conn:
        if hard:
            row = conn.execute(
                'DELETE FROM employees WHERE id = %s RETURNING id',
                (employee_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """UPDATE employees
                   SET active = FALSE
                   WHERE id = %s
                   RETURNING id""",
                (employee_id,),
            ).fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=404, detail='Employee not found')
    return {'ok': True}


@app.post('/api/employees/{employee_id}/image')
async def upload_employee_image(employee_id: int, file: UploadFile = File(...)):
    ext = Path(file.filename or '').suffix.lower()
    if ext not in {'.jpg', '.jpeg', '.png'}:
        raise HTTPException(status_code=400, detail='Only JPG and PNG images are supported')

    with connect() as conn:
        employee = conn.execute(
            'SELECT id, gallery_label FROM employees WHERE id = %s',
            (employee_id,),
        ).fetchone()
    if employee is None:
        raise HTTPException(status_code=404, detail='Employee not found')

    target = gallery_dir / f'{employee["gallery_label"]}{ext}'
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail='Uploaded image is empty')
    target.write_bytes(data)
    return {'ok': True, 'path': str(target)}


@app.get('/api/attendance/today')
def today_attendance(day: Optional[date] = None):
    target_day = day or date.today()
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.id AS employee_id,
                      e.employee_code,
                      e.name,
                      e.gallery_label,
                      e.department,
                      e.role,
                      s.attendance_date,
                      s.in_time,
                      s.out_time,
                      s.duration_seconds,
                      s.status,
                      in_ev.snapshot_path AS in_snapshot_path,
                      out_ev.snapshot_path AS out_snapshot_path
               FROM employees e
               LEFT JOIN attendance_sessions s
                 ON s.employee_id = e.id AND s.attendance_date = %s
               LEFT JOIN attendance_events in_ev ON in_ev.id = s.in_event_id
               LEFT JOIN attendance_events out_ev ON out_ev.id = s.out_event_id
               WHERE e.active = TRUE
               ORDER BY
                 CASE WHEN s.status = 'inside' THEN 0
                      WHEN s.in_time IS NOT NULL THEN 1
                      ELSE 2 END,
                 e.name ASC""",
            (target_day,),
        ).fetchall()

    for row in rows:
        row['duration_hhmm'] = seconds_to_hhmm(row['duration_seconds'])
        row['in_snapshot_url'] = snapshot_url(row.pop('in_snapshot_path'))
        row['out_snapshot_url'] = snapshot_url(row.pop('out_snapshot_path'))
    return {'date': target_day, 'rows': rows}


@app.get('/api/events/today')
def today_events(day: Optional[date] = None, verification_only: bool = False):
    target_day = day or date.today()
    where = 'DATE(ev.event_time) = %s'
    params = [target_day]
    if verification_only:
        where += ' AND ev.needs_verification = TRUE'

    with connect() as conn:
        rows = conn.execute(
            f"""SELECT ev.id,
                       ev.direction,
                       ev.event_type,
                       ev.event_time,
                       ev.confidence,
                       ev.snapshot_path,
                       ev.needs_verification,
                       ev.verification_reason,
                       e.name,
                       e.employee_code,
                       c.name AS camera_name
                FROM attendance_events ev
                LEFT JOIN employees e ON e.id = ev.employee_id
                LEFT JOIN cameras c ON c.id = ev.camera_id
                WHERE {where}
                ORDER BY ev.event_time DESC
                LIMIT 200""",
            params,
        ).fetchall()

    for row in rows:
        row['snapshot_url'] = snapshot_url(row.pop('snapshot_path'))
    return {'date': target_day, 'rows': rows}


@app.get('/api/spoof-attempts/today')
def today_spoofs(day: Optional[date] = None):
    target_day = day or date.today()
    with connect() as conn:
        rows = conn.execute(
            """SELECT sp.id,
                      sp.event_time,
                      sp.snapshot_path,
                      c.name AS camera_name,
                      c.direction
               FROM spoof_attempts sp
               LEFT JOIN cameras c ON c.id = sp.camera_id
               WHERE DATE(sp.event_time) = %s
               ORDER BY sp.event_time DESC
               LIMIT 100""",
            (target_day,),
        ).fetchall()

    for row in rows:
        row['snapshot_url'] = snapshot_url(row.pop('snapshot_path'))
    return {'date': target_day, 'rows': rows}


@app.get('/api/cameras')
def list_cameras():
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, name, direction, stream_url, active, created_at
               FROM cameras
               ORDER BY direction, name"""
        ).fetchall()
    return rows


@app.post('/api/admin/clear-data')
def clear_attendance_data(scope: str = Query('today', pattern='^(today|all)$')):
    with connect() as conn:
        if scope == 'today':
            target_day = date.today()
            conn.execute(
                """DELETE FROM attendance_events
                   WHERE DATE(event_time) = %s""",
                (target_day,),
            )
            conn.execute(
                """DELETE FROM spoof_attempts
                   WHERE DATE(event_time) = %s""",
                (target_day,),
            )
            conn.execute(
                """DELETE FROM attendance_sessions
                   WHERE attendance_date = %s""",
                (target_day,),
            )
        else:
            conn.execute('DELETE FROM attendance_events')
            conn.execute('DELETE FROM spoof_attempts')
            conn.execute('DELETE FROM attendance_sessions')
            conn.execute('DELETE FROM cameras')
            conn.execute('DELETE FROM employees')
        conn.commit()
    return {'ok': True, 'scope': scope}
