"""Seed demo companies, employees, and dashboard users.

This script is idempotent: it updates existing rows with the same company slug,
employee code, gallery label, or user email instead of creating duplicates.
It does not start cameras or recognition workers.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.auth import hash_password
from backend.db import connect, init_db


DEFAULT_PASSWORD = 'demo123'

COMPANIES = [
    {
        'name': 'Vayam',
        'slug': 'vayam',
        'employees': [
            ('VAY-001', 'Ananya Rao', 'Leadership', 'CEO', 'vayam_ceo', 'company_admin', 'ceo@vayam.com'),
            ('VAY-002', 'Priya Menon', 'People Operations', 'HR', 'vayam_hr', 'hr', 'hr@vayam.com'),
            ('VAY-003', 'Rahul Sharma', 'Operations', 'Manager', 'vayam_manager', 'manager', 'manager@vayam.com'),
            ('VAY-004', 'Arjun Nair', 'Engineering', 'Tech Guy', 'vayam_tech', 'employee', 'tech@vayam.com'),
        ],
    },
    {
        'name': 'GetSet',
        'slug': 'getset',
        'employees': [
            ('GET-001', 'Neha Kapoor', 'Leadership', 'CEO', 'getset_ceo', 'company_admin', 'ceo@getset.com'),
            ('GET-002', 'Meera Iyer', 'People Operations', 'HR', 'getset_hr', 'hr', 'hr@getset.com'),
            ('GET-003', 'Karan Malhotra', 'Engineering', 'Manager', 'getset_manager', 'manager', 'manager@getset.com'),
            ('GET-004', 'Aditi Singh', 'Engineering', 'Software Developer', 'getset_dev_1', 'employee', 'dev1@getset.com'),
            ('GET-005', 'Rohan Gupta', 'Engineering', 'Software Developer', 'getset_dev_2', 'employee', 'dev2@getset.com'),
            ('GET-006', 'Sneha Reddy', 'Engineering', 'Software Developer', 'getset_dev_3', 'employee', 'dev3@getset.com'),
            ('GET-007', 'Vikram Joshi', 'Engineering', 'Software Developer', 'getset_dev_4', 'employee', 'dev4@getset.com'),
            ('GET-008', 'Pooja Nair', 'Engineering', 'Software Developer', 'getset_dev_5', 'employee', 'dev5@getset.com'),
            ('GET-009', 'Sameer Khan', 'Engineering', 'Software Developer', 'getset_dev_6', 'employee', 'dev6@getset.com'),
            ('GET-010', 'Isha Verma', 'Engineering', 'Software Developer', 'getset_dev_7', 'employee', 'dev7@getset.com'),
        ],
    },
]


def upsert_company(conn, name, slug):
    return conn.execute(
        """INSERT INTO companies (name, slug, active)
           VALUES (%s, %s, TRUE)
           ON CONFLICT (slug) DO UPDATE
           SET name = EXCLUDED.name,
               active = TRUE
           RETURNING id""",
        (name, slug),
    ).fetchone()['id']


def upsert_employee(conn, company_id, employee):
    employee_code, name, department, role, gallery_label, _user_role, _email = employee
    existing = conn.execute(
        """SELECT id FROM employees
           WHERE company_id = %s
             AND (employee_code = %s OR gallery_label = %s)""",
        (company_id, employee_code, gallery_label),
    ).fetchone()
    if existing:
        return conn.execute(
            """UPDATE employees
               SET employee_code = %s,
                   name = %s,
                   department = %s,
                   role = %s,
                   gallery_label = %s,
                   active = TRUE
               WHERE id = %s
               RETURNING id""",
            (employee_code, name, department, role, gallery_label, existing['id']),
        ).fetchone()['id']

    return conn.execute(
        """INSERT INTO employees
           (company_id, employee_code, name, department, role, gallery_label, active)
           VALUES (%s, %s, %s, %s, %s, %s, TRUE)
           RETURNING id""",
        (company_id, employee_code, name, department, role, gallery_label),
    ).fetchone()['id']


def upsert_user(conn, company_id, employee_id, employee, password_hash):
    _code, _name, _department, _job_role, _gallery_label, user_role, email = employee
    conn.execute(
        """INSERT INTO attendance_users
           (company_id, email, password_hash, role, employee_id, active)
           VALUES (%s, %s, %s, %s, %s, TRUE)
           ON CONFLICT (email) DO UPDATE
           SET company_id = EXCLUDED.company_id,
               role = EXCLUDED.role,
               employee_id = EXCLUDED.employee_id,
               active = TRUE""",
        (company_id, email.lower(), password_hash, user_role, employee_id),
    )


def main():
    init_db()
    password_hash = hash_password(DEFAULT_PASSWORD)

    with connect() as conn:
        summary = []
        for company in COMPANIES:
            company_id = upsert_company(conn, company['name'], company['slug'])
            for employee in company['employees']:
                employee_id = upsert_employee(conn, company_id, employee)
                upsert_user(conn, company_id, employee_id, employee, password_hash)
            summary.append((company['name'], len(company['employees'])))
        conn.commit()

    print('Seeded mock data:')
    for name, count in summary:
        print(f'- {name}: {count} employees')
    print(f'User password for all seeded mock accounts: {DEFAULT_PASSWORD}')


if __name__ == '__main__':
    main()
