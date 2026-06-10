"""
PostgreSQL attendance logger used by the recognition workers.

The dashboard and workers share the same tables. Recognition keeps writing
locally from the main laptop so it can process OBS streams continuously without
depending on HTTP calls between local services.
"""

import logging as log
import os
import time
from datetime import datetime

import cv2
import psycopg
from psycopg.rows import dict_row


SCHEMA_POSTGRES = [
    # ── companies (new) ───────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS companies (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",

    # ── employees ─────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS employees (
        id SERIAL PRIMARY KEY,
        employee_code TEXT,
        name TEXT NOT NULL,
        department TEXT,
        role TEXT,
        gallery_label TEXT NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",

    # ── cameras ───────────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS cameras (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
        stream_url TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        last_heartbeat TIMESTAMPTZ,
        last_frame_at TIMESTAMPTZ,
        fps DOUBLE PRECISION,
        inference_ms DOUBLE PRECISION,
        api_queue_size INTEGER,
        reconnect_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'unknown',
        status_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    # idempotent column additions for cameras (existing databases)
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS last_heartbeat TIMESTAMPTZ",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS last_frame_at TIMESTAMPTZ",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS fps DOUBLE PRECISION",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS inference_ms DOUBLE PRECISION",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS api_queue_size INTEGER",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS reconnect_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS status_message TEXT",

    # ── attendance_sessions ───────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS attendance_sessions (
        id SERIAL PRIMARY KEY,
        employee_id INTEGER NOT NULL REFERENCES employees(id),
        attendance_date DATE NOT NULL,
        in_time TIMESTAMPTZ,
        out_time TIMESTAMPTZ,
        duration_seconds INTEGER,
        status TEXT NOT NULL DEFAULT 'outside'
            CHECK (status IN ('inside', 'outside')),
        in_event_id INTEGER,
        out_event_id INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (employee_id, attendance_date)
    )""",

    # ── attendance_events ─────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS attendance_events (
        id SERIAL PRIMARY KEY,
        employee_id INTEGER REFERENCES employees(id),
        camera_id INTEGER REFERENCES cameras(id),
        direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
        event_type TEXT NOT NULL DEFAULT 'recognized'
            CHECK (event_type IN ('recognized', 'duplicate', 'unmatched_exit')),
        event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        confidence DOUBLE PRECISION,
        snapshot_path TEXT,
        needs_verification BOOLEAN NOT NULL DEFAULT FALSE,
        verification_reason TEXT
    )""",

    # ── spoof_attempts ────────────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS spoof_attempts (
        id SERIAL PRIMARY KEY,
        camera_id INTEGER REFERENCES cameras(id),
        event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        snapshot_path TEXT
    )""",

    # ── attendance_users (new — separate from any existing users table) ────────
    """CREATE TABLE IF NOT EXISTS attendance_users (
        id SERIAL PRIMARY KEY,
        company_id INTEGER REFERENCES companies(id),
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'employee',
        employee_id INTEGER REFERENCES employees(id),
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "ALTER TABLE attendance_users ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)",
    "ALTER TABLE attendance_users ADD COLUMN IF NOT EXISTS employee_id INTEGER REFERENCES employees(id)",
    "ALTER TABLE attendance_users ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE",

    # ── multi-tenant: add company_id to all business tables ───────────────────
    "ALTER TABLE employees ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)",
    "ALTER TABLE cameras ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)",
    "ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)",
    "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)",
    "ALTER TABLE spoof_attempts ADD COLUMN IF NOT EXISTS company_id INTEGER REFERENCES companies(id)",

    # ── approval workflow columns on attendance_events ────────────────────────
    "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS approval_status TEXT DEFAULT 'approved'",
    "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS approved_by INTEGER",
    "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ",
    "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS approval_note TEXT",
    "ALTER TABLE attendance_events ADD COLUMN IF NOT EXISTS corrected_employee_id INTEGER REFERENCES employees(id)",

    "ALTER TABLE employees DROP CONSTRAINT IF EXISTS employees_employee_code_key",
    "ALTER TABLE employees DROP CONSTRAINT IF EXISTS employees_gallery_label_key",
    "ALTER TABLE cameras DROP CONSTRAINT IF EXISTS cameras_name_key",

    # ── indexes ───────────────────────────────────────────────────────────────
    "CREATE INDEX IF NOT EXISTS idx_sessions_date ON attendance_sessions (attendance_date)",
    "CREATE INDEX IF NOT EXISTS idx_events_time ON attendance_events (event_time)",
    "CREATE INDEX IF NOT EXISTS idx_spoof_time ON spoof_attempts (event_time)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_company ON attendance_sessions (company_id, attendance_date)",
    "CREATE INDEX IF NOT EXISTS idx_events_company ON attendance_events (company_id, event_time)",
    "CREATE INDEX IF NOT EXISTS idx_employees_company ON employees (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_approvals ON attendance_events (company_id, approval_status) WHERE needs_verification = TRUE",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_company_gallery_label ON employees (company_id, gallery_label)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_company_employee_code ON employees (company_id, employee_code) WHERE employee_code IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_cameras_company_name ON cameras (company_id, name)",
]


