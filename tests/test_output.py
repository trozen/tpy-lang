"""Output behavioral tests for the TurboPython compiler.

Tests verify that compiled C++ output matches CPython output.
"""

from conftest import (
    CASES_DIR,
    get_module_name,
    compile_file,
    run_cpython,
    compile_and_run_cpp,
    TEST_CODEGEN_OPTIONS,
)


def make_output_test(case_name: str):
    """Create an output comparison test for a case."""
    def test_func(tmp_path):
        case_dir = CASES_DIR / case_name
        src_dir = case_dir / "src"
        expected_dir = case_dir / "expected"

        src_files = list(src_dir.glob("*.tp.py"))
        assert src_files, f"No .tp.py files found in {src_dir}"

        main_src = src_files[0]
        module_name = get_module_name(main_src)

        # Run with CPython
        cpython_output = run_cpython(main_src)

        # Compile to C++ and run
        compile_file(str(main_src), str(tmp_path), TEST_CODEGEN_OPTIONS)
        cpp_output = compile_and_run_cpp(tmp_path, module_name)

        # Compare outputs
        assert cpp_output == cpython_output, (
            f"Output mismatch:\n"
            f"--- CPython ---\n{cpython_output}\n"
            f"--- C++ ---\n{cpp_output}"
        )

        # Check against expected output file
        expected_output_file = expected_dir / "output.txt"
        if expected_output_file.exists():
            expected_output = expected_output_file.read_text()
            assert cpp_output == expected_output, (
                f"Output differs from expected:\n"
                f"--- Expected ---\n{expected_output}\n"
                f"--- Actual ---\n{cpp_output}"
            )

    return test_func


def _generate_tests():
    """Discover cases and generate output test functions."""
    if not CASES_DIR.exists():
        return

    for case_dir in sorted(CASES_DIR.iterdir()):
        if case_dir.is_dir() and (case_dir / "src").exists():
            # Skip diagnostic-only tests (no output.txt)
            if not (case_dir / "expected" / "output.txt").exists():
                continue
            case_name = case_dir.name
            globals()[f"test_{case_name}_output"] = make_output_test(case_name)


_generate_tests()
