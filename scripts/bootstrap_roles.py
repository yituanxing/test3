#!/usr/bin/env python3
"""Create a region-scoped demo account interactively (no passwords in git/CI)."""
from __future__ import annotations
import argparse
import getpass
import sqlite3

from app import access
from app.main import db, setup_db


def main():
    parser=argparse.ArgumentParser(description='Create a real role-scoped local demo account')
    parser.add_argument('--username',required=True)
    parser.add_argument('--region-id',type=int,required=True)
    a=parser.parse_args()
    pw=getpass.getpass('New password (>= 12 characters): ')
    again=getpass.getpass('Confirm password: ')
    if pw!=again:
        raise SystemExit('Passwords do not match')
    setup_db()
    try:
        with db() as c:
            uid=access.create_user(c,a.username,pw,a.region_id)
        print(f'Account created: id={uid}, name={a.username}, region_id={a.region_id}')
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise SystemExit(f'Account not created: {exc}')


if __name__=='__main__':main()
