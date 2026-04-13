"""Fast compilation tests for the TurboPython compiler.

Tests compilation diagnostics, inline annotations, and generated code.
Does NOT compile C++ or run binaries - see test_exec.py for that.

Set UPDATE_EXPECTED=1 to update expected files instead of comparing.
"""

import shutil
import warnings
from pathlib import Path

import pytest

from conftest import (
    UPDATE_EXPECTED,
    get_module_name,
    compile_with_diagnostics,
    get_case_default_int,
    validate_annotations,
    validate_type_annotations,
    validate_non_null_annotations,
    validate_bounds_annotations,
    validate_div_annotations,
    validate_cast_annotations,
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

    # In update mode, clear stale comp artifacts before regenerating
    if UPDATE_EXPECTED:
        diag = expected_dir / "diag.txt"
        if diag.exists():
            diag.unlink()
        for subdir in ["include", "src"]:
            d = expected_dir / subdir
            if d.is_dir():
                shutil.rmtree(d)

    # Compile
    result = compile_with_diagnostics(main_src, tmp_path, default_int=get_case_default_int(case_dir))

    # Check/update diagnostics
    expected_diag = expected_dir / "diag.txt"
    check_or_update(result.diagnostics, expected_diag, "Diagnostics")

    # Validate inline annotations for all local source files (only in test mode)
    if not UPDATE_EXPECTED:
        src_dir = case_dir / "src"
        all_annotation_errors = []
        for src_file in sorted(src_dir.rglob("*.py")):
            all_annotation_errors.extend(validate_annotations(src_file, result.diagnostics))
        if all_annotation_errors:
            pytest.fail(
                "\n".join(all_annotation_errors),
                pytrace=False,
            )

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
            pytest.fail(
                "Error test must have at least one '# tpyc: error(...)' annotation",
                pytrace=False,
            )

    # Handle compilation failure
    if not result.success:
        if not UPDATE_EXPECTED and not case_dir.name.startswith("error_"):
            pytest.fail(
                f"Non-error test failed to compile: {main_src}\n"
                f"--- diagnostics ---\n{result.diagnostics}",
                pytrace=False,
            )
        return

    # Error tests must not compile successfully
    if not UPDATE_EXPECTED and case_dir.name.startswith("error_"):
        pytest.fail(
            f"Error test compiled successfully (expected compilation failure): {main_src}",
            pytrace=False,
        )

    # Warn if a non-error/non-panic test compiles but has no runtime snapshot
    is_error = case_dir.name.startswith("error_")
    is_panic = case_dir.name.startswith("panic_")
    is_warn = case_dir.name.startswith("warn_")
    # Entry module produces cpp = there's something to execute
    entry_cpp = next((cpp_path for name, _, cpp_path, _ in result.all_modules
                      if name == module_name), None)
    has_cpp = entry_cpp is not None
    if not UPDATE_EXPECTED and not is_error and not is_panic and not is_warn and has_cpp:
        has_output = (expected_dir / "output.txt").exists()
        if not has_output:
            warnings.warn(
                f"Test '{case_dir.name}' compiles but has no expected/output.txt "
                f"(run update_snapshots.py --exec -k {case_dir.name})",
                stacklevel=1,
            )

    # Check/update generated code for local modules (skip library modules like tplib)
    for mod_name, hpp_path, cpp_path, is_local in result.all_modules:
        if not is_local:
            continue
        pairs = []
        if hpp_path is not None:  # None for native_module (no .hpp generated)
            pairs.append((".hpp", hpp_path))
        if cpp_path is not None:  # None for native_module (no .cpp generated)
            pairs.append((".cpp", cpp_path))
        for ext, gen_path in pairs:
            expected_file = _module_to_expected_path(expected_dir, mod_name, ext)
            if not gen_path.exists():
                pytest.fail(f"{gen_path} not generated", pytrace=False)
            check_or_update(gen_path.read_text(), expected_file, f"{mod_name}{ext}")

    # Validate # tpyc: type(...) annotations against compiler-resolved types
    if not UPDATE_EXPECTED and result.declared_var_types is not None:
        type_errors = validate_type_annotations(main_src, result.declared_var_types)
        if type_errors:
            pytest.fail("\n".join(type_errors), pytrace=False)

    # Validate # tpyc: non_null/nullable annotations against ptr deref facts
    if not UPDATE_EXPECTED and result.ptr_deref_facts is not None:
        nn_errors = validate_non_null_annotations(main_src, result.ptr_deref_facts)
        if nn_errors:
            pytest.fail("\n".join(nn_errors), pytrace=False)

    # Validate # tpyc: bounds_safe/bounds_checked annotations
    if not UPDATE_EXPECTED and result.subscript_bounds_facts is not None:
        bounds_errors = validate_bounds_annotations(main_src, result.subscript_bounds_facts)
        if bounds_errors:
            pytest.fail("\n".join(bounds_errors), pytrace=False)

    # Validate # tpyc: div_safe/div_checked annotations
    if not UPDATE_EXPECTED and result.div_zero_facts is not None:
        div_errors = validate_div_annotations(main_src, result.div_zero_facts)
        if div_errors:
            pytest.fail("\n".join(div_errors), pytrace=False)

    # Validate # tpyc: cast_safe/cast_checked annotations
    if not UPDATE_EXPECTED and result.cast_safe_facts is not None:
        cast_errors = validate_cast_annotations(main_src, result.cast_safe_facts)
        if cast_errors:
            pytest.fail("\n".join(cast_errors), pytrace=False)
