#!/usr/bin/env bash
# aside-threads launcher for macOS / Linux.
#   ./run.sh               open the side panel (server + docked window)
#   ./run.sh make --job X  any CLI command: python -m aside ...
# Windows uses run.bat instead.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
[ -x .venv/bin/python ] || { echo "Run ./setup.sh first."; exit 1; }
if [ $# -eq 0 ]; then
  printf ']0;aside-threads SERVER - keep open for scheduled posts'
  echo "This terminal is the aside server. Closing it stops scheduled posts."
  echo "The side panel window can be closed and reopened at http://127.0.0.1:5291/"
  exec .venv/bin/python -m aside ui
else
  exec .venv/bin/python -m aside "$@"
fi
