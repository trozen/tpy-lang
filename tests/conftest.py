"""Shared fixtures and utilities for TurboPython tests."""

import os
import re
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass

import pytest

# Import the compiler
sys.path.insert(0, str(Path(__file__).parent.parent))
from tpyc.cli import compile_file, get_module_name
from tpyc.codegen_cpp import CodeGenOptions
from tpyc.parse import Parser
from tpyc.sema import SemanticAnalyzer, SemanticError

# Default options for tests: emit source comments for easier debugging
TEST_CODEGEN_OPTIONS = CodeGenOptions(emit_source_comments=True)

# Paths
TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"
HARNESS_DIR = TESTS_DIR / "harness"
PROJECT_ROOT = TESTS_DIR.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"


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
        ["g++", "-std=c++20", "-I", str(RUNTIME_DIR), "-o", str(exe_file), str(cpp_file), "-lgmp"],
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


def compile_with_diagnostics(src_file: Path, output_dir: Path) -> CompileResult:
    """Compile a TurboPython file and capture diagnostics.

    Returns CompileResult with success status, diagnostics, and output paths.
    """
    module_name = get_module_name(src_file)
    source = src_file.read_text()

    try:
        parser = Parser()
        module = parser.parse(source)

        analyzer = SemanticAnalyzer()
        analyzer.analyze(module)

        # If we get here, compilation succeeded
        hpp_path, cpp_path = compile_file(str(src_file), str(output_dir), TEST_CODEGEN_OPTIONS)
        return CompileResult(success=True, diagnostics="", hpp_path=hpp_path, cpp_path=cpp_path)

    except SemanticError as e:
        diag = e.format(src_file.name)
        return CompileResult(success=False, diagnostics=diag + "\n")


@dataclass
class RunResult:
    """Result of running a compiled program."""
    success: bool      # exit code == 0
    stdout: str
    stderr: str
    returncode: int


def build_and_run(build_dir: Path, module_name: str) -> RunResult:
    """Compile generated C++ and run, capturing all output (including panics)."""
    module_dir = build_dir / f"{module_name}.d"
    cpp_file = module_dir / f"{module_name}.cpp"
    exe_file = build_dir / "program"

    # Compile C++
    result = subprocess.run(
        ["g++", "-std=c++20", "-I", str(RUNTIME_DIR), "-o", str(exe_file), str(cpp_file), "-lgmp"],
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
