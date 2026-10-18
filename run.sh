#!/usr/bin/env bash
# Creates the virtualenv on first run and installs/updates the dependencies whenever requirements.txt changes,
# then starts the server. Extra args go to the app (e.g. --port 9000).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if [ ! -f .venv/.requirements-installed ] || [ requirements.txt -nt .venv/.requirements-installed ]; then
  .venv/bin/python -m pip install -q -r requirements.txt
  touch .venv/.requirements-installed
fi
exec .venv/bin/python -m app "$@"
