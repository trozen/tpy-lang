"""Execution tests for the TurboPython compiler.

Compiles C++ code, runs binaries, and compares output to expected/output.txt.
Only runs for test cases that compile successfully.

Set UPDATE_EXPECTED=1 to update expected files instead of comparing.
"""

import pytest

from conftest import (
    UPDATE_EXPECTED,
    get_module_name,
    compile_with_diagnostics,
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
            pytest.skip("Compilation failed")

        # Build and run C++ (pass all cpp files for multi-module support)
        all_cpp_files = [cpp_path for _, _, cpp_path in result.all_modules] if result.all_modules else None
        run_result = build_and_run(tmp_path, module_name, all_cpp_files=all_cpp_files)

        if run_result.success:
            expected_output = expected_dir / "output.txt"
            check_or_update(run_result.stdout, expected_output, "Output")

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
    """Discover and generate execution tests for all cases."""
    for name, case_dir, main_src in discover_cases():
        globals()[f"test_{name}"] = make_exec_test(case_dir, main_src)


_generate_tests()
