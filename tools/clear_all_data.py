"""Clear all attendance demo data from the configured PostgreSQL database."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db import connect


TABLES = [
    'attendance_events',
    'spoof_attempts',
    'attendance_sessions',
    'cameras',
    'employees',
]


def main():
    with connect() as conn:
        for table in TABLES:
            conn.execute(f'DELETE FROM {table}')
        conn.commit()
    print('Cleared all employees, cameras, attendance sessions, events, and spoof attempts.')


if __name__ == '__main__':
    main()
