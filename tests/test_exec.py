"""Execution tests for the TurboPython compiler.

Compiles C++ code, runs binaries, and compares output with CPython.
Only runs for test cases that compile successfully.

Set UPDATE_EXPECTED=1 to update expected files instead of comparing.
"""

import tempfile
from pathlib import Path

import pytest

from conftest import (
    UPDATE_EXPECTED,
    get_module_name,
    compile_with_diagnostics,
    run_cpython,
    build_and_run,
    check_or_update,
    remove_if_exists,
    discover_cases,
)


def make_exec_test(case_dir, main_src):
    """Create an execution test for a case."""
    def test_func(tmp_path):
        expected_dir = case_dir / "expected"
        module_name = get_module_name(main_src)

        # Compile TurboPython → C++
        result = compile_with_diagnostics(main_src, tmp_path)

        if not result.success:
            pytest.fail(f"Compilation failed unexpectedly:\n{result.diagnostics}")

        # Build and run C++
        run_result = build_and_run(tmp_path, module_name)

        if run_result.success:
            # Get expected output from CPython
            cpython_output = run_cpython(main_src)

            # Verify C++ output matches CPython (only in test mode)
            if not UPDATE_EXPECTED and run_result.stdout != cpython_output:
                pytest.fail(
                    f"Output mismatch.\n"
                    f"--- CPython ---\n{cpython_output}\n"
                    f"--- C++ ---\n{run_result.stdout}"
                )

            # Check/update output.txt
            expected_output = expected_dir / "output.txt"
            check_or_update(cpython_output, expected_output, "Output")

            # Clean up stale panic.txt in update mode
            if UPDATE_EXPECTED:
                remove_if_exists(expected_dir / "panic.txt")
        else:
            # Runtime panic
            expected_panic = expected_dir / "panic.txt"
            check_or_update(run_result.stderr, expected_panic, "Panic output")

            # Clean up stale output.txt in update mode
            if UPDATE_EXPECTED:
                remove_if_exists(expected_dir / "output.txt")

            # In test mode, verify panic was expected
            if not UPDATE_EXPECTED and not expected_panic.exists():
                pytest.fail(f"Program panicked unexpectedly.\nstderr: {run_result.stderr}")

    return test_func


def _generate_tests():
    """Discover and generate execution tests for cases that compile successfully."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for name, case_dir, main_src in discover_cases():
            case_tmp = tmp_path / name
            case_tmp.mkdir()
            result = compile_with_diagnostics(main_src, case_tmp)
            if result.success:
                globals()[f"test_{name}"] = make_exec_test(case_dir, main_src)


_generate_tests()
