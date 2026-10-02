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
  printf ']0;aside-threads - keep this window open while posts are scheduled'
  echo "aside is running. Keep this terminal open - minimize is OK."
  echo "Closing it stops scheduled posts. Run ./run.sh again to reopen the side panel."
  exec .venv/bin/python -m aside ui
else
  exec .venv/bin/python -m aside "$@"
fi
