"""Shared fixtures and utilities for TurboPython tests."""

import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, field

import pytest

# When set, tests update expected files instead of comparing
UPDATE_EXPECTED = os.environ.get("UPDATE_EXPECTED", "").lower() in ("1", "true")

# Import the compiler
sys.path.insert(0, str(Path(__file__).parent.parent))
from tpyc.cli import get_module_name
from tpyc.codegen_cpp import CodeGenOptions, CodeGenError
from tpyc.parse import Parser, ParseError
from tpyc.sema import SemanticAnalyzer, SemanticError, Diagnostic, DiagnosticLevel
from tpyc.compiler import Compiler, CompileError, BuildLayout, CppCompilerConfig

# Default options for tests: emit source comments for easier debugging
TEST_CODEGEN_OPTIONS = CodeGenOptions(emit_source_comments=True)

# Shared C++ compiler config (auto-detects ccache)
CPP_CONFIG = CppCompilerConfig.from_env()

# Paths
TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"    # All tests (grouped by feature)
PROJECT_ROOT = TESTS_DIR.parent
LIB_DIR = PROJECT_ROOT / "lib"
CPY_LIB_DIR = LIB_DIR / "cpy"
TPY_LIB_DIR = LIB_DIR / "tpy"
STDLIB_DIR = LIB_DIR / "stdlib"
DEFAULT_LIB_DIRS = [TPY_LIB_DIR, STDLIB_DIR]
RUNTIME_DIR = PROJECT_ROOT / "runtime" / "cpp" / "include"


