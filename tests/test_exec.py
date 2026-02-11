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
    discover_cases,
)


@pytest.mark.parametrize("case_dir, main_src", [
    pytest.param(case_dir, main_src, id=name)
    for name, case_dir, main_src in discover_cases()
])
def test_exec(case_dir, main_src):
    expected_dir = case_dir / "expected"
    module_name = get_module_name(main_src)
    build_dir = case_dir / "__tpyc__"

    # In update mode, clear stale exec artifacts before regenerating
    if UPDATE_EXPECTED:
        for fname in ["output.txt", "panic.txt"]:
            f = expected_dir / fname
            if f.exists():
                f.unlink()

    # Compile TurboPython → C++
    result = compile_with_diagnostics(main_src, build_dir)

    if not result.success:
        pytest.skip("Compilation failed")

    # Build and run C++ (pass all cpp files for multi-module support)
    all_cpp_files = [cpp_path for _, _, cpp_path in result.all_modules] if result.all_modules else None
    run_result = build_and_run(build_dir, module_name, all_cpp_files=all_cpp_files)

    if run_result.success:
        expected_output = expected_dir / "output.txt"
        check_or_update(run_result.stdout, expected_output, "Output")
    else:
        if not UPDATE_EXPECTED and not case_dir.name.startswith("panic_"):
            pytest.fail(f"Non-panic test panicked:\n{run_result.stderr}")

        # Runtime panic
        expected_panic = expected_dir / "panic.txt"
        check_or_update(run_result.stderr, expected_panic, "Panic output")
