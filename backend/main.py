import base64
import os
from datetime import date, datetime, timedelta
from typing import Optional

import cv2
import numpy as np
import psycopg
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.auth import (
    CurrentUser,
    create_token,
    get_user,
    hash_password,
    roles,
    verify_password,
)
from backend.db import PROJECT_ROOT, connect, init_db, seconds_to_hhmm, snapshot_url

WORKER_SECRET = os.getenv('WORKER_SECRET', '')

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


# ─── Pydantic Models ──────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    email: str
    password: str


class CompanyIn(BaseModel):
    name: str = Field(..., min_length=1)
    slug: str = Field(..., min_length=1)
    active: bool = True


class CompanyPatch(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    active: Optional[bool] = None


class UserIn(BaseModel):
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=6)
    role: str
    employee_id: Optional[int] = None
    active: bool = True


class UserPatch(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    employee_id: Optional[int] = None
    active: Optional[bool] = None


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


class CameraIn(BaseModel):
    name: str = Field(..., min_length=1)
    direction: str
    stream_url: Optional[str] = None
    active: bool = True


class CameraPatch(BaseModel):
    name: Optional[str] = None
    direction: Optional[str] = None
    stream_url: Optional[str] = None
    active: Optional[bool] = None


class RecognitionEvent(BaseModel):
    name: str
    confidence: float
    direction: str
    camera_name: str
    stream_url: Optional[str] = None
    verification_confidence: float = 0.65
    snapshot_base64: Optional[str] = None
    company_id: Optional[int] = None


class SpoofEvent(BaseModel):
    direction: str
    camera_name: str
    stream_url: Optional[str] = None
    snapshot_base64: Optional[str] = None
    company_id: Optional[int] = None


class CameraHeartbeat(BaseModel):
    name: str = Field(..., min_length=1)
    direction: str
    stream_url: Optional[str] = None
    fps: Optional[float] = None
    inference_ms: Optional[float] = None
    api_queue_size: Optional[int] = None
    reconnect_count: int = 0
    last_frame_at: Optional[datetime] = None
    status: str = 'online'
    status_message: Optional[str] = None
    company_id: Optional[int] = None


class ApproveIn(BaseModel):
    corrected_employee_id: Optional[int] = None
    note: Optional[str] = None


class RejectIn(BaseModel):
    note: Optional[str] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _default_company_id(conn) -> int:
    row = conn.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()
    return row['id'] if row else 1


def _resolve_company(event_company_id: Optional[int], conn) -> int:
    if event_company_id:
        return event_company_id
    return _default_company_id(conn)


def save_snapshot(kind, direction, name, snapshot_base64):
    if not snapshot_base64:
        return None
    folder = snapshots_dir / kind / direction
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = ''.join(ch for ch in name if ch.isalnum() or ch in ('-', '_')) or kind
    target = folder / f'{safe_name}_{datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")}.jpg'
    target.write_bytes(base64.b64decode(snapshot_base64))
    return str(target)


def ensure_camera(conn, name, direction, stream_url, company_id):
    return conn.execute(
        """INSERT INTO cameras (name, direction, stream_url, company_id)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (company_id, name) DO UPDATE
           SET direction = EXCLUDED.direction,
               stream_url = COALESCE(EXCLUDED.stream_url, cameras.stream_url),
               active = TRUE
           RETURNING id""",
        (name, direction, stream_url, company_id),
    ).fetchone()['id']


def ensure_employee(conn, gallery_label, company_id):
    label = gallery_label.strip() or 'Unknown'
    return conn.execute(
        """INSERT INTO employees (name, gallery_label, company_id)
           VALUES (%s, %s, %s)
           ON CONFLICT (company_id, gallery_label) DO UPDATE
           SET name = COALESCE(NULLIF(employees.name, ''), EXCLUDED.name)
           RETURNING id, name""",
        (label, label, company_id),
    ).fetchone()


def ensure_employee_in_company(conn, employee_id: Optional[int], company_id: int):
    if employee_id is None:
        return
    row = conn.execute(
        'SELECT id FROM employees WHERE id = %s AND company_id = %s',
        (employee_id, company_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail='Employee does not belong to this company')


def validate_employee_image(data):
    arr = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail='Uploaded image could not be decoded')
    height, width = image.shape[:2]
    if width < 160 or height < 160:
        raise HTTPException(status_code=400, detail='Face image must be at least 160x160 pixels')
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if cv2.Laplacian(gray, cv2.CV_64F).var() < 35.0:
        raise HTTPException(status_code=400, detail='Face image is too blurry')
    return image


def _apply_approved_event(conn, event: dict, effective_employee_id: int, approver_id: int,
                           note: Optional[str], corrected_id: Optional[int]):
    """Update event status and apply session changes for an approved event."""
    conn.execute(
        """UPDATE attendance_events
           SET approval_status = 'approved',
               approved_by = %s,
               approved_at = NOW(),
               approval_note = %s,
               corrected_employee_id = %s
           WHERE id = %s""",
        (approver_id, note, corrected_id, event['id']),
    )
    event_time = event['event_time']
    event_date = event_time.date() if hasattr(event_time, 'date') else event_time
    company_id = event.get('company_id')

    if event['direction'] == 'in':
        conn.execute(
            """INSERT INTO attendance_sessions
               (employee_id, attendance_date, in_time, status, in_event_id, company_id)
               VALUES (%s, %s, %s, 'inside', %s, %s)
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
               WHERE attendance_sessions.in_time IS NULL""",
            (effective_employee_id, event_date, event_time, event['id'], company_id),
        )
    else:
        conn.execute(
            """UPDATE attendance_sessions
               SET out_time = %s,
                   out_event_id = %s,
                   duration_seconds = GREATEST(0, EXTRACT(EPOCH FROM (%s - in_time))::INTEGER),
                   status = 'outside',
                   updated_at = NOW()
               WHERE employee_id = %s
                 AND attendance_date = %s
                 AND in_time IS NOT NULL
                 AND out_time IS NULL""",
            (event_time, event['id'], event_time, effective_employee_id, event_date),
        )


# ─── Lifecycle ────────────────────────────────────────────────────────────────

@app.on_event('startup')
def startup():
    init_db()


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get('/api/health')
def health():
    return {'ok': True}


# ─── Authentication ───────────────────────────────────────────────────────────

@app.post('/api/auth/login')
def login(body: LoginIn):
    with connect() as conn:
        user = conn.execute(
            """SELECT id, company_id, email, password_hash, role, active
               FROM attendance_users WHERE email = %s""",
            (body.email.lower().strip(),),
        ).fetchone()
    if not user or not verify_password(body.password, user['password_hash']):
        raise HTTPException(status_code=401, detail='Invalid email or password')
    if not user['active']:
        raise HTTPException(status_code=403, detail='Account is disabled')
    token = create_token(user['id'], user['email'], user['role'], user['company_id'])
    return {
        'token': token,
        'user': {
            'id': user['id'],
            'email': user['email'],
            'role': user['role'],
            'company_id': user['company_id'],
        },
    }


@app.post('/api/auth/logout')
def logout():
    return {'ok': True}


@app.get('/api/me')
def me(user: CurrentUser = Depends(get_user)):
    with connect() as conn:
        row = conn.execute(
            """SELECT u.id, u.company_id, u.email, u.role, u.employee_id, u.active, u.created_at,
                      c.name AS company_name
               FROM attendance_users u
               LEFT JOIN companies c ON c.id = u.company_id
               WHERE u.id = %s""",
            (user.id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='User not found')
    return row


# ─── Companies (platform_admin only) ─────────────────────────────────────────

@app.get('/api/companies')
def list_companies(user: CurrentUser = Depends(roles('platform_admin'))):
    with connect() as conn:
        return conn.execute(
            'SELECT id, name, slug, active, created_at FROM companies ORDER BY name'
        ).fetchall()


@app.post('/api/companies', status_code=201)
def create_company(body: CompanyIn, user: CurrentUser = Depends(roles('platform_admin'))):
    try:
        with connect() as conn:
            ensure_employee_in_company(conn, body.employee_id, company_id)
            row = conn.execute(
                """INSERT INTO companies (name, slug, active)
                   VALUES (%s, %s, %s)
                   RETURNING id, name, slug, active, created_at""",
                (body.name, body.slug.lower(), body.active),
            ).fetchone()
            conn.commit()
        return row
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail='Company slug already exists')


@app.patch('/api/companies/{company_id}')
def update_company(company_id: int, body: CompanyPatch,
                   user: CurrentUser = Depends(roles('platform_admin'))):
    fields = body.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail='No fields to update')
    allowed = ['name', 'slug', 'active']
    assignments, values = [], []
    for f in allowed:
        if f in fields:
            assignments.append(f'{f} = %s')
            values.append(fields[f])
    values.append(company_id)
    with connect() as conn:
        if 'employee_id' in fields:
            ensure_employee_in_company(conn, fields['employee_id'], company_id)
        row = conn.execute(
            f"UPDATE companies SET {', '.join(assignments)} WHERE id = %s RETURNING *",
            values,
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail='Company not found')
    return row


