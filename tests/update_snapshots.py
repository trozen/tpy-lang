#!/usr/bin/env python3
"""Thin wrapper around `pytest --update-snapshots`.

Usage:
    python tests/update_snapshots.py                   # update all cases
    python tests/update_snapshots.py -k hello          # update a specific case
    python tests/update_snapshots.py -k "foo or bar"   # pattern

Any other arguments (e.g. -n auto) are passed directly to pytest.

Equivalent to `uv run pytest tests/test_case.py tests/test_interop_exec.py
-v --update-snapshots`. --update-snapshots implies --force-exec so output.txt,
panic.txt, diagnostics, generated code (including the CPython ext-exec glue),
and the per-case .fingerprints file are regenerated in one pass.
"""
import argparse
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description="Update test snapshots")
    parser.add_argument("-k", "--pattern", help="Case name pattern (passed to pytest -k)")

    args, extra_pytest_args = parser.parse_known_args()

    pytest_args = ["pytest", "tests/test_case.py", "tests/test_interop_exec.py",
                   "-v", "--update-snapshots"]
    if args.pattern:
        pytest_args.extend(["-k", args.pattern])
    pytest_args.extend(extra_pytest_args)

    return subprocess.run(pytest_args).returncode


if __name__ == "__main__":
    sys.exit(main())
else:
    # This file RUNS pytest -- it is a script, not a test module. If it is imported
    # (e.g. a mistaken `pytest tests/update_snapshots.py`), fail LOUDLY here instead
    # of letting the top-level code fork-bomb the build host: under `-n auto` every
    # xdist worker would import it and launch a full nested --update-snapshots suite.
    raise RuntimeError(
        "update_snapshots.py is a script, not a pytest test module -- do not run "
        "`pytest tests/update_snapshots.py`. Run `python tests/update_snapshots.py "
        "[-k PATTERN]`, or offload with `rpytest --update-snapshots -k PATTERN`."
    )
