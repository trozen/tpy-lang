"""Fast compilation tests for the TurboPython compiler.

Tests compilation diagnostics, inline annotations, and generated code.
Does NOT compile C++ or run binaries - see test_exec.py for that.

Set UPDATE_EXPECTED=1 to update expected files instead of comparing.
"""

from pathlib import Path

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


def _module_to_expected_path(expected_dir: Path, mod_name: str, ext: str) -> Path:
    """Convert module name to expected file path.

    Args:
        expected_dir: The expected/ directory for the test case.
        mod_name: Module name (may be dotted, e.g., "mypackage.utils").
        ext: File extension (".hpp" or ".cpp").

    Returns:
        Path for the expected file in include/ or src/ subdirectory.
    """
    subdir = "include" if ext == ".hpp" else "src"
    parts = mod_name.split('.')
    if len(parts) == 1:
        return expected_dir / subdir / f"{parts[0]}{ext}"
    # pkg.sub.mod → include/pkg/sub/mod.hpp or src/pkg/sub/mod.cpp
    rel_dir = '/'.join(parts[:-1])
    return expected_dir / subdir / rel_dir / f"{parts[-1]}{ext}"


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
                    remove_if_exists(_module_to_expected_path(expected_dir, module_name, ext))
                # Also clean up any other module files
                for mod_name, _, _ in result.all_modules:
                    for ext in [".hpp", ".cpp"]:
                        remove_if_exists(_module_to_expected_path(expected_dir, mod_name, ext))
            else:
                # Verify compilation was expected to fail (no .hpp in expected/include/)
                include_dir = expected_dir / "include"
                expects_success = include_dir.exists() and any(include_dir.rglob("*.hpp"))
                if expects_success:
                    pytest.fail("Expected compilation to fail")
            return

        # Compilation succeeded
        if not UPDATE_EXPECTED:
            include_dir = expected_dir / "include"
            expects_success = include_dir.exists() and any(include_dir.rglob("*.hpp"))
            if not expects_success:
                pytest.fail(f"Compilation succeeded unexpectedly:\n{result.diagnostics}")

        # Check/update generated code for all modules
        for mod_name, hpp_path, cpp_path in result.all_modules:
            for ext, gen_path in [(".hpp", hpp_path), (".cpp", cpp_path)]:
                expected_file = _module_to_expected_path(expected_dir, mod_name, ext)
                if not gen_path.exists():
                    pytest.fail(f"{gen_path} not generated")
                check_or_update(gen_path.read_text(), expected_file, f"{mod_name}{ext}")

    return test_func


def _generate_tests():
    """Discover and generate compilation tests for all cases."""
    for name, case_dir, main_src in discover_cases():
        globals()[f"test_{name}"] = make_comp_test(case_dir, main_src)


_generate_tests()