# ─── Users ────────────────────────────────────────────────────────────────────

VALID_ROLES = ('platform_admin', 'company_admin', 'hr', 'manager', 'employee')


@app.get('/api/users')
def list_users(company_ctx: Optional[int] = Query(None),
               user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        rows = conn.execute(
            """SELECT u.id, u.company_id, u.email, u.role, u.employee_id,
                      u.active, u.created_at, e.name AS employee_name
               FROM attendance_users u
               LEFT JOIN employees e ON e.id = u.employee_id
               WHERE u.company_id = %s
               ORDER BY u.role, u.email""",
            (company_id,),
        ).fetchall()
    return rows


@app.post('/api/users', status_code=201)
def create_user(body: UserIn, company_ctx: Optional[int] = Query(None),
                user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f'Invalid role. Must be one of: {", ".join(VALID_ROLES)}')
    # company_admin cannot create platform_admin
    if body.role == 'platform_admin' and not user.is_platform_admin:
        raise HTTPException(status_code=403, detail='Only platform admins can create platform admins')
    company_id = user.require_company(company_ctx)
    try:
        with connect() as conn:
            row = conn.execute(
                """INSERT INTO attendance_users (company_id, email, password_hash, role, employee_id, active)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, company_id, email, role, employee_id, active, created_at""",
                (company_id, body.email.lower().strip(), hash_password(body.password),
                 body.role, body.employee_id, body.active),
            ).fetchone()
            conn.commit()
        return row
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail='Email already exists')


