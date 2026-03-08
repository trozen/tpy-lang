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
    get_case_default_int,
    build_and_run,
    find_extra_src_files,
    find_extra_include_dirs,
    find_force_includes,
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
    result = compile_with_diagnostics(main_src, build_dir, default_int=get_case_default_int(case_dir))

    if not result.success:
        pytest.skip("Compilation failed")

    # Build and run C++ (pass all cpp files for multi-module support)
    all_cpp_files = [cpp_path for _, _, cpp_path, _ in result.all_modules] if result.all_modules else None
    extra_src = find_extra_src_files(case_dir)
    extra_includes = find_extra_include_dirs(case_dir)
    force_includes = find_force_includes(case_dir)
    run_result = build_and_run(build_dir, module_name, all_cpp_files=all_cpp_files,
                               extra_src_files=extra_src or None,
                               extra_include_dirs=extra_includes or None,
                               force_includes=force_includes or None)

    if run_result.cpp_build_failed:
        pytest.fail(
            f"C++ compilation failed for {main_src}.\n"
            f"--- stderr ---\n{run_result.stderr}",
            pytrace=False,
        )

    if run_result.success:
        expected_output = expected_dir / "output.txt"
        check_or_update(run_result.stdout, expected_output, "Output")
    else:
        if not UPDATE_EXPECTED and not case_dir.name.startswith("panic_"):
            pytest.fail(
                f"Non-panic test panicked for {main_src} (exit code {run_result.returncode}).\n"
                f"--- stderr ---\n{run_result.stderr}",
                pytrace=False,
            )

        # Runtime panic
        expected_panic = expected_dir / "panic.txt"
        check_or_update(run_result.stderr, expected_panic, "Panic output")
