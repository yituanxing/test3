"""Interactive administrator bootstrap and idempotent local business demo fixture.

The local Excel presentation uses an isolated SQLite database. Its P### roster
is a clearly labeled *synthetic demonstration*, never a real identity record.
The normal real-only server MUST NOT acquire any synthetic people.
"""
from __future__ import annotations

import getpass
import os
import sqlite3
from app import access, base_registry
from app.main import db, setup_db, seed_dedup_synthetic, seed_synthetic_business


def prepare_business_demo() -> dict:
    """Populate a FRESH local demo exactly once; preserve any existing user work."""
    if os.getenv("ZD_LEGACY_MODE") != "1":
        return {"status": "skipped", "reason": "real-only mode"}
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM synthetic_observations").fetchone()[0]
    if count:
        return {"status": "preserved", "existing_synthetic_observations": count}

    result = seed_synthetic_business()
    duplicate_case = seed_dedup_synthetic()
    with db() as conn:
        run = base_registry.generate(
            conn, base_registry.GenerateIn(
                days=7, strategy="frequency", correction_mode="rebuild"
            )
        )
        if run["unresolved_count"]:
            raise RuntimeError("Synthetic bootstrap unexpectedly needs manual assignments")
        published = base_registry.publish(conn, run["run"]["id"])
        return {
            "status": "ready",
            "synthetic_observations": result["inserted"] + duplicate_case["inserted"],
            "synthetic_people": len(published["people"]),
            "published_roster": published["run"]["id"],
            "mode": "synthetic_only",
        }


def main() -> None:
    setup_db()
    with db() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0]
        if existing:
            print("Existing accounts:", existing, "- no account changes.")
        else:
            region = conn.execute(
                "SELECT id,name FROM regions WHERE level=1 ORDER BY id LIMIT 1"
            ).fetchone()
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

    status = prepare_business_demo()
    print("Business demo:", status)
    print("Synthetic P### persons and rules are NOT derived from real video.")
    print("Demo is bound to 127.0.0.1 only.")


if __name__ == "__main__":
    main()
