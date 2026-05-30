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
