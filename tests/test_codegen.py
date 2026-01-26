"""Codegen snapshot tests for the TurboPython compiler.

Tests verify that code generation produces expected C++ output.
Also supports diagnostic testing via expected/diag.txt and inline annotations.
"""

from conftest import (
    CASES_DIR, get_module_name, compile_file, TEST_CODEGEN_OPTIONS,
    compile_with_diagnostics, validate_annotations
)


def make_codegen_test(case_name: str):
    """Create a codegen snapshot test for a case."""
    def test_func(tmp_path):
        case_dir = CASES_DIR / case_name
        src_dir = case_dir / "src"
        expected_dir = case_dir / "expected"

        src_files = list(src_dir.glob("*.tp.py"))
        assert src_files, f"No .tp.py files found in {src_dir}"

        main_src = src_files[0]
        module_name = get_module_name(main_src)

        # Check if this is a diagnostic test (has diag.txt)
        expected_diag = expected_dir / "diag.txt"
        has_diag_test = expected_diag.exists()

        # Compile and capture diagnostics
        result = compile_with_diagnostics(main_src, tmp_path)

        # Compare diagnostics if diag.txt exists
        if has_diag_test:
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

        # If compilation failed, we're done (diagnostic test)
        if not result.success:
            # Make sure we expected failure (diag.txt should exist with errors)
            if not has_diag_test:
                assert False, f"Compilation failed unexpectedly:\n{result.diagnostics}"
            return

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
    """Discover cases and generate codegen test functions."""
    if not CASES_DIR.exists():
        return

    for case_dir in sorted(CASES_DIR.iterdir()):
        if case_dir.is_dir() and (case_dir / "src").exists():
            case_name = case_dir.name
            globals()[f"test_{case_name}_codegen"] = make_codegen_test(case_name)


_generate_tests()