def run_cpython(src_file: Path) -> str:
    """Run a TurboPython file with CPython using the test harness."""
    env = os.environ.copy()
    # Include CPython stubs (for tpy module), tpy search root (for tplib),
    # stdlib analogs, and source dir (for multi-module imports)
    src_dir = src_file.parent
    env["PYTHONPATH"] = f"{CPY_LIB_DIR}{os.pathsep}{TPY_LIB_DIR}{os.pathsep}{STDLIB_DIR}{os.pathsep}{src_dir}"

    result = subprocess.run(
        [sys.executable, str(src_file)],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        stderr_text = _filter_lib_traceback(result.stderr)
        pytest.fail(
            f"CPython execution failed for {src_file} (exit code {result.returncode}).\n"
            f"--- stderr ---\n{stderr_text}",
            pytrace=False,
        )

    return result.stdout


@dataclass
class CompileResult:
    """Result of compiling a TurboPython file."""
    success: bool
    diagnostics: str  # Full diagnostic output
    hpp_path: Path | None = None
    cpp_path: Path | None = None
    # For multi-module compilation: list of (module_name, hpp_path, cpp_path, is_local) tuples
    # is_local=True for modules from the test's src/ dir, False for library modules
    all_modules: list[tuple[str, Path, Path, bool]] = field(default_factory=list)
    # Resolved types for variable declarations (from sema), for # tpyc: type(...) validation
    declared_var_types: dict[tuple[int, str], object] | None = None
    # Ptr dereference facts (from sema), for # tpyc: non_null/nullable validation
    ptr_deref_facts: dict[tuple[int, str], bool] | None = None
    # Subscript bounds facts (from sema), for # tpyc: bounds_safe/bounds_checked validation
    subscript_bounds_facts: dict[tuple[int, str], bool] | None = None
    # Division non-zero facts (from sema), for # tpyc: div_safe/div_checked validation
    div_zero_facts: dict[tuple[int, str], bool] | None = None


def _validate_default_int_name(name: str) -> str:
    allowed = {"Int32", "Int64", "BigInt"}
    if name not in allowed:
        pytest.fail(
            f"Invalid default int '{name}'. Expected one of: {', '.join(sorted(allowed))}"
        )
    return name


def load_case_options(case_dir: Path) -> dict[str, str]:
    """Load and validate optional per-case test options.json."""
    options_path = case_dir / "options.json"
    if not options_path.exists():
        return {}

    try:
        raw = json.loads(options_path.read_text())
    except json.JSONDecodeError as e:
        pytest.fail(f"{options_path}: invalid JSON ({e.msg})")

    if not isinstance(raw, dict):
        pytest.fail(f"{options_path}: expected JSON object")

    allowed_keys = {"default_int"}
    unknown = sorted(k for k in raw.keys() if k not in allowed_keys)
    if unknown:
        pytest.fail(f"{options_path}: unsupported keys: {', '.join(unknown)}")

    if "default_int" in raw:
        value = raw["default_int"]
        if not isinstance(value, str):
            pytest.fail(f"{options_path}: 'default_int' must be a string")
        _validate_default_int_name(value)

    return raw


def get_case_default_int(case_dir: Path) -> str:
    """Resolve default integer mode for a test case."""
    options = load_case_options(case_dir)
    return _validate_default_int_name(options.get("default_int", "Int32"))


def compile_with_diagnostics(src_file: Path, output_dir: Path, default_int: str | None = None) -> CompileResult:
    """Compile a TurboPython file and capture diagnostics.

    Returns CompileResult with success status, diagnostics, and output paths.
    Warnings are collected but don't cause failure. Errors cause failure.
    Uses Compiler for multi-module support.
    """
    module_name = get_module_name(src_file)
    default_int = _validate_default_int_name(default_int or "Int32")

    try:
        # Use Compiler for multi-module support
        compiler = Compiler(src_file, default_int=default_int, lib_dirs=DEFAULT_LIB_DIRS)
        compiled_modules = compiler.compile()

        # Collect diagnostics from all analyzers
        all_diags = []
        has_errors = False
        for mod in compiled_modules:
            for d in mod.analyzer.diagnostics:
                all_diags.append(d.format(mod.path.name))
                if d.level == DiagnosticLevel.ERROR:
                    has_errors = True
        diagnostics = "\n".join(all_diags) + "\n" if all_diags else ""

        if has_errors:
            return CompileResult(success=False, diagnostics=diagnostics)

        # Generate code for all modules and track paths
        entry_module = next(m for m in compiled_modules if m.is_entry_point)
        src_dir = src_file.parent.resolve()
        all_modules = []
        for mod in compiled_modules:
            hpp_path, cpp_path = compiler.generate_code(
                mod, output_dir, entry_module_name=entry_module.name,
                options=TEST_CODEGEN_OPTIONS
            )
            is_local = False
            try:
                mod.path.resolve().relative_to(src_dir)
                is_local = True
            except ValueError:
                pass
            all_modules.append((mod.name, hpp_path, cpp_path, is_local))

        # Return paths for the entry point module
        layout = BuildLayout(output_dir, entry_module.name)
        hpp_path = layout.hpp_path(entry_module.name)
        cpp_path = layout.cpp_path(entry_module.name)
        ctx = entry_module.analyzer.ctx if entry_module.analyzer else None
        declared_var_types = ctx.declared_var_types if ctx else None
        ptr_deref_facts = ctx.ptr_deref_facts if ctx else None
        subscript_bounds_facts = ctx.subscript_bounds_facts if ctx else None
        div_zero_facts = ctx.div_zero_facts if ctx else None
        return CompileResult(success=True, diagnostics=diagnostics, hpp_path=hpp_path, cpp_path=cpp_path,
                             all_modules=all_modules, declared_var_types=declared_var_types,
                             ptr_deref_facts=ptr_deref_facts,
                             subscript_bounds_facts=subscript_bounds_facts,
                             div_zero_facts=div_zero_facts)

    except CompileError as e:
        return CompileResult(success=False, diagnostics=e.format() + "\n")
    except SemanticError as e:
        diag = e.format(src_file.name)
        return CompileResult(success=False, diagnostics=diag + "\n")
    except ParseError as e:
        diag = e.format(src_file.name)
        return CompileResult(success=False, diagnostics=diag + "\n")
    except CodeGenError as e:
        diag = e.format(src_file.name)
        return CompileResult(success=False, diagnostics=diag + "\n")


@dataclass
class RunResult:
    """Result of running a compiled program."""
    success: bool      # exit code == 0
    stdout: str
    stderr: str
    returncode: int
    cpp_build_failed: bool = False


def pytest_configure(config):
    """Print ccache status at session start."""
    if CPP_CONFIG.ccache:
        print("C++ compilation: using ccache")
    else:
        print("C++ compilation: ccache not found (install for faster re-runs)")


def find_extra_src_files(case_dir: Path) -> list[Path]:
    """Find extra C++ source files in the test's src/ directory.

    These are compiled alongside the generated C++ to provide stub
    implementations for native functions.
    """
    src_dir = case_dir / "src"
    return sorted(src_dir.glob("*.cpp"))


def find_extra_include_dirs(case_dir: Path) -> list[Path]:
    """Find extra include directories for C++ compilation.

    If the test's src/ directory contains .h files (e.g., native type
    definitions for interop tests), returns it as an include directory.
    """
    src_dir = case_dir / "src"
    if any(src_dir.glob("*.hpp")):
        return [src_dir]
    return []


def find_force_includes(case_dir: Path) -> list[Path]:
    """Find headers to force-include before all generated code.

    Returns .h files from the test's src/ directory. These are injected
    via -include so that native type definitions are visible in generated
    headers without needing # tpy: include() directives.
    """
    src_dir = case_dir / "src"
    return sorted(src_dir.glob("*.hpp"))


def build_and_run(build_dir: Path, module_name: str,
                  all_cpp_files: list[Path] | None = None,
                  extra_src_files: list[Path] | None = None,
                  extra_include_dirs: list[Path] | None = None,
                  force_includes: list[Path] | None = None,
                  build_variant: str = "debug") -> RunResult:
    """Compile generated C++ and run, capturing all output (including panics).

    Args:
        build_dir: Directory containing generated C++ files.
        module_name: Name of the entry point module.
        all_cpp_files: List of all C++ files to compile (for multi-module).
                       If None, compiles only the entry module.
        extra_src_files: Additional C++ source files to include in the build
                         (e.g., stub implementations for native functions).
        extra_include_dirs: Additional include directories for C++ compilation
                            (e.g., directories containing native type headers).
        force_includes: Headers to force-include via -include before all source
                        (e.g., native type definitions for interop tests).
        build_variant: Build variant ("debug" or "release").
    """
    layout = BuildLayout(build_dir, module_name, build_variant=build_variant)

    # Determine C++ files to compile
    if all_cpp_files is None:
        cpp_files = [layout.cpp_path(module_name)]
    else:
        cpp_files = list(all_cpp_files)

    if extra_src_files:
        cpp_files.extend(extra_src_files)

    # Compile C++ with include path for cross-module references
    compile_cmds = layout.build_cpp_commands(
        runtime_include_dir=RUNTIME_DIR,
        cpp_files=cpp_files,
        config=CPP_CONFIG,
        extra_include_dirs=extra_include_dirs or None,
        force_includes=force_includes or None,
    )
    for cmd in compile_cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return RunResult(
                success=False,
                stdout="",
                stderr=result.stderr,
                returncode=result.returncode,
                cpp_build_failed=True,
            )

    # Run and capture output
    exe_file = layout.binary_path()
    result = subprocess.run([str(exe_file)], capture_output=True, text=True)
    return RunResult(
        success=(result.returncode == 0),
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode
    )


@dataclass
class Annotation:
    """A diagnostic annotation from source code."""
    line: int
    level: str  # "error", "warning", "ok"
    pattern: str | None  # Regex pattern (None for "ok")


def parse_annotations(source: str) -> list[Annotation]:
    """Parse # tpyc: annotations from source code.

    Supports:
      # tpyc: error(/pattern/)
      # tpyc: warning(/pattern/)
      # tpyc: ok

    Annotations must be at end of code lines (not in comment-only lines).
    """
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*(error|warning|ok)(?:\s*\(\s*/(.+?)/\s*\))?')

    for lineno, line in enumerate(source.splitlines(), start=1):
        # Skip comment-only lines (annotations must be on code lines)
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        for match in pattern.finditer(line):
            level = match.group(1)
            regex = match.group(2)
            annotations.append(Annotation(line=lineno, level=level, pattern=regex))

    return annotations


def validate_annotations(src_file: Path, diagnostics: str) -> list[str]:
    """Validate that diagnostics match inline annotations.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_annotations(source)
    errors = []

    # Parse diagnostics into (line, level, message) tuples
    diag_pattern = re.compile(rf'^{re.escape(src_file.name)}:(\d+):\s*(error|warning):\s*(.+)$', re.MULTILINE)
    diag_by_line: dict[int, list[tuple[str, str]]] = {}
    for match in diag_pattern.finditer(diagnostics):
        line = int(match.group(1))
        level = match.group(2)
        message = match.group(3)
        diag_by_line.setdefault(line, []).append((level, message))

    # Check each annotation
    for ann in annotations:
        line_diags = diag_by_line.get(ann.line, [])

        if ann.level == "ok":
            # Expect no diagnostics on this line
            if line_diags:
                errors.append(f"{src_file.name}:{ann.line}: expected no diagnostics but got: {line_diags}")
        else:
            # Expect a matching diagnostic
            found = False
            for level, message in line_diags:
                if level == ann.level:
                    if ann.pattern is None or re.search(ann.pattern, message):
                        found = True
                        break
            if not found:
                errors.append(
                    f"{src_file.name}:{ann.line}: expected {ann.level}(/{ann.pattern}/) but got: {line_diags or 'nothing'}"
                )

    return errors


@dataclass
class TypeAnnotation:
    """A type annotation from source code (# tpyc: type(...))."""
    line: int
    expected_type: str


def parse_type_annotations(source: str) -> list[TypeAnnotation]:
    """Parse # tpyc: type(...) annotations from source code."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*type\(\s*(.+?)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            annotations.append(TypeAnnotation(line=lineno, expected_type=match.group(1)))

    return annotations


_VAR_NAME_RE = re.compile(r'\s*(\w+)\s*(?::\s*[\w\[\], .|]+\s*)?=')


def validate_type_annotations(
    src_file: Path,
    declared_var_types: dict[tuple[int, str], object],
) -> list[str]:
    """Validate # tpyc: type(...) annotations against compiler-resolved types.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_type_annotations(source)
    errors = []

    for ann in annotations:
        # Extract variable name from the source line
        line_text = source.splitlines()[ann.line - 1]
        var_match = _VAR_NAME_RE.match(line_text)
        if not var_match:
            errors.append(f"Line {ann.line}: could not extract variable name from line")
            continue
        var_name = var_match.group(1)

        key = (ann.line, var_name)
        actual_type = declared_var_types.get(key)
        if actual_type is None:
            errors.append(
                f"Line {ann.line}: no declared type found for '{var_name}'"
            )
            continue

        actual_str = str(actual_type)
        expected = ann.expected_type

        if expected.startswith('/') and expected.endswith('/'):
            # Regex match
            if not re.search(expected[1:-1], actual_str):
                errors.append(
                    f"Line {ann.line}: expected type matching /{expected[1:-1]}/ "
                    f"for '{var_name}' but got '{actual_str}'"
                )
        else:
            if actual_str != expected:
                errors.append(
                    f"Line {ann.line}: expected type '{expected}' "
                    f"for '{var_name}' but got '{actual_str}'"
                )

    return errors


@dataclass
class NonNullAnnotation:
    """A non-null annotation from source code (# tpyc: non_null/nullable(var))."""
    line: int
    var_name: str
    expected_non_null: bool  # True for non_null, False for nullable


def parse_non_null_annotations(source: str) -> list[NonNullAnnotation]:
    """Parse # tpyc: non_null(var) and # tpyc: nullable(var) annotations."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*(non_null|nullable)\(\s*(\w+)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            kind = match.group(1)
            var_name = match.group(2)
            annotations.append(NonNullAnnotation(
                line=lineno,
                var_name=var_name,
                expected_non_null=(kind == "non_null"),
            ))

    return annotations


def validate_non_null_annotations(
    src_file: Path,
    ptr_deref_facts: dict[tuple[int, str], bool],
) -> list[str]:
    """Validate # tpyc: non_null/nullable annotations against compiler facts.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_non_null_annotations(source)
    errors = []

    for ann in annotations:
        key = (ann.line, ann.var_name)
        actual = ptr_deref_facts.get(key)
        if actual is None:
            errors.append(
                f"Line {ann.line}: no ptr dereference found for '{ann.var_name}'"
            )
            continue
        if ann.expected_non_null and not actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' to be non_null "
                f"but deref_check is used"
            )
        elif not ann.expected_non_null and actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' to be nullable "
                f"but deref_check is skipped (proven non-null)"
            )

    return errors