@app.patch('/api/users/{user_id}')
def update_user(user_id: int, body: UserPatch, company_ctx: Optional[int] = Query(None),
                user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    fields = body.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail='No fields to update')
    if 'role' in fields and fields['role'] not in VALID_ROLES:
        raise HTTPException(status_code=400, detail='Invalid role')
    if 'password' in fields:
        fields['password_hash'] = hash_password(fields.pop('password'))
    allowed = ['email', 'password_hash', 'role', 'employee_id', 'active']
    assignments, values = [], []
    for f in allowed:
        if f in fields:
            assignments.append(f'{f} = %s')
            values.append(fields[f])
    if not assignments:
        raise HTTPException(status_code=400, detail='No valid fields')
    values.extend([user_id, company_id])
    with connect() as conn:
        row = conn.execute(
            f"""UPDATE attendance_users SET {', '.join(assignments)}
                WHERE id = %s AND company_id = %s
                RETURNING id, company_id, email, role, employee_id, active""",
            values,
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail='User not found')
    return row


@app.delete('/api/users/{user_id}')
def deactivate_user(user_id: int, company_ctx: Optional[int] = Query(None),
                    user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        row = conn.execute(
            'UPDATE attendance_users SET active = FALSE WHERE id = %s AND company_id = %s RETURNING id',
            (user_id, company_id),
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail='User not found')
    return {'ok': True}


# ─── Employees ────────────────────────────────────────────────────────────────

@app.get('/api/employees')
def list_employees(company_ctx: Optional[int] = Query(None),
                   user: CurrentUser = Depends(get_user)):
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, employee_code, name, department, role, gallery_label,
                      active, created_at
               FROM employees
               WHERE company_id = %s
               ORDER BY active DESC, name ASC""",
            (company_id,),
        ).fetchall()
    return rows


@app.post('/api/employees', status_code=201)
def create_employee(employee: EmployeeIn, company_ctx: Optional[int] = Query(None),
                    user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    try:
        with connect() as conn:
            row = conn.execute(
                """INSERT INTO employees
                   (company_id, employee_code, name, department, role, gallery_label, active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, employee_code, name, department, role,
                             gallery_label, active, created_at""",
                (company_id, employee.employee_code, employee.name, employee.department,
                 employee.role, employee.gallery_label, employee.active),
            ).fetchone()
            conn.commit()
        return row
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail='Employee code or gallery label already exists')


@app.patch('/api/employees/{employee_id}')
def update_employee(employee_id: int, patch: EmployeePatch,
                    company_ctx: Optional[int] = Query(None),
                    user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    fields = patch.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail='No fields to update')
    allowed = ['employee_code', 'name', 'department', 'role', 'gallery_label', 'active']
    assignments, values = [], []
    for f in allowed:
        if f in fields:
            assignments.append(f'{f} = %s')
            values.append(fields[f])
    values.extend([employee_id, company_id])
    try:
        with connect() as conn:
            row = conn.execute(
                f"""UPDATE employees
                    SET {', '.join(assignments)}
                    WHERE id = %s AND company_id = %s
                    RETURNING id, employee_code, name, department, role,
                              gallery_label, active, created_at""",
                values,
            ).fetchone()
            conn.commit()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail='Employee code or gallery label already exists')
    if not row:
        raise HTTPException(status_code=404, detail='Employee not found')
    return row


@app.delete('/api/employees/{employee_id}')
def delete_employee(employee_id: int, hard: bool = False,
                    company_ctx: Optional[int] = Query(None),
                    user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        if hard:
            row = conn.execute(
                'DELETE FROM employees WHERE id = %s AND company_id = %s RETURNING id',
                (employee_id, company_id),
            ).fetchone()
        else:
            row = conn.execute(
                'UPDATE employees SET active = FALSE WHERE id = %s AND company_id = %s RETURNING id',
                (employee_id, company_id),
            ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail='Employee not found')
    return {'ok': True}


@app.post('/api/employees/{employee_id}/image')
async def upload_employee_image(employee_id: int, file: UploadFile = File(...),
                                company_ctx: Optional[int] = Query(None),
                                user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    ext = (file.filename or '').split('.')[-1].lower()
    if ext not in {'jpg', 'jpeg', 'png'}:
        raise HTTPException(status_code=400, detail='Only JPG and PNG images are supported')
    with connect() as conn:
        employee = conn.execute(
            'SELECT id, gallery_label FROM employees WHERE id = %s AND company_id = %s',
            (employee_id, company_id),
        ).fetchone()
    if not employee:
        raise HTTPException(status_code=404, detail='Employee not found')
    target = gallery_dir / f'{employee["gallery_label"]}.{ext}'
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail='Uploaded image is empty')
    validate_employee_image(data)
    target.write_bytes(data)
    return {'ok': True, 'path': str(target)}


# ─── Cameras ──────────────────────────────────────────────────────────────────

@app.get('/api/cameras')
def list_cameras(company_ctx: Optional[int] = Query(None),
                 user: CurrentUser = Depends(get_user)):
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, name, direction, stream_url, active, created_at,
                      last_heartbeat, last_frame_at, fps, inference_ms,
                      api_queue_size, reconnect_count, status, status_message
               FROM cameras
               WHERE company_id = %s
               ORDER BY direction, name""",
            (company_id,),
        ).fetchall()
    return rows


@app.post('/api/cameras', status_code=201)
def create_camera(body: CameraIn, company_ctx: Optional[int] = Query(None),
                  user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    if body.direction not in ('in', 'out'):
        raise HTTPException(status_code=400, detail='direction must be in or out')
    company_id = user.require_company(company_ctx)
    try:
        with connect() as conn:
            row = conn.execute(
                """INSERT INTO cameras (company_id, name, direction, stream_url, active)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, name, direction, stream_url, active, created_at""",
                (company_id, body.name, body.direction, body.stream_url, body.active),
            ).fetchone()
            conn.commit()
        return row
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail='Camera name already exists')


@app.patch('/api/cameras/{camera_id}')
def update_camera(camera_id: int, body: CameraPatch,
                  company_ctx: Optional[int] = Query(None),
                  user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    fields = body.dict(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail='No fields to update')
    allowed = ['name', 'direction', 'stream_url', 'active']
    assignments, values = [], []
    for f in allowed:
        if f in fields:
            assignments.append(f'{f} = %s')
            values.append(fields[f])
    values.extend([camera_id, company_id])
    with connect() as conn:
        row = conn.execute(
            f"UPDATE cameras SET {', '.join(assignments)} WHERE id = %s AND company_id = %s RETURNING *",
            values,
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail='Camera not found')
    return row


@app.delete('/api/cameras/{camera_id}')
def delete_camera(camera_id: int, company_ctx: Optional[int] = Query(None),
                  user: CurrentUser = Depends(get_user)):
    if not user.can_manage_company():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        row = conn.execute(
            'DELETE FROM cameras WHERE id = %s AND company_id = %s RETURNING id',
            (camera_id, company_id),
        ).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(status_code=404, detail='Camera not found')
    return {'ok': True}


# ─── Worker Endpoints (no user auth — use WORKER_SECRET if configured) ────────

def _verify_worker(worker_secret: Optional[str] = None):
    if WORKER_SECRET and worker_secret != WORKER_SECRET:
        raise HTTPException(status_code=401, detail='Invalid worker secret')


@app.post('/api/recognition/mark')
def recognition_mark(event: RecognitionEvent,
                     x_worker_secret: Optional[str] = Header(None)):
    _verify_worker(x_worker_secret)
    if event.direction not in ('in', 'out'):
        raise HTTPException(status_code=400, detail='direction must be in or out')

    now = datetime.now().astimezone()
    today = now.date()
    time_str = now.strftime('%H:%M:%S')
    snapshot_path = save_snapshot('events', event.direction, event.name, event.snapshot_base64)
    needs_verification = event.confidence < event.verification_confidence
    approval_status = 'pending' if needs_verification else 'approved'
    verification_reason = 'low_confidence' if needs_verification else None
    changed = False

    with connect() as conn:
        company_id = _resolve_company(event.company_id, conn)
        camera_id = ensure_camera(conn, event.camera_name, event.direction, event.stream_url, company_id)
        employee = ensure_employee(conn, event.name, company_id)
        employee_id = employee['id']
        event_id = conn.execute(
            """INSERT INTO attendance_events
               (employee_id, camera_id, direction, event_type, event_time,
                confidence, snapshot_path, needs_verification, verification_reason,
                approval_status, company_id)
               VALUES (%s, %s, %s, 'recognized', %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (employee_id, camera_id, event.direction, now, event.confidence,
             snapshot_path, needs_verification, verification_reason, approval_status, company_id),
        ).fetchone()['id']

        # Only approved (high-confidence) events update sessions immediately
        if not needs_verification:
            if event.direction == 'in':
                row = conn.execute(
                    """INSERT INTO attendance_sessions
                       (employee_id, attendance_date, in_time, status, in_event_id, company_id)
                       VALUES (%s, %s, %s, 'inside', %s, %s)
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
                    (employee_id, today, now, event_id, company_id),
                ).fetchone()
                changed = row is not None
            else:
                row = conn.execute(
                    """UPDATE attendance_sessions
                       SET out_time = %s,
                           out_event_id = %s,
                           duration_seconds = GREATEST(
                               0, EXTRACT(EPOCH FROM (%s - in_time))::INTEGER
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
                               verification_reason = 'exit_without_open_entry',
                               approval_status = 'pending'
                           WHERE id = %s""",
                        (event_id,),
                    )
        conn.commit()

    return {'ok': True, 'changed': changed, 'time': time_str}


@app.post('/api/recognition/spoof')
def recognition_spoof(event: SpoofEvent,
                      x_worker_secret: Optional[str] = Header(None)):
    _verify_worker(x_worker_secret)
    if event.direction not in ('in', 'out'):
        raise HTTPException(status_code=400, detail='direction must be in or out')
    now = datetime.now().astimezone()
    snapshot_path = save_snapshot('spoof', event.direction, 'spoof', event.snapshot_base64)
    with connect() as conn:
        company_id = _resolve_company(event.company_id, conn)
        camera_id = ensure_camera(conn, event.camera_name, event.direction, event.stream_url, company_id)
        conn.execute(
            'INSERT INTO spoof_attempts (camera_id, event_time, snapshot_path, company_id) VALUES (%s, %s, %s, %s)',
            (camera_id, now, snapshot_path, company_id),
        )
        conn.commit()
    return {'ok': True}


@app.post('/api/cameras/heartbeat')
def camera_heartbeat(event: CameraHeartbeat,
                     x_worker_secret: Optional[str] = Header(None)):
    _verify_worker(x_worker_secret)
    if event.direction not in ('in', 'out'):
        raise HTTPException(status_code=400, detail='direction must be in or out')
    with connect() as conn:
        company_id = _resolve_company(event.company_id, conn)
        conn.execute(
            """INSERT INTO cameras
               (name, direction, stream_url, active, last_heartbeat, last_frame_at,
                fps, inference_ms, api_queue_size, reconnect_count, status, status_message, company_id)
               VALUES (%s, %s, %s, TRUE, NOW(), %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (company_id, name) DO UPDATE
               SET direction = EXCLUDED.direction,
                   stream_url = COALESCE(EXCLUDED.stream_url, cameras.stream_url),
                   active = TRUE,
                   last_heartbeat = NOW(),
                   last_frame_at = EXCLUDED.last_frame_at,
                   fps = EXCLUDED.fps,
                   inference_ms = EXCLUDED.inference_ms,
                   api_queue_size = EXCLUDED.api_queue_size,
                   reconnect_count = EXCLUDED.reconnect_count,
                   status = EXCLUDED.status,
                   status_message = EXCLUDED.status_message""",
            (event.name, event.direction, event.stream_url, event.last_frame_at,
             event.fps, event.inference_ms, event.api_queue_size, event.reconnect_count,
             event.status, event.status_message, company_id),
        )
        conn.commit()
    return {'ok': True}


# ─── Attendance ───────────────────────────────────────────────────────────────

@app.get('/api/attendance')
def get_attendance(
    day: Optional[date] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    department: Optional[str] = Query(None),
    employee_search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    company_ctx: Optional[int] = Query(None),
    user: CurrentUser = Depends(get_user),
):
    if not user.can_view_all_employees():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    target_day = day or (end_date or date.today())
    if start_date is None:
        start_date = target_day

    conditions = ['e.active = TRUE', 'e.company_id = %s']
    params: list = [company_id]

    if department:
        conditions.append('e.department ILIKE %s')
        params.append(f'%{department}%')
    if employee_search:
        conditions.append('(e.name ILIKE %s OR e.employee_code ILIKE %s)')
        params.extend([f'%{employee_search}%', f'%{employee_search}%'])

    where = ' AND '.join(conditions)

    with connect() as conn:
        rows = conn.execute(
            f"""SELECT e.id AS employee_id,
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
                       in_ev.confidence AS in_confidence,
                       in_ev.approval_status AS in_approval_status,
                       in_ev.snapshot_path AS in_snapshot_path,
                       out_ev.snapshot_path AS out_snapshot_path
                FROM employees e
                LEFT JOIN attendance_sessions s
                  ON s.employee_id = e.id
                  AND s.attendance_date BETWEEN %s AND %s
                LEFT JOIN attendance_events in_ev ON in_ev.id = s.in_event_id
                LEFT JOIN attendance_events out_ev ON out_ev.id = s.out_event_id
                WHERE {where}
                ORDER BY
                  CASE WHEN s.status = 'inside' THEN 0
                       WHEN s.in_time IS NOT NULL THEN 1
                       ELSE 2 END,
                  e.name ASC""",
            [start_date, target_day] + params,
        ).fetchall()

    result = []
    for row in rows:
        if status_filter and row['status'] != status_filter and not (status_filter == 'not_arrived' and row['status'] is None):
            continue
        row['duration_hhmm'] = seconds_to_hhmm(row['duration_seconds'])
        row['in_snapshot_url'] = snapshot_url(row.pop('in_snapshot_path'))
        row['out_snapshot_url'] = snapshot_url(row.pop('out_snapshot_path'))
        result.append(row)

    return {'start_date': start_date, 'end_date': target_day, 'rows': result}


@app.get('/api/attendance/me')
def my_attendance(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    user: CurrentUser = Depends(get_user),
):
    with connect() as conn:
        u = conn.execute(
            'SELECT employee_id FROM attendance_users WHERE id = %s', (user.id,)
        ).fetchone()
    if not u or not u['employee_id']:
        raise HTTPException(status_code=400, detail='No employee profile linked to your account')
    employee_id = u['employee_id']

    today = date.today()
    e_date = end_date or today
    s_date = start_date or (today - timedelta(days=30))

    with connect() as conn:
        sessions = conn.execute(
            """SELECT s.attendance_date, s.in_time, s.out_time, s.duration_seconds, s.status,
                      in_ev.confidence AS in_confidence, in_ev.approval_status AS in_approval_status,
                      in_ev.snapshot_path AS in_snapshot_path
               FROM attendance_sessions s
               LEFT JOIN attendance_events in_ev ON in_ev.id = s.in_event_id
               WHERE s.employee_id = %s
                 AND s.attendance_date BETWEEN %s AND %s
               ORDER BY s.attendance_date DESC""",
            (employee_id, s_date, e_date),
        ).fetchall()

        pending_events = conn.execute(
            """SELECT id, direction, event_time, confidence, approval_status,
                      snapshot_path, verification_reason
               FROM attendance_events
               WHERE employee_id = %s
                 AND needs_verification = TRUE
                 AND DATE(event_time) BETWEEN %s AND %s
               ORDER BY event_time DESC""",
            (employee_id, s_date, e_date),
        ).fetchall()

    for s in sessions:
        s['duration_hhmm'] = seconds_to_hhmm(s['duration_seconds'])
        s['in_snapshot_url'] = snapshot_url(s.pop('in_snapshot_path'))
    for ev in pending_events:
        ev['snapshot_url'] = snapshot_url(ev.pop('snapshot_path'))

    return {'sessions': sessions, 'pending_events': pending_events}


# ─── Approvals ────────────────────────────────────────────────────────────────

@app.get('/api/approvals')
def list_approvals(
    company_ctx: Optional[int] = Query(None),
    user: CurrentUser = Depends(get_user),
):
    if not user.can_approve():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        rows = conn.execute(
            """SELECT ev.id,
                      ev.direction,
                      ev.event_type,
                      ev.event_time,
                      ev.confidence,
                      ev.snapshot_path,
                      ev.needs_verification,
                      ev.verification_reason,
                      ev.approval_status,
                      ev.approval_note,
                      ev.corrected_employee_id,
                      e.name AS employee_name,
                      e.employee_code,
                      e.department,
                      c.name AS camera_name
               FROM attendance_events ev
               LEFT JOIN employees e ON e.id = ev.employee_id
               LEFT JOIN cameras c ON c.id = ev.camera_id
               WHERE ev.company_id = %s
                 AND ev.needs_verification = TRUE
                 AND ev.approval_status = 'pending'
               ORDER BY ev.event_time DESC
               LIMIT 200""",
            (company_id,),
        ).fetchall()
    for row in rows:
        row['snapshot_url'] = snapshot_url(row.pop('snapshot_path'))
    return rows


@app.post('/api/approvals/{event_id}/approve')
def approve_event(event_id: int, body: ApproveIn,
                  company_ctx: Optional[int] = Query(None),
                  user: CurrentUser = Depends(get_user)):
    if not user.can_approve():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        event = conn.execute(
            """SELECT id, company_id, employee_id, direction, event_time, approval_status
               FROM attendance_events WHERE id = %s""",
            (event_id,),
        ).fetchone()
        if not event:
            raise HTTPException(status_code=404, detail='Event not found')
        if event['company_id'] != company_id:
            raise HTTPException(status_code=403, detail='Forbidden')
        if event['approval_status'] != 'pending':
            raise HTTPException(status_code=400, detail='Event is not pending approval')

        effective_employee_id = body.corrected_employee_id or event['employee_id']
        if not effective_employee_id:
            raise HTTPException(status_code=400, detail='Cannot approve: no employee associated')
        ensure_employee_in_company(conn, effective_employee_id, company_id)

        _apply_approved_event(conn, dict(event), effective_employee_id, user.id,
                               body.note, body.corrected_employee_id)
        conn.commit()
    return {'ok': True}


@app.post('/api/approvals/{event_id}/reject')
def reject_event(event_id: int, body: RejectIn,
                 company_ctx: Optional[int] = Query(None),
                 user: CurrentUser = Depends(get_user)):
    if not user.can_approve():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        event = conn.execute(
            'SELECT id, company_id, approval_status FROM attendance_events WHERE id = %s',
            (event_id,),
        ).fetchone()
        if not event:
            raise HTTPException(status_code=404, detail='Event not found')
        if event['company_id'] != company_id:
            raise HTTPException(status_code=403, detail='Forbidden')
        if event['approval_status'] != 'pending':
            raise HTTPException(status_code=400, detail='Event is not pending')
        conn.execute(
            """UPDATE attendance_events
               SET approval_status = 'rejected', approved_by = %s,
                   approved_at = NOW(), approval_note = %s
               WHERE id = %s""",
            (user.id, body.note, event_id),
        )
        conn.commit()
    return {'ok': True}


# ─── Analytics ────────────────────────────────────────────────────────────────

@app.get('/api/analytics/daily')
def daily_analytics(
    day: Optional[date] = Query(None),
    department: Optional[str] = Query(None),
    company_ctx: Optional[int] = Query(None),
    user: CurrentUser = Depends(get_user),
):
    if not user.can_view_all_employees():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)
    target_day = day or date.today()

    dept_filter = 'AND e.department ILIKE %s' if department else ''
    dept_params = [f'%{department}%'] if department else []

    with connect() as conn:
        total = conn.execute(
            f'SELECT COUNT(*) AS n FROM employees e WHERE e.company_id = %s AND e.active = TRUE {dept_filter}',
            [company_id] + dept_params,
        ).fetchone()['n']

        session_stats = conn.execute(
            f"""SELECT
                COUNT(DISTINCT s.employee_id) FILTER (WHERE s.in_time IS NOT NULL) AS present,
                COUNT(DISTINCT s.employee_id) FILTER (WHERE s.status = 'inside') AS inside,
                COUNT(DISTINCT s.employee_id) FILTER (WHERE s.in_time IS NOT NULL AND s.out_time IS NOT NULL) AS completed,
                COUNT(DISTINCT s.employee_id) FILTER (WHERE s.in_time IS NOT NULL AND s.out_time IS NULL) AS missing_clock_out
            FROM attendance_sessions s
            JOIN employees e ON e.id = s.employee_id
            WHERE s.company_id = %s AND s.attendance_date = %s AND e.active = TRUE {dept_filter}""",
            [company_id, target_day] + dept_params,
        ).fetchone()

        pending = conn.execute(
            f"""SELECT COUNT(*) AS n
               FROM attendance_events ev
               JOIN employees e ON e.id = ev.employee_id
               WHERE ev.company_id = %s AND DATE(ev.event_time) = %s
                 AND ev.needs_verification = TRUE AND ev.approval_status = 'pending'
                 {dept_filter}""",
            [company_id, target_day] + dept_params,
        ).fetchone()['n']

    present = session_stats['present'] or 0
    return {
        'date': target_day,
        'total_employees': total,
        'present': present,
        'absent': total - present,
        'inside': session_stats['inside'] or 0,
        'completed': session_stats['completed'] or 0,
        'missing_clock_out': session_stats['missing_clock_out'] or 0,
        'pending_approval': pending,
    }


@app.get('/api/analytics/weekly')
def weekly_analytics(
    start_date: Optional[date] = Query(None),
    department: Optional[str] = Query(None),
    employee_id: Optional[int] = Query(None),
    company_ctx: Optional[int] = Query(None),
    user: CurrentUser = Depends(get_user),
):
    if not user.can_view_all_employees():
        raise HTTPException(status_code=403, detail='Forbidden')
    company_id = user.require_company(company_ctx)

    today = date.today()
    week_start = start_date or (today - timedelta(days=today.weekday()))
    week_end = week_start + timedelta(days=6)

    extra_filter = ''
    extra_params: list = []
    if department:
        extra_filter += ' AND e.department ILIKE %s'
        extra_params.append(f'%{department}%')
    if employee_id:
        extra_filter += ' AND e.id = %s'
        extra_params.append(employee_id)

    with connect() as conn:
        daily_rows = conn.execute(
            f"""SELECT
                s.attendance_date,
                COUNT(DISTINCT s.employee_id) FILTER (WHERE s.in_time IS NOT NULL) AS present,
                COUNT(DISTINCT s.employee_id) FILTER (WHERE s.status = 'inside') AS inside,
                COUNT(DISTINCT s.employee_id) FILTER (WHERE s.in_time IS NOT NULL AND s.out_time IS NOT NULL) AS completed,
                COUNT(DISTINCT s.employee_id) FILTER (WHERE s.in_time IS NOT NULL AND s.out_time IS NULL) AS missing_clock_out,
                COALESCE(SUM(s.duration_seconds) FILTER (WHERE s.duration_seconds IS NOT NULL), 0) AS total_seconds
            FROM attendance_sessions s
            JOIN employees e ON e.id = s.employee_id
            WHERE s.company_id = %s
              AND s.attendance_date BETWEEN %s AND %s
              AND e.active = TRUE
              {extra_filter}
            GROUP BY s.attendance_date
            ORDER BY s.attendance_date""",
            [company_id, week_start, week_end] + extra_params,
        ).fetchall()

        late_arrivals = conn.execute(
            f"""SELECT COUNT(DISTINCT s.employee_id) AS n
               FROM attendance_sessions s
               JOIN employees e ON e.id = s.employee_id
               WHERE s.company_id = %s
                 AND s.attendance_date BETWEEN %s AND %s
                 AND s.in_time IS NOT NULL
                 AND (s.in_time AT TIME ZONE 'UTC')::time > '09:00:00'
                 {extra_filter}""",
            [company_id, week_start, week_end] + extra_params,
        ).fetchone()['n']

        pending_approvals = conn.execute(
            f"""SELECT COUNT(*) AS n
               FROM attendance_events ev
               JOIN employees e ON e.id = ev.employee_id
               WHERE ev.company_id = %s
                 AND DATE(ev.event_time) BETWEEN %s AND %s
                 AND ev.needs_verification = TRUE
                 AND ev.approval_status = 'pending'
                 {extra_filter}""",
            [company_id, week_start, week_end] + extra_params,
        ).fetchone()['n']

    daily_map = {str(r['attendance_date']): r for r in daily_rows}
    trend = []
    total_seconds = 0
    total_days_with_data = 0
    total_missing = 0

    for i in range(7):
        d = week_start + timedelta(days=i)
        key = str(d)
        row = daily_map.get(key, {})
        day_seconds = int(row.get('total_seconds', 0) or 0)
        total_seconds += day_seconds
        present = int(row.get('present', 0) or 0)
        if present > 0:
            total_days_with_data += 1
        missing = int(row.get('missing_clock_out', 0) or 0)
        total_missing += missing
        trend.append({
            'date': key,
            'present': present,
            'inside': int(row.get('inside', 0) or 0),
            'completed': int(row.get('completed', 0) or 0),
            'missing_clock_out': missing,
            'total_hours': round(day_seconds / 3600, 1),
        })

    avg_hours = round(total_seconds / 3600 / max(total_days_with_data, 1), 1)

    return {
        'week_start': week_start,
        'week_end': week_end,
        'total_hours': round(total_seconds / 3600, 1),
        'avg_hours_per_day': avg_hours,
        'late_arrivals': late_arrivals,
        'missing_clock_outs': total_missing,
        'pending_approvals': pending_approvals,
        'trend': trend,
    }


# ─── Legacy / Backward-Compatible Endpoints ───────────────────────────────────

@app.get('/api/attendance/today')
def today_attendance(day: Optional[date] = Query(None),
                     company_ctx: Optional[int] = Query(None),
                     user: CurrentUser = Depends(get_user)):
    target_day = day or date.today()
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.id AS employee_id, e.employee_code, e.name, e.gallery_label,
                      e.department, e.role, s.attendance_date,
                      s.in_time, s.out_time, s.duration_seconds, s.status,
                      in_ev.snapshot_path AS in_snapshot_path,
                      out_ev.snapshot_path AS out_snapshot_path
               FROM employees e
               LEFT JOIN attendance_sessions s
                 ON s.employee_id = e.id AND s.attendance_date = %s
               LEFT JOIN attendance_events in_ev ON in_ev.id = s.in_event_id
               LEFT JOIN attendance_events out_ev ON out_ev.id = s.out_event_id
               WHERE e.active = TRUE AND e.company_id = %s
               ORDER BY
                 CASE WHEN s.status = 'inside' THEN 0
                      WHEN s.in_time IS NOT NULL THEN 1
                      ELSE 2 END,
                 e.name ASC""",
            (target_day, company_id),
        ).fetchall()
    for row in rows:
        row['duration_hhmm'] = seconds_to_hhmm(row['duration_seconds'])
        row['in_snapshot_url'] = snapshot_url(row.pop('in_snapshot_path'))
        row['out_snapshot_url'] = snapshot_url(row.pop('out_snapshot_path'))
    return {'date': target_day, 'rows': rows}


@app.get('/api/events/today')
def today_events(day: Optional[date] = Query(None),
                 verification_only: bool = False,
                 company_ctx: Optional[int] = Query(None),
                 user: CurrentUser = Depends(get_user)):
    target_day = day or date.today()
    company_id = user.require_company(company_ctx)
    where = 'ev.company_id = %s AND DATE(ev.event_time) = %s'
    params = [company_id, target_day]
    if verification_only:
        where += ' AND ev.needs_verification = TRUE'
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT ev.id, ev.direction, ev.event_type, ev.event_time, ev.confidence,
                       ev.snapshot_path, ev.needs_verification, ev.verification_reason,
                       ev.approval_status, e.name, e.employee_code, c.name AS camera_name
                FROM attendance_events ev
                LEFT JOIN employees e ON e.id = ev.employee_id
                LEFT JOIN cameras c ON c.id = ev.camera_id
                WHERE {where}
                ORDER BY ev.event_time DESC LIMIT 200""",
            params,
        ).fetchall()
    for row in rows:
        row['snapshot_url'] = snapshot_url(row.pop('snapshot_path'))
    return {'date': target_day, 'rows': rows}


