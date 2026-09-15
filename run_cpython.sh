#!/bin/bash
# Run a TurboPython file with CPython using lib/cpy stubs
# Usage: ./run_cpython.sh path/to/program.py

if [ -z "$1" ]; then
    echo "Usage: $0 <file.py>"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$SCRIPT_DIR/lib/cpy" python3 "$1"