@dataclass
class BoundsSafeAnnotation:
    """A bounds annotation from source code (# tpyc: bounds_safe/bounds_checked)."""
    line: int
    var_name: str
    expected_safe: bool  # True for bounds_safe, False for bounds_checked


def parse_bounds_annotations(source: str) -> list[BoundsSafeAnnotation]:
    """Parse # tpyc: bounds_safe and # tpyc: bounds_checked annotations."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*(bounds_safe|bounds_checked)\(\s*(\w+)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            kind = match.group(1)
            var_name = match.group(2)
            annotations.append(BoundsSafeAnnotation(
                line=lineno,
                var_name=var_name,
                expected_safe=(kind == "bounds_safe"),
            ))

    return annotations


def validate_bounds_annotations(
    src_file: Path,
    subscript_bounds_facts: dict[tuple[int, str], bool],
) -> list[str]:
    """Validate # tpyc: bounds_safe/bounds_checked annotations against compiler facts.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_bounds_annotations(source)
    errors = []

    for ann in annotations:
        key = (ann.line, ann.var_name)
        actual = subscript_bounds_facts.get(key)
        if actual is None:
            errors.append(
                f"Line {ann.line}: no subscript access found for '{ann.var_name}'"
            )
            continue
        if ann.expected_safe and not actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' subscript to be bounds_safe "
                f"but bounds check is used"
            )
        elif not ann.expected_safe and actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' subscript to be bounds_checked "
                f"but bounds check is skipped (proven safe)"
            )

    return errors


