#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
KINESCOPE_PYTHON=${KINESCOPE_PYTHON:-python3}

if ! command -v "$KINESCOPE_PYTHON" >/dev/null 2>&1; then
  printf '%s\n' "Python 3 is required by this installer. Install Python 3, then run install.sh again." >&2
  exit 127
fi

exec "$KINESCOPE_PYTHON" "$SCRIPT_DIR/scripts/install.py" "$@"
