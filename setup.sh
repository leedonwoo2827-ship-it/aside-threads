#!/usr/bin/env bash
# aside-threads setup for macOS / Linux - safe to run again.
# Windows uses setup.bat instead.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "python3 not found (3.11+ required)"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'   || { echo "Python 3.11 or newer is required"; exit 1; }

echo "[1/5] Python venv (.venv)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv

echo "[2/5] Python packages"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

echo "[3/5] Playwright Chromium (card rendering)"
if [ "$(uname -s)" = "Linux" ]; then
  .venv/bin/python -m playwright install --with-deps chromium || .venv/bin/python -m playwright install chromium
else
  .venv/bin/python -m playwright install chromium
fi

echo "[4/5] Fonts (Pretendard)"
.venv/bin/python -m aside fonts

echo "[5/5] Doctor (Codex login, Chrome, fonts)"
command -v codex >/dev/null || echo "WARNING: codex CLI not found. Install it, then run: codex login"
.venv/bin/python -c 'import tkinter' 2>/dev/null   || echo "NOTE: tkinter missing - folder/file picker falls back to typing a path (Linux: sudo apt install python3-tk)"
.venv/bin/python -m aside doctor || true

echo
echo "Setup finished."
echo "Next: 1) codex login   (once, your company ChatGPT account)"
echo "      2) ./run.sh      (opens the side panel)"
