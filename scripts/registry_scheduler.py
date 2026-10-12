"""Opt-in synthetic roster draft scheduler; run alongside local Web server.

Usage: ZD_RUN_ROSTER_SCHEDULER=1 bash scripts/start_local_demo.sh
No real-person data is read; publication always needs human approval.
"""
import time
import logging
from app import main,roster_maintenance

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s")
main.setup_db()
while True:
    try:
        with main.db() as c:
            c.execute('BEGIN IMMEDIATE')
            outcome=roster_maintenance.tick(c)
        if outcome['action'] in ('draft_created','no_source_observations'):
            logging.info("Synthetic roster maintenance: %s",outcome)
    except Exception:
        logging.exception("Synthetic roster maintenance error")
    time.sleep(60)
