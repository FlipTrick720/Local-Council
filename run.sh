#!/usr/bin/env bash
# Creates the virtualenv on first run, then starts the server. Extra args go to the app (e.g. --port 9000).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -q -r requirements.txt
fi
exec .venv/bin/python -m app "$@"