class AttendanceLogger:
    def __init__(
        self,
        db_url,
        snapshot_dir,
        direction='in',
        camera_name=None,
        stream_url=None,
        verification_confidence=0.65,
        spoof_rate_limit_s=5.0,
        company_id=1,
    ):
        if direction not in ('in', 'out'):
            raise ValueError("--direction must be either 'in' or 'out'")

        self.direction = direction
        self.camera_name = camera_name or f'{direction}-camera'
        self.snapshot_dir = snapshot_dir
        self.event_dir = os.path.join(snapshot_dir, direction)
        self.spoof_dir = os.path.join(snapshot_dir, 'spoof')
        self.verification_confidence = verification_confidence
        self.company_id = company_id
        self._last_spoof_at = 0.0
        self._spoof_rate_limit_s = spoof_rate_limit_s

        os.makedirs(self.event_dir, exist_ok=True)
        os.makedirs(self.spoof_dir, exist_ok=True)

        self.conn = psycopg.connect(db_url, row_factory=dict_row, connect_timeout=5)
        self.conn.autocommit = False
        self._ensure_schema()
        self.camera_id = self._ensure_camera(self.camera_name, direction, stream_url)

    def _ensure_schema(self):
        with self.conn.cursor() as cur:
            for stmt in SCHEMA_POSTGRES:
                cur.execute(stmt)
        self.conn.commit()

    def _ensure_camera(self, name, direction, stream_url):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cameras (name, direction, stream_url, company_id)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (company_id, name) DO UPDATE
                   SET direction = EXCLUDED.direction,
                       stream_url = COALESCE(EXCLUDED.stream_url, cameras.stream_url),
                       active = TRUE
                   RETURNING id""",
                (name, direction, stream_url, self.company_id),
            )
            camera_id = cur.fetchone()['id']
        self.conn.commit()
        return camera_id

    def _ensure_employee(self, gallery_label):
        label = str(gallery_label).strip()
        if not label:
            label = 'Unknown'
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO employees (name, gallery_label, company_id)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (company_id, gallery_label) DO UPDATE
                   SET name = COALESCE(NULLIF(employees.name, ''), EXCLUDED.name)
                   RETURNING id, name""",
                (label, label, self.company_id),
            )
            employee = cur.fetchone()
        self.conn.commit()
        return employee

    def _crop_face(self, frame, roi):
        x1 = max(int(roi.position[0]), 0)
        y1 = max(int(roi.position[1]), 0)
        x2 = min(int(roi.position[0] + roi.size[0]), frame.shape[1])
        y2 = min(int(roi.position[1] + roi.size[1]), frame.shape[0])
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    def _write_snapshot(self, frame, roi, folder, prefix):
        now = datetime.now().astimezone()
        path = os.path.join(
            folder,
            f'{prefix}_{now.strftime("%Y-%m-%d_%H-%M-%S-%f")}.jpg',
        )
        crop = self._crop_face(frame, roi)
        if crop is not None:
            cv2.imwrite(path, crop)
            return path
        return None

    def mark(self, name, confidence, frame, roi):
        """Record one IN or OUT event and update today's attendance session.

        Returns (changed, HH:MM:SS). changed is True only when the event modifies
        the daily session (approved events only). Low-confidence events are flagged
        pending and do not modify sessions until HR approves them.
        """
        employee = self._ensure_employee(name)
        employee_id = employee['id']
        now = datetime.now().astimezone()
        today = now.date()
        time_str = now.strftime('%H:%M:%S')
        snapshot_path = self._write_snapshot(
            frame, roi, self.event_dir, f'{self.direction}_{name}')
        needs_verification = confidence < self.verification_confidence
        approval_status = 'pending' if needs_verification else 'approved'
        event_type = 'recognized'
        verification_reason = 'low_confidence' if needs_verification else None
        changed = False

        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO attendance_events
                   (employee_id, camera_id, direction, event_type, event_time,
                    confidence, snapshot_path, needs_verification, verification_reason,
                    approval_status, company_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    employee_id,
                    self.camera_id,
                    self.direction,
                    event_type,
                    now,
                    float(confidence),
                    snapshot_path,
                    needs_verification,
                    verification_reason,
                    approval_status,
                    self.company_id,
                ),
            )
            event_id = cur.fetchone()['id']

            # Only approved events (high-confidence) modify sessions immediately.
            # Pending events wait for HR approval before affecting sessions.
            if not needs_verification:
                if self.direction == 'in':
                    cur.execute(
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
                        (employee_id, today, now, event_id, self.company_id),
                    )
                    changed = cur.fetchone() is not None
                else:
                    cur.execute(
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
                    )
                    changed = cur.fetchone() is not None
                    if not changed:
                        cur.execute(
                            """UPDATE attendance_events
                               SET event_type = 'unmatched_exit',
                                   needs_verification = TRUE,
                                   verification_reason = 'exit_without_open_entry',
                                   approval_status = 'pending'
                               WHERE id = %s""",
                            (event_id,),
                        )

        self.conn.commit()

        if changed:
            log.info('Attendance %s: %s at %s', self.direction.upper(), name, time_str)
        return changed, time_str

    def log_spoof(self, frame, roi):
        now_mono = time.monotonic()
        if now_mono - self._last_spoof_at < self._spoof_rate_limit_s:
            return False
        self._last_spoof_at = now_mono

        now = datetime.now()
        snapshot_path = self._write_snapshot(frame, roi, self.spoof_dir, 'spoof')
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO spoof_attempts (camera_id, event_time, snapshot_path, company_id)
                   VALUES (%s, %s, %s, %s)""",
                (self.camera_id, now, snapshot_path, self.company_id),
            )
        self.conn.commit()
        log.warning('Spoof attempt logged on %s camera at %s', self.direction, now)
        return True

    def close(self):
        self.conn.close()