@dataclass
class DivSafeAnnotation:
    """A division annotation from source code (# tpyc: div_safe/div_checked)."""
    line: int
    var_name: str
    expected_safe: bool  # True for div_safe, False for div_checked


def parse_div_annotations(source: str) -> list[DivSafeAnnotation]:
    """Parse # tpyc: div_safe and # tpyc: div_checked annotations."""
    annotations = []
    pattern = re.compile(r'#\s*tpyc:\s*(div_safe|div_checked)\(\s*(\w+)\s*\)')

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            continue
        match = pattern.search(line)
        if match:
            kind = match.group(1)
            var_name = match.group(2)
            annotations.append(DivSafeAnnotation(
                line=lineno,
                var_name=var_name,
                expected_safe=(kind == "div_safe"),
            ))

    return annotations


def validate_div_annotations(
    src_file: Path,
    div_zero_facts: dict[tuple[int, str], bool],
) -> list[str]:
    """Validate # tpyc: div_safe/div_checked annotations against compiler facts.

    Returns list of validation errors (empty if all pass).
    """
    source = src_file.read_text()
    annotations = parse_div_annotations(source)
    errors = []

    for ann in annotations:
        key = (ann.line, ann.var_name)
        actual = div_zero_facts.get(key)
        if actual is None:
            errors.append(
                f"Line {ann.line}: no division/modulo found for '{ann.var_name}'"
            )
            continue
        if ann.expected_safe and not actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' divisor to be div_safe "
                f"but zero check is used"
            )
        elif not ann.expected_safe and actual:
            errors.append(
                f"Line {ann.line}: expected '{ann.var_name}' divisor to be div_checked "
                f"but zero check is skipped (proven non-zero)"
            )

    return errors


