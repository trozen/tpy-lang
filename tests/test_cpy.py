"""CPython compatibility tests for the TurboPython compiler.

Runs each test case's source with CPython and compares stdout to expected/output.txt.
Only covers success cases (not error_ or panic_). Skips cases with no_cpython.txt marker.

This is read-only verification — output.txt is never written by this test.
"""

import pytest

from conftest import (
    check_or_update,
    run_cpython,
    discover_success_cases,
)


@pytest.mark.parametrize("case_dir, main_src", [
    pytest.param(case_dir, main_src, id=name)
    for name, case_dir, main_src in discover_success_cases()
])
def test_cpy(case_dir, main_src):
    if (case_dir / "no_cpython.txt").exists():
        pytest.skip("no_cpython.txt")

    expected_output = case_dir / "expected" / "output.txt"

    if not expected_output.exists():
        pytest.skip("No expected output.txt")

    cpython_output = run_cpython(main_src)
    check_or_update(cpython_output, expected_output, "CPython output")
