"""Tests for the TurboPython compiler.

For each test case:
1. Compile (check diag.txt)
2. If compilation fails → verify it was expected (no .hpp in expected/)
3. If compilation succeeds → run and check output.txt or panic.txt
"""

from conftest import (
    CASES_DIR,
    get_module_name,
    compile_with_diagnostics,
    validate_annotations,
    run_cpython,
    build_and_run,
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

        # Check diagnostics (missing diag.txt = no diagnostics expected)
        expected_diag_content = ""
        expected_diag = expected_dir / "diag.txt"
        if expected_diag.exists():
            expected_diag_content = expected_diag.read_text()
        assert result.diagnostics == expected_diag_content, (
            f"Diagnostics differ.\n"
            f"--- Expected ---\n{expected_diag_content}\n"
            f"--- Got ---\n{result.diagnostics}"
        )
        annotation_errors = validate_annotations(main_src, result.diagnostics)
        assert not annotation_errors, "\n".join(annotation_errors)

        # Check if compilation should fail
        expects_success = any(expected_dir.glob("*.hpp"))

        if not expects_success:
            assert not result.success, "Expected compilation to fail"
            return

        assert result.success, f"Compilation failed:\n{result.diagnostics}"

        # Check generated code
        module_dir = tmp_path / f"{module_name}.d"
        for ext in [".hpp", ".cpp"]:
            expected_file = expected_dir / f"{module_name}{ext}"
            if expected_file.exists():
                generated_file = module_dir / f"{module_name}{ext}"
                assert generated_file.exists(), f"{generated_file} not generated"
                assert generated_file.read_text() == expected_file.read_text(), (
                    f"{module_name}{ext} differs from expected"
                )

        # Run
        run_result = build_and_run(tmp_path, module_name)

        if run_result.success:
            # Compare with CPython
            cpython_output = run_cpython(main_src)
            assert run_result.stdout == cpython_output, (
                f"Output mismatch.\n"
                f"--- CPython ---\n{cpython_output}\n"
                f"--- C++ ---\n{run_result.stdout}"
            )
            # Compare with expected output
            expected_output = expected_dir / "output.txt"
            if expected_output.exists():
                assert run_result.stdout == expected_output.read_text(), (
                    f"Output differs from expected"
                )
        else:
            # Check panic
            expected_panic = expected_dir / "panic.txt"
            assert expected_panic.exists(), (
                f"Program panicked unexpectedly.\nstderr: {run_result.stderr}"
            )
            assert run_result.stderr == expected_panic.read_text(), (
                f"Panic output differs.\n"
                f"--- Expected ---\n{expected_panic.read_text()}\n"
                f"--- Got ---\n{run_result.stderr}"
            )

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
        if not expected_dir.exists():
            continue

        rel_path = case_dir.relative_to(CASES_DIR)
        test_name = str(rel_path).replace("/", "_").replace("\\", "_")
        globals()[f"test_{test_name}"] = make_test(case_dir)


_generate_tests()