def check_or_update(actual: str, expected_file: Path, description: str) -> None:
    """Compare actual with expected, or update expected if UPDATE_EXPECTED is set.

    In update mode, creates parent directories and writes the file.
    In test mode, asserts that actual matches expected (missing file = empty expected).
    """
    if UPDATE_EXPECTED:
        expected_file.parent.mkdir(parents=True, exist_ok=True)
        expected_file.write_text(actual)
    else:
        expected = expected_file.read_text() if expected_file.exists() else ""
        if actual != expected:
            diff = _format_unified_diff(expected, actual, fromfile=str(expected_file), tofile="actual")
            pytest.fail(
                f"{description} differs: {expected_file}\n{diff}",
                pytrace=False,
            )


def _format_unified_diff(expected: str, actual: str, fromfile: str, tofile: str, context: int = 3) -> str:
    """Return a readable unified diff for expected vs actual text."""
    diff_lines = list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
            n=context,
        )
    )
    if not diff_lines:
        return "(no visible line diff; content may differ by trailing newline or whitespace)"
    return "\n".join(diff_lines)


def _filter_lib_traceback(stderr: str) -> str:
    """Hide lib/tpy frames from traceback output while preserving the error."""
    lines = stderr.splitlines()
    filtered: list[str] = []
    for line in lines:
        normalized = line.replace("\\", "/")
        if '/lib/tpy/' in normalized or '/lib/cpy/' in normalized:
            continue
        filtered.append(line)
    out = "\n".join(filtered).strip()
    return out or stderr.strip()



def _discover_from_dirs(base_dirs: list[Path]):
    """Discover test cases from the given directories.

    Returns list of (name, case_dir, main_src) tuples.
    """
    cases = []

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue

        prefix = base_dir.name

        for src_dir in base_dir.rglob("src"):
            if not src_dir.is_dir():
                continue
            case_dir = src_dir.parent
            src_files = list(src_dir.glob("*.py"))
            if not src_files:
                continue

            # Prefer main.py as entry point, otherwise pick first alphabetically
            main_src = None
            for sf in src_files:
                if sf.name == "main.py":
                    main_src = sf
                    break
            if main_src is None:
                main_src = sorted(src_files, key=lambda p: p.name)[0]

            rel_path = case_dir.relative_to(base_dir)
            name = f"{prefix}/{rel_path}".replace("\\", "/")
            cases.append((name, case_dir, main_src))

    return cases


def discover_cases():
    """Discover all test cases from cases/ directory.

    Returns list of (name, case_dir, main_src) tuples.
    """
    return _discover_from_dirs([CASES_DIR])


def discover_success_cases():
    """Discover success test cases only (excludes error_ and panic_ prefixed tests).

    Returns list of (name, case_dir, main_src) tuples.
    """
    return [
        (name, case_dir, main_src)
        for name, case_dir, main_src in _discover_from_dirs([CASES_DIR])
        if not case_dir.name.startswith(("error_", "panic_"))
    ]
