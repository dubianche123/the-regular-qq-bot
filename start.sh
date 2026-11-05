#!/bin/sh
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.10+ is required. Install Python, then run this launcher again.' >&2
    exit 1
fi
exec python3 "$SCRIPT_DIR/tools/start_regular.py" "$@"
