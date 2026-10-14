#!/usr/bin/env bash
# First-run local demo. Never expose to the public network.
set -euo pipefail
cd "$(dirname "$0")/.."
# Never reopen the legacy database with synthetic P### records.
export ZD_LEGACY_MODE=0
export ZD_DATA_DIR="${ZD_DATA_DIR:-$PWD/data/real-only}"
mkdir -p "$ZD_DATA_DIR"
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -q \
  -r requirements.txt -r requirements-business.txt
.venv/bin/python -m scripts.demo_bootstrap
printf '\nOpen http://127.0.0.1:8001/login\n'
echo 'Press Ctrl+C to stop. Data and credentials remain local.'
# Synthetic roster scheduler intentionally removed from the operational launcher.
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
