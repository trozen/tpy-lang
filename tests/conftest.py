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
from tpyc.cli import get_module_name
from tpyc.codegen_cpp import CodeGenOptions, CodeGenError
from tpyc.parse import Parser, ParseError
from tpyc.sema import SemanticAnalyzer, SemanticError, Diagnostic
from tpyc.compiler import Compiler, CompileError, BuildLayout, CppCompilerConfig

# Default options for tests: emit source comments for easier debugging
TEST_CODEGEN_OPTIONS = CodeGenOptions(emit_source_comments=True)

# Shared C++ compiler config (auto-detects ccache)
CPP_CONFIG = CppCompilerConfig.from_env()

# Paths
TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"    # All tests (grouped by feature)
HARNESS_DIR = TESTS_DIR / "harness"
PROJECT_ROOT = TESTS_DIR.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime" / "cpp" / "include"


def run_cpython(src_file: Path) -> str:
    """Run a TurboPython file with CPython using the test harness."""
    env = os.environ.copy()
    # Include both harness dir and source dir for multi-module imports
    src_dir = src_file.parent
    env["PYTHONPATH"] = f"{HARNESS_DIR}{os.pathsep}{src_dir}"

    result = subprocess.run(
        [sys.executable, str(src_file)],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        pytest.fail(f"CPython execution failed:\n{result.stderr}")

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

        # Generate code for all modules and track paths
        entry_module = next(m for m in compiled_modules if m.is_entry_point)
        all_modules = []
        for mod in compiled_modules:
            hpp_path, cpp_path = compiler.generate_code(
                mod, output_dir, entry_module_name=entry_module.name,
                options=TEST_CODEGEN_OPTIONS
            )
            all_modules.append((mod.name, hpp_path, cpp_path))

        # Return paths for the entry point module
        layout = BuildLayout(output_dir, entry_module.name)
        hpp_path = layout.hpp_path(entry_module.name)
        cpp_path = layout.cpp_path(entry_module.name)
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


def build_and_run(build_dir: Path, module_name: str,
                  all_cpp_files: list[Path] | None = None,
                  extra_src_files: list[Path] | None = None,
                  build_variant: str = "debug") -> RunResult:
    """Compile generated C++ and run, capturing all output (including panics).

    Args:
        build_dir: Directory containing generated C++ files.
        module_name: Name of the entry point module.
        all_cpp_files: List of all C++ files to compile (for multi-module).
                       If None, compiles only the entry module.
        extra_src_files: Additional C++ source files to include in the build
                         (e.g., stub implementations for native functions).
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
    )
    for cmd in compile_cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            pytest.fail(f"C++ compilation failed:\n{result.stderr}")

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
