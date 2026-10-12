"""Interactive first-run local demo account bootstrap.

No default username/password pairs are inserted in code or CI. If one or
more users already exist, this command makes no changes to their accounts.
"""
from __future__ import annotations

import getpass
import sqlite3
from app import access
from app.main import db, setup_db

def main():
    setup_db()
    with db() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0]
        if existing:
            print("Existing accounts:", existing, "- no bootstrap needed.")
            return
        region = conn.execute(
            "SELECT id,name FROM regions WHERE level=1 ORDER BY id LIMIT 1").fetchone()
        if region is None:
            raise SystemExit("No top-level region is configured.")
        print("Create local demo administrator demo_state for", region["name"])
        password = getpass.getpass("Administrator password (min 12 characters): ")
        repeated = getpass.getpass("Repeat password: ")
        if password != repeated:
            raise SystemExit("Passwords did not match.")
        try:
            access.create_user(conn, "demo_state", password, region["id"])
        except (sqlite3.IntegrityError, ValueError) as error:
            raise SystemExit("Cannot create administrator: " + str(error)) from error
    print("Demo account created. Bind only to 127.0.0.1.")

if __name__ == "__main__":
    main()
