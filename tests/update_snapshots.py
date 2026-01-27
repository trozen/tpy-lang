#!/usr/bin/env python3
"""Update expected snapshots by running tests with UPDATE_EXPECTED=1.

Usage:
    python tests/update_snapshots.py              # Update all cases
    python tests/update_snapshots.py hello        # Update specific case (pytest -k hello)
    python tests/update_snapshots.py -k hello     # Same, explicit -k flag

Any arguments are passed directly to pytest.
"""
import os
import subprocess
import sys

# Build pytest arguments
pytest_args = ["pytest", "tests/test_cases.py", "-v"]

# Pass through all command-line arguments
if sys.argv[1:]:
    args = sys.argv[1:]
    # If first arg doesn't start with -, treat it as a -k pattern
    if args and not args[0].startswith("-"):
        pytest_args.extend(["-k", args[0]])
        pytest_args.extend(args[1:])
    else:
        pytest_args.extend(args)

result = subprocess.run(
    pytest_args,
    env={**os.environ, "UPDATE_EXPECTED": "1"}
)
sys.exit(result.returncode)
