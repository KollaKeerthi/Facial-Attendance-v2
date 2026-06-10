import os
import socket
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

from app.attendance import SCHEMA_POSTGRES


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / '.env')
DEFAULT_DATABASE_URL = 'postgresql://postgres:postgres@localhost:5432/attendance'

DEFAULT_ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'admin@example.com')
DEFAULT_ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
DEFAULT_COMPANY_NAME = os.getenv('DEFAULT_COMPANY_NAME', 'Default Company')


def database_url():
    return os.getenv('DATABASE_URL', DEFAULT_DATABASE_URL)


def ipv4_hostaddr(conninfo):
    host = urlparse(conninfo).hostname
    if not host or host in ('localhost', '127.0.0.1'):
        return None
    try:
        infos = socket.getaddrinfo(host, 5432, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return None
    return infos[0][4][0] if infos else None


def connect():
    conninfo = database_url()
    hostaddr = ipv4_hostaddr(conninfo)
    kwargs = {
        'row_factory': dict_row,
        'connect_timeout': 20,
    }
    if hostaddr:
        kwargs['hostaddr'] = hostaddr
    return psycopg.connect(conninfo, **kwargs)


def init_db():
    with connect() as conn:
        with conn.cursor() as cur:
            for stmt in SCHEMA_POSTGRES:
                cur.execute(stmt)
        conn.commit()
        seed_defaults(conn)


def seed_defaults(conn):
    """Create default company, migrate orphan records, and create first admin.

    Safe to call on every startup — all operations are idempotent.
    Returns the default company id.
    """
    from backend.auth import hash_password  # deferred to avoid import cycle at module load

    # Create default company if none exists
    company = conn.execute('SELECT id FROM companies LIMIT 1').fetchone()
    if not company:
        company = conn.execute(
            """INSERT INTO companies (name, slug)
               VALUES (%s, %s)
               ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
               RETURNING id""",
            (DEFAULT_COMPANY_NAME, 'default'),
        ).fetchone()
    company_id = company['id']

    # Assign orphan records (NULL company_id) to the default company
    for table in ('employees', 'cameras', 'attendance_sessions', 'attendance_events', 'spoof_attempts'):
        conn.execute(
            f'UPDATE {table} SET company_id = %s WHERE company_id IS NULL',
            (company_id,),
        )

    # Backfill approval_status for old events that pre-date the column
    conn.execute(
        "UPDATE attendance_events SET approval_status = 'approved' WHERE approval_status IS NULL"
    )

    # Create platform_admin if no attendance_users exist at all
    existing = conn.execute('SELECT id FROM attendance_users LIMIT 1').fetchone()
    if not existing:
        conn.execute(
            """INSERT INTO attendance_users (company_id, email, password_hash, role)
               VALUES (%s, %s, %s, 'platform_admin')
               ON CONFLICT (email) DO NOTHING""",
            (company_id, DEFAULT_ADMIN_EMAIL, hash_password(DEFAULT_ADMIN_PASSWORD)),
        )

    conn.commit()
    return company_id


def snapshot_url(path):
    if not path:
        return None
    normalized = Path(path)
    try:
        rel = normalized.resolve().relative_to((PROJECT_ROOT / 'logs' / 'snapshots').resolve())
    except (ValueError, OSError):
        rel = Path(path)
    return '/snapshots/' + str(rel).replace('\\', '/')


def seconds_to_hhmm(seconds):
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f'{hours:02d}:{minutes:02d}'


def today():
    return date.today()