@app.get('/api/spoof-attempts/today')
def today_spoofs(day: Optional[date] = Query(None),
                 company_ctx: Optional[int] = Query(None),
                 user: CurrentUser = Depends(get_user)):
    target_day = day or date.today()
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        rows = conn.execute(
            """SELECT sp.id, sp.event_time, sp.snapshot_path,
                      c.name AS camera_name, c.direction
               FROM spoof_attempts sp
               LEFT JOIN cameras c ON c.id = sp.camera_id
               WHERE sp.company_id = %s AND DATE(sp.event_time) = %s
               ORDER BY sp.event_time DESC LIMIT 100""",
            (company_id, target_day),
        ).fetchall()
    for row in rows:
        row['snapshot_url'] = snapshot_url(row.pop('snapshot_path'))
    return {'date': target_day, 'rows': rows}


@app.post('/api/admin/clear-data')
def clear_attendance_data(scope: str = Query('today', pattern='^(today|all)$'),
                          company_ctx: Optional[int] = Query(None),
                          user: CurrentUser = Depends(roles('platform_admin', 'company_admin'))):
    company_id = user.require_company(company_ctx)
    with connect() as conn:
        if scope == 'today':
            target_day = date.today()
            conn.execute(
                'DELETE FROM attendance_events WHERE company_id = %s AND DATE(event_time) = %s',
                (company_id, target_day),
            )
            conn.execute(
                'DELETE FROM spoof_attempts WHERE company_id = %s AND DATE(event_time) = %s',
                (company_id, target_day),
            )
            conn.execute(
                'DELETE FROM attendance_sessions WHERE company_id = %s AND attendance_date = %s',
                (company_id, target_day),
            )
        else:
            conn.execute('DELETE FROM attendance_events WHERE company_id = %s', (company_id,))
            conn.execute('DELETE FROM spoof_attempts WHERE company_id = %s', (company_id,))
            conn.execute('DELETE FROM attendance_sessions WHERE company_id = %s', (company_id,))
        conn.commit()
    return {'ok': True, 'scope': scope}
