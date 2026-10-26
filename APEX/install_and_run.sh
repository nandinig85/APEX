#!/usr/bin/env bash
# APEX one-click install and launch (macOS / Linux). Run: bash install_and_run.sh
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required. Install it from https://www.python.org/downloads/"
  exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  "$PY" -m venv .venv
fi
echo "Installing dependencies..."
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt
echo "Starting APEX at http://localhost:8501 ..."
.venv/bin/python -m streamlit run app.py
