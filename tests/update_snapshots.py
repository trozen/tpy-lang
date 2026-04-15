#!/usr/bin/env python3
"""Update expected snapshots by running tests with UPDATE_EXPECTED=1.

Usage:
    python tests/update_snapshots.py                   # update all cases
    python tests/update_snapshots.py -k hello          # update a specific case
    python tests/update_snapshots.py -k "foo or bar"   # pattern

Any other arguments (e.g. -n auto) are passed directly to pytest.

UPDATE_EXPECTED=1 implies unconditional exec + CPython phases so that
output.txt, panic.txt, diagnostics, generated code, and the per-case
.fingerprints file are all regenerated in one pass.
"""
import argparse
import os
import subprocess
import sys

parser = argparse.ArgumentParser(description="Update test snapshots")
parser.add_argument("-k", "--pattern", help="Case name pattern (passed to pytest -k)")

args, extra_pytest_args = parser.parse_known_args()

pytest_args = ["pytest", "tests/test_case.py", "-v"]
if args.pattern:
    pytest_args.extend(["-k", args.pattern])
pytest_args.extend(extra_pytest_args)

result = subprocess.run(
    pytest_args,
    env={**os.environ, "UPDATE_EXPECTED": "1"},
)
sys.exit(result.returncode)
