"""CPython compatibility tests for the TurboPython compiler.

Runs each test case's source with CPython and compares stdout to expected/output.txt.
Only covers cases/ (not errors/ or panics/). Skips cases with no_cpython.txt marker.

This is read-only verification — output.txt is never written by this test.
"""

import pytest

from conftest import (
    run_cpython,
    discover_cases_only,
)


def make_cpy_test(case_dir, main_src):
    """Create a CPython test for a case."""
    def test_func():
        if (case_dir / "no_cpython.txt").exists():
            pytest.skip("no_cpython.txt")

        expected_output = case_dir / "expected" / "output.txt"

        if not expected_output.exists():
            pytest.skip("No expected output.txt")

        cpython_output = run_cpython(main_src)

        expected = expected_output.read_text()
        assert cpython_output == expected, (
            f"CPython output differs from expected.\n"
            f"--- Expected ---\n{expected}\n"
            f"--- CPython ---\n{cpython_output}"
        )

    return test_func


def _generate_tests():
    """Discover and generate CPython tests for all normal cases."""
    for name, case_dir, main_src in discover_cases_only():
        globals()[f"test_{name}"] = make_cpy_test(case_dir, main_src)


_generate_tests()
