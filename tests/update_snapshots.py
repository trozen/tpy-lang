#!/usr/bin/env python3
"""Update expected snapshots by running tests with UPDATE_EXPECTED=1.

Usage:
    python tests/update_snapshots.py              # Update all cases
    python tests/update_snapshots.py --comp       # Update compilation tests only
    python tests/update_snapshots.py --exec       # Update execution tests only
    python tests/update_snapshots.py --cpy        # Run CPython compatibility checks (read-only)
    python tests/update_snapshots.py hello        # Update specific case (all types)
    python tests/update_snapshots.py --comp hello # Update compilation tests for specific case

Any other arguments are passed directly to pytest.
"""
import argparse
import os
import subprocess
import sys

parser = argparse.ArgumentParser(description="Update test snapshots")
parser.add_argument("--comp", action="store_true", help="Update compilation tests only")
parser.add_argument("--exec", action="store_true", help="Update execution tests only")
parser.add_argument("--cpy", action="store_true", help="Run CPython compatibility checks (read-only)")
parser.add_argument("pattern", nargs="?", help="Case name pattern (passed to pytest -k)")
parser.add_argument("pytest_args", nargs="*", help="Additional pytest arguments")

args = parser.parse_args()

# Determine which test files to run
test_files = []
if args.comp:
    test_files.append("tests/test_comp.py")
if args.exec:
    test_files.append("tests/test_exec.py")
if args.cpy:
    test_files.append("tests/test_cpy.py")
if not test_files:
    test_files = ["tests/test_comp.py", "tests/test_exec.py", "tests/test_cpy.py"]

# Build pytest arguments
pytest_args = ["pytest"] + test_files + ["-v"]
if args.pattern:
    pytest_args.extend(["-k", args.pattern])
pytest_args.extend(args.pytest_args)

result = subprocess.run(
    pytest_args,
    env={**os.environ, "UPDATE_EXPECTED": "1"}
)
sys.exit(result.returncode)
