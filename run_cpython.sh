#!/bin/bash
# Run a .tp.py file with CPython using the test harness
# Usage: ./run_cpython.sh examples/game_of_life.tp.py

if [ -z "$1" ]; then
    echo "Usage: $0 <file.tp.py>"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$SCRIPT_DIR/tests/harness" python3 "$1"
