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
    parse_annotations,
    check_or_update,
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


@pytest.mark.parametrize("case_dir, main_src", [
    pytest.param(case_dir, main_src, id=name)
    for name, case_dir, main_src in discover_cases()
])
def test_comp(case_dir, main_src, tmp_path):
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

    # Error tests must have at least one # tpyc: error(...) annotation
    if not UPDATE_EXPECTED and case_dir.name.startswith("error_"):
        src_dir = case_dir / "src"
        has_error_annotation = False
        for src_file in src_dir.rglob("*.py"):
            annotations = parse_annotations(src_file.read_text())
            if any(a.level == "error" for a in annotations):
                has_error_annotation = True
                break
        if not has_error_annotation:
            pytest.fail("Error test must have at least one '# tpyc: error(...)' annotation")

    # Handle compilation failure
    if not result.success:
        if not UPDATE_EXPECTED and not case_dir.name.startswith("error_"):
            pytest.fail(f"Non-error test failed to compile:\n{result.diagnostics}")
        return

    # Error tests must not compile successfully
    if not UPDATE_EXPECTED and case_dir.name.startswith("error_"):
        pytest.fail(f"Error test compiled successfully (expected compilation failure)")

    # Check/update generated code for all modules
    for mod_name, hpp_path, cpp_path in result.all_modules:
        for ext, gen_path in [(".hpp", hpp_path), (".cpp", cpp_path)]:
            expected_file = _module_to_expected_path(expected_dir, mod_name, ext)
            if not gen_path.exists():
                pytest.fail(f"{gen_path} not generated")
            check_or_update(gen_path.read_text(), expected_file, f"{mod_name}{ext}")
