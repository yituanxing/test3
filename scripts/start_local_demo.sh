#!/usr/bin/env bash
# First-run local demo. Never expose to the public network.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -q \
  -r requirements.txt -r requirements-business.txt
.venv/bin/python -m scripts.demo_bootstrap
printf '\nOpen http://127.0.0.1:8001/login\n'
echo 'Press Ctrl+C to stop. Data and credentials remain local.'
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
