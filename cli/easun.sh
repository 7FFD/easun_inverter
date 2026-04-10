#!/usr/bin/env bash
# easun.sh — wrapper that activates the backend venv and runs easun.py
#
# Usage:
#   ./easun.sh discover [--timeout SECONDS]
#   ./easun.sh monitor  [--inverter-ip IP] [--local-ip IP] [--model MODEL]
#                       [--interval SECONDS] [--once]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../backend"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python3"

# Fall back to system python3 if the venv doesn't exist yet
if [[ -x "$VENV_PYTHON" ]]; then
    PYTHON="$VENV_PYTHON"
else
    echo "Warning: venv not found at $VENV_PYTHON, falling back to system python3"
    echo "  Run: cd \"$BACKEND_DIR\" && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    echo ""
    PYTHON="python3"
fi

exec "$PYTHON" "$SCRIPT_DIR/easun.py" "$@"
