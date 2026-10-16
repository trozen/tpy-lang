#!/bin/bash
# Run a TurboPython file with CPython in the project's venv, whose editable
# tpy-lang install provides tpy and tplib (from lib/cpy)
# Usage: ./run_cpython.sh path/to/program.py [args...]

if [ -z "$1" ]; then
    echo "Usage: $0 <file.py> [args...]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec uv run --project "$SCRIPT_DIR" python "$@"
