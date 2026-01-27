"""Tests for the TurboPython compiler.

For each test case:
1. Compile (check diag.txt)
2. If compilation fails → verify it was expected (no .hpp in expected/)
3. If compilation succeeds → run and check output.txt or panic.txt

Set UPDATE_EXPECTED=1 to update expected files instead of comparing.
"""

from conftest import (
    CASES_DIR,
    UPDATE_EXPECTED,
    get_module_name,
    compile_with_diagnostics,
    validate_annotations,
    run_cpython,
    build_and_run,
    check_or_update,
    remove_if_exists,
)


def make_test(case_dir):
    """Create a test for a case."""
    def test_func(tmp_path):
        src_dir = case_dir / "src"
        expected_dir = case_dir / "expected"

        src_files = list(src_dir.glob("*.tp.py"))
        assert src_files, f"No .tp.py files found in {src_dir}"

        main_src = src_files[0]
        module_name = get_module_name(main_src)

        # Compile
        result = compile_with_diagnostics(main_src, tmp_path)

        # Check/update diagnostics
        expected_diag = expected_dir / "diag.txt"
        check_or_update(result.diagnostics, expected_diag, "Diagnostics")

        # Validate inline annotations (only in test mode)
        if not UPDATE_EXPECTED:
            annotation_errors = validate_annotations(main_src, result.diagnostics)
            assert not annotation_errors, "\n".join(annotation_errors)

        # Handle compilation failure
        if not result.success:
            if UPDATE_EXPECTED:
                # Error test: remove any stale generated files
                for ext in [".hpp", ".cpp"]:
                    remove_if_exists(expected_dir / f"{module_name}{ext}")
                remove_if_exists(expected_dir / "output.txt")
                remove_if_exists(expected_dir / "panic.txt")
            else:
                # Verify compilation was expected to fail (no .hpp in expected/)
                expects_success = any(expected_dir.glob("*.hpp"))
                assert not expects_success, "Expected compilation to fail"
            return

        # Compilation succeeded
        if not UPDATE_EXPECTED:
            expects_success = any(expected_dir.glob("*.hpp"))
            assert expects_success, f"Compilation succeeded unexpectedly:\n{result.diagnostics}"

        # Check/update generated code
        module_dir = tmp_path / f"{module_name}.d"
        for ext in [".hpp", ".cpp"]:
            generated_file = module_dir / f"{module_name}{ext}"
            expected_file = expected_dir / f"{module_name}{ext}"
            assert generated_file.exists(), f"{generated_file} not generated"
            check_or_update(generated_file.read_text(), expected_file, f"{module_name}{ext}")

        # Run
        run_result = build_and_run(tmp_path, module_name)

        if run_result.success:
            # Get expected output from CPython
            cpython_output = run_cpython(main_src)

            # Verify C++ output matches CPython (only in test mode)
            if not UPDATE_EXPECTED:
                assert run_result.stdout == cpython_output, (
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
                assert False, f"Program panicked unexpectedly.\nstderr: {run_result.stderr}"

    return test_func


def _generate_tests():
    """Discover and generate tests for all cases."""
    if not CASES_DIR.exists():
        return

    for src_dir in CASES_DIR.rglob("src"):
        if not src_dir.is_dir():
            continue
        case_dir = src_dir.parent
        expected_dir = case_dir / "expected"

        # In update mode, allow cases without expected/ dir (will be created)
        if not UPDATE_EXPECTED and not expected_dir.exists():
            continue

        rel_path = case_dir.relative_to(CASES_DIR)
        test_name = str(rel_path).replace("/", "_").replace("\\", "_")
        globals()[f"test_{test_name}"] = make_test(case_dir)


_generate_tests()
