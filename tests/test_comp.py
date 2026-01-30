"""Fast compilation tests for the TurboPython compiler.

Tests compilation diagnostics, inline annotations, and generated code.
Does NOT compile C++ or run binaries - see test_exec.py for that.

Set UPDATE_EXPECTED=1 to update expected files instead of comparing.
"""

import pytest

from conftest import (
    UPDATE_EXPECTED,
    get_module_name,
    compile_with_diagnostics,
    validate_annotations,
    check_or_update,
    remove_if_exists,
    discover_cases,
)


def make_comp_test(case_dir, main_src):
    """Create a compilation test for a case."""
    def test_func(tmp_path):
        expected_dir = case_dir / "expected"
        module_name = get_module_name(main_src)

        # Compile
        result = compile_with_diagnostics(main_src, tmp_path)

        # Check/update diagnostics
        expected_diag = expected_dir / "diag.txt"
        check_or_update(result.diagnostics, expected_diag, "Diagnostics")

        # Validate inline annotations (only in test mode)
        if not UPDATE_EXPECTED:
            annotation_errors = validate_annotations(main_src, result.diagnostics)
            if annotation_errors:
                pytest.fail("\n".join(annotation_errors))

        # Handle compilation failure
        if not result.success:
            if UPDATE_EXPECTED:
                # Error test: remove any stale generated files
                for ext in [".hpp", ".cpp"]:
                    remove_if_exists(expected_dir / f"{module_name}{ext}")
            else:
                # Verify compilation was expected to fail (no .hpp in expected/)
                expects_success = any(expected_dir.glob("*.hpp"))
                if expects_success:
                    pytest.fail("Expected compilation to fail")
            return

        # Compilation succeeded
        if not UPDATE_EXPECTED:
            expects_success = any(expected_dir.glob("*.hpp"))
            if not expects_success:
                pytest.fail(f"Compilation succeeded unexpectedly:\n{result.diagnostics}")

        # Check/update generated code
        module_dir = tmp_path / f"{module_name}.d"
        for ext in [".hpp", ".cpp"]:
            generated_file = module_dir / f"{module_name}{ext}"
            expected_file = expected_dir / f"{module_name}{ext}"
            if not generated_file.exists():
                pytest.fail(f"{generated_file} not generated")
            check_or_update(generated_file.read_text(), expected_file, f"{module_name}{ext}")

    return test_func


def _generate_tests():
    """Discover and generate compilation tests for all cases."""
    for name, case_dir, main_src in discover_cases():
        globals()[f"test_{name}"] = make_comp_test(case_dir, main_src)


_generate_tests()
