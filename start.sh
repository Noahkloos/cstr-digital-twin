#!/usr/bin/env bash
# Eenmalige installatie + starten (Linux / macOS):  ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Virtuele omgeving aanmaken..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -f static/vendor/chart.umd.js ]; then
  .venv/bin/python scripts/download_vendor.py || echo "Download mislukt - de CDN wordt gebruikt."
fi
echo "Dashboard: http://127.0.0.1:8000   API-docs: http://127.0.0.1:8000/docs"
exec .venv/bin/python run.py
