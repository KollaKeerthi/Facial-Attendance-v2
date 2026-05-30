"""
Print a PostgreSQL attendance roster for a given date.

Usage:
    python tools/attendance_report.py
    python tools/attendance_report.py 2026-05-28
    python tools/attendance_report.py --spoof
"""

import argparse
import os
from datetime import date

import psycopg
from psycopg.rows import dict_row


DEFAULT_DB_URL = 'postgresql://postgres:postgres@localhost:5432/attendance'


def hhmm(seconds):
    if seconds is None:
        return '-'
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}'


p = argparse.ArgumentParser(description=__doc__)
p.add_argument('day', nargs='?', default=date.today().isoformat(),
               help='Date in YYYY-MM-DD format. Defaults to today.')
p.add_argument('--db-url', default=os.getenv('DATABASE_URL', DEFAULT_DB_URL),
               help='PostgreSQL connection URL.')
p.add_argument('--spoof', action='store_true',
               help='Show spoof attempts instead of attendance.')
args = p.parse_args()

with psycopg.connect(args.db_url, row_factory=dict_row) as conn:
    if args.spoof:
        rows = conn.execute(
            """SELECT sp.event_time, sp.snapshot_path, c.name AS camera_name
               FROM spoof_attempts sp
               LEFT JOIN cameras c ON c.id = sp.camera_id
               WHERE DATE(sp.event_time) = %s
               ORDER BY sp.event_time""",
            (args.day,),
        ).fetchall()
        print(f'Spoof attempts on {args.day}: {len(rows)}')
        print('-' * 76)
        for row in rows:
            print(f'{row["event_time"]:%H:%M:%S}  {row["camera_name"] or "-":<16} {row["snapshot_path"] or "-"}')
    else:
        rows = conn.execute(
            """SELECT e.employee_code, e.name, s.in_time, s.out_time,
                      s.duration_seconds, COALESCE(s.status, 'not-arrived') AS status
               FROM employees e
               LEFT JOIN attendance_sessions s
                 ON s.employee_id = e.id AND s.attendance_date = %s
               WHERE e.active = TRUE
               ORDER BY e.name""",
            (args.day,),
        ).fetchall()
        print(f'Attendance on {args.day}: {len(rows)} employees')
        print('-' * 86)
        print(f'{"ID":<12} {"Name":<24} {"In":<10} {"Out":<10} {"Spent":<8} Status')
        print('-' * 86)
        for row in rows:
            in_time = row['in_time'].strftime('%H:%M:%S') if row['in_time'] else '-'
            out_time = row['out_time'].strftime('%H:%M:%S') if row['out_time'] else '-'
            print(
                f'{row["employee_code"] or "-":<12} '
                f'{row["name"]:<24} '
                f'{in_time:<10} '
                f'{out_time:<10} '
                f'{hhmm(row["duration_seconds"]):<8} '
                f'{row["status"]}'
            )
