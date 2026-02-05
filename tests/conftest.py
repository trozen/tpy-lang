"""Shared fixtures and utilities for TurboPython tests."""

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
from tpyc.cli import compile_file, get_module_name
from tpyc.codegen_cpp import CodeGenOptions, CodeGenError
from tpyc.parse import Parser, ParseError
from tpyc.sema import SemanticAnalyzer, SemanticError, Diagnostic
from tpyc.compiler import Compiler, CompileError

# Default options for tests: emit source comments for easier debugging
TEST_CODEGEN_OPTIONS = CodeGenOptions(emit_source_comments=True)

# Paths
TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"    # Normal tests (compile + run)
ERRORS_DIR = TESTS_DIR / "errors"  # Compilation error tests
PANICS_DIR = TESTS_DIR / "panics"  # Runtime panic tests
HARNESS_DIR = TESTS_DIR / "harness"
PROJECT_ROOT = TESTS_DIR.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime" / "cpp" / "include"


def run_cpython(src_file: Path) -> str:
    """Run a TurboPython file with CPython using the test harness."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(HARNESS_DIR)

    result = subprocess.run(
        [sys.executable, str(src_file)],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        pytest.fail(f"CPython execution failed:\n{result.stderr}")

    return result.stdout


def compile_and_run_cpp(build_dir: Path, module_name: str) -> str:
    """Compile generated C++ and run the executable."""
    module_dir = build_dir / f"{module_name}.d"
    cpp_file = module_dir / f"{module_name}.cpp"
    exe_file = build_dir / "program"

    result = subprocess.run(
        ["g++", "-std=c++23", "-I", str(RUNTIME_DIR), "-o", str(exe_file), str(cpp_file), "-lgmp"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        pytest.fail(f"C++ compilation failed:\n{result.stderr}")

    result = subprocess.run([str(exe_file)], capture_output=True, text=True)

    if result.returncode != 0:
        pytest.fail(f"C++ execution failed:\n{result.stderr}")

    return result.stdout


@dataclass
class CompileResult:
    """Result of compiling a TurboPython file."""
    success: bool
    diagnostics: str  # Full diagnostic output
    hpp_path: Path | None = None
    cpp_path: Path | None = None
    # For multi-module compilation: list of all (module_name, hpp_path, cpp_path) tuples
    all_modules: list[tuple[str, Path, Path]] = field(default_factory=list)


def compile_with_diagnostics(src_file: Path, output_dir: Path) -> CompileResult:
    """Compile a TurboPython file and capture diagnostics.

    Returns CompileResult with success status, diagnostics, and output paths.
    Warnings are collected but don't cause failure. Errors cause failure.
    Uses Compiler for multi-module support.
    """
    module_name = get_module_name(src_file)

    try:
        # Use Compiler for multi-module support
        compiler = Compiler(src_file)
        compiled_modules = compiler.compile()

        # Collect warnings from all analyzers
        all_diags = []
        for mod in compiled_modules:
            for d in mod.analyzer.diagnostics:
                all_diags.append(d.format(mod.path.name))
        diagnostics = "\n".join(all_diags) + "\n" if all_diags else ""

        # Get all non-entry modules in dependency order for init calls
        all_imported = [m.name for m in compiled_modules if not m.is_entry_point]

        # Generate code for all modules and track paths
        all_modules = []
        for mod in compiled_modules:
            hpp_path, cpp_path = compiler.generate_code(
                mod, output_dir, TEST_CODEGEN_OPTIONS,
                all_imported_modules=all_imported if mod.is_entry_point else None
            )
            all_modules.append((mod.name, hpp_path, cpp_path))

        # Return paths for the entry point module
        entry_module = next(m for m in compiled_modules if m.is_entry_point)
        module_dir = output_dir / f"{entry_module.name}.d"
        hpp_path = module_dir / f"{entry_module.name}.hpp"
        cpp_path = module_dir / f"{entry_module.name}.cpp"
        return CompileResult(success=True, diagnostics=diagnostics, hpp_path=hpp_path, cpp_path=cpp_path, all_modules=all_modules)

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


def build_and_run(build_dir: Path, module_name: str,
                  all_cpp_files: list[Path] | None = None) -> RunResult:
    """Compile generated C++ and run, capturing all output (including panics).

    Args:
        build_dir: Directory containing generated C++ files.
        module_name: Name of the entry point module.
        all_cpp_files: List of all C++ files to compile (for multi-module).
                       If None, compiles only the entry module.
    """
    exe_file = build_dir / "program"

    # Determine C++ files to compile
    if all_cpp_files is None:
        module_dir = build_dir / f"{module_name}.d"
        cpp_files = [module_dir / f"{module_name}.cpp"]
    else:
        cpp_files = all_cpp_files

    # Compile C++ with include path for cross-module references
    result = subprocess.run(
        ["g++", "-std=c++23", "-I", str(RUNTIME_DIR), "-I", str(build_dir),
         "-o", str(exe_file)] + [str(f) for f in cpp_files] + ["-lgmp"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"C++ compilation failed:\n{result.stderr}")

    # Run and capture output
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
                errors.append(f"Line {ann.line}: expected no diagnostics but got: {line_diags}")
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
                    f"Line {ann.line}: expected {ann.level}(/{ann.pattern}/) but got: {line_diags or 'nothing'}"
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
        assert actual == expected, (
            f"{description} differs.\n"
            f"--- Expected ---\n{expected}\n"
            f"--- Got ---\n{actual}"
        )


def remove_if_exists(path: Path) -> None:
    """Remove a file if it exists (used in update mode to clean up stale files)."""
    if path.exists():
        path.unlink()


def discover_cases():
    """Discover all test cases from cases/, errors/, and panics/ directories.

    Returns list of (name, case_dir, main_src) tuples.
    """
    cases = []

    for base_dir in [CASES_DIR, ERRORS_DIR, PANICS_DIR]:
        if not base_dir.exists():
            continue

        prefix = base_dir.name  # "cases", "errors", or "panics"

        for src_dir in base_dir.rglob("src"):
            if not src_dir.is_dir():
                continue
            case_dir = src_dir.parent
            src_files = list(src_dir.glob("*.tp.py"))
            if not src_files:
                continue

            # Prefer main.tp.py as entry point, otherwise pick first alphabetically
            main_src = None
            for sf in src_files:
                if sf.name == "main.tp.py":
                    main_src = sf
                    break
            if main_src is None:
                main_src = sorted(src_files, key=lambda p: p.name)[0]

            rel_path = case_dir.relative_to(base_dir)
            name = f"{prefix}_{rel_path}".replace("/", "_").replace("\\", "_")
            cases.append((name, case_dir, main_src))

    return cases
