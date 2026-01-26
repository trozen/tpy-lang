"""Codegen snapshot tests for the TurboPython compiler.

Tests are organized by type:
- tests/cases/errors/   - Compile-time error tests (check diag.txt)
- tests/cases/panics/   - Runtime panic tests (check panic.txt)
- tests/cases/          - Normal codegen tests (check .hpp/.cpp)
"""

from conftest import (
    CASES_DIR, get_module_name,
    compile_with_diagnostics, validate_annotations, build_and_run
)


def make_error_test(case_dir):
    """Create a compile-time error test."""
    def test_func(tmp_path):
        src_dir = case_dir / "src"
        expected_dir = case_dir / "expected"

        src_files = list(src_dir.glob("*.tp.py"))
        assert src_files, f"No .tp.py files found in {src_dir}"

        main_src = src_files[0]

        # Compile and capture diagnostics
        result = compile_with_diagnostics(main_src, tmp_path)

        # Should fail compilation
        assert not result.success, f"Expected compilation to fail, but it succeeded"

        # Compare diagnostics
        expected_diag = expected_dir / "diag.txt"
        assert expected_diag.exists(), f"Missing {expected_diag}"
        expected_diag_content = expected_diag.read_text()
        assert result.diagnostics == expected_diag_content, (
            f"Diagnostics differ from expected.\n"
            f"--- Expected ---\n{expected_diag_content}\n"
            f"--- Got ---\n{result.diagnostics}"
        )

        # Validate inline annotations
        annotation_errors = validate_annotations(main_src, result.diagnostics)
        assert not annotation_errors, (
            f"Annotation validation failed:\n" + "\n".join(annotation_errors)
        )

    return test_func


def make_panic_test(case_dir):
    """Create a runtime panic test."""
    def test_func(tmp_path):
        src_dir = case_dir / "src"
        expected_dir = case_dir / "expected"

        src_files = list(src_dir.glob("*.tp.py"))
        assert src_files, f"No .tp.py files found in {src_dir}"

        main_src = src_files[0]
        module_name = get_module_name(main_src)

        # Compile (should succeed)
        result = compile_with_diagnostics(main_src, tmp_path)
        assert result.success, f"Compilation failed unexpectedly:\n{result.diagnostics}"

        # Compare generated code
        module_dir = tmp_path / f"{module_name}.d"
        for ext in [".hpp", ".cpp"]:
            expected_file = expected_dir / f"{module_name}{ext}"
            if expected_file.exists():
                generated_file = module_dir / f"{module_name}{ext}"
                assert generated_file.exists(), f"Expected {module_name}{ext} to be generated"
                expected_content = expected_file.read_text()
                generated_content = generated_file.read_text()
                assert generated_content == expected_content, (
                    f"Generated {module_name}{ext} differs from expected.\n"
                    f"--- Expected ---\n{expected_content}\n"
                    f"--- Generated ---\n{generated_content}"
                )

        # Run (should panic)
        run_result = build_and_run(tmp_path, module_name)
        assert not run_result.success, f"Expected panic but program succeeded with output:\n{run_result.stdout}"

        # Compare panic output
        expected_panic = expected_dir / "panic.txt"
        assert expected_panic.exists(), f"Missing {expected_panic}"
        expected_panic_content = expected_panic.read_text()
        assert run_result.stderr == expected_panic_content, (
            f"Panic output differs from expected.\n"
            f"--- Expected ---\n{expected_panic_content}\n"
            f"--- Got ---\n{run_result.stderr}"
        )

    return test_func


def make_codegen_test(case_dir):
    """Create a codegen snapshot test for a normal case."""
    def test_func(tmp_path):
        src_dir = case_dir / "src"
        expected_dir = case_dir / "expected"

        src_files = list(src_dir.glob("*.tp.py"))
        assert src_files, f"No .tp.py files found in {src_dir}"

        main_src = src_files[0]
        module_name = get_module_name(main_src)

        # Compile (should succeed)
        result = compile_with_diagnostics(main_src, tmp_path)
        assert result.success, f"Compilation failed unexpectedly:\n{result.diagnostics}"

        # Compare generated code
        module_dir = tmp_path / f"{module_name}.d"

        for ext in [".hpp", ".cpp"]:
            expected_file = expected_dir / f"{module_name}{ext}"
            generated_file = module_dir / f"{module_name}{ext}"
            assert generated_file.exists(), f"Expected {module_name}{ext} to be generated"

            expected_content = expected_file.read_text()
            generated_content = generated_file.read_text()

            assert generated_content == expected_content, (
                f"Generated {module_name}{ext} differs from expected.\n"
                f"--- Expected ---\n{expected_content}\n"
                f"--- Generated ---\n{generated_content}"
            )

    return test_func


def _generate_tests():
    """Discover cases and generate test functions."""
    if not CASES_DIR.exists():
        return

    # Error tests (compile-time failures)
    errors_dir = CASES_DIR / "errors"
    if errors_dir.exists():
        for case_dir in sorted(errors_dir.iterdir()):
            if case_dir.is_dir() and (case_dir / "src").exists():
                case_name = case_dir.name
                globals()[f"test_error_{case_name}"] = make_error_test(case_dir)

    # Panic tests (runtime failures)
    panics_dir = CASES_DIR / "panics"
    if panics_dir.exists():
        for case_dir in sorted(panics_dir.iterdir()):
            if case_dir.is_dir() and (case_dir / "src").exists():
                case_name = case_dir.name
                globals()[f"test_panic_{case_name}"] = make_panic_test(case_dir)

    # Normal codegen tests (in root of cases/)
    for case_dir in sorted(CASES_DIR.iterdir()):
        if case_dir.is_dir() and case_dir.name not in ("errors", "panics") and (case_dir / "src").exists():
            case_name = case_dir.name
            globals()[f"test_{case_name}_codegen"] = make_codegen_test(case_dir)


_generate_tests()
