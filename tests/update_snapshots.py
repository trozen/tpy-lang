#!/usr/bin/env python3
"""Update expected snapshots for test cases.

Usage:
    python tests/update_snapshots.py              # Update all cases
    python tests/update_snapshots.py hello        # Update specific case
    python tests/update_snapshots.py panics/int32_add_overflow  # By path

Test type is auto-detected by trying to compile and run:
- Compilation fails → error test (generates diag.txt)
- Runtime panics → panic test (generates diag.txt, panic.txt, .hpp/.cpp)
- Runs successfully → normal test (generates diag.txt, output.txt, .hpp/.cpp)
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"
HARNESS_DIR = TESTS_DIR / "harness"
PROJECT_ROOT = TESTS_DIR.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"

sys.path.insert(0, str(PROJECT_ROOT))
from tpyc.cli import compile_file, get_module_name
from tpyc.codegen_cpp import CodeGenOptions
from tpyc.parse import Parser
from tpyc.sema import SemanticAnalyzer, SemanticError

SNAPSHOT_CODEGEN_OPTIONS = CodeGenOptions(emit_source_comments=True)


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
        print(f"  Warning: CPython execution failed: {result.stderr.strip()}")
        return ""

    return result.stdout


def update_case(case_dir: Path) -> None:
    """Update snapshots for a test case, auto-detecting type."""
    src_dir = case_dir / "src"
    expected_dir = case_dir / "expected"

    src_files = list(src_dir.glob("*.tp.py"))
    if not src_files:
        print(f"No source files in {src_dir}")
        return

    main_src = src_files[0]
    module_name = get_module_name(main_src)
    expected_dir.mkdir(parents=True, exist_ok=True)

    rel_path = case_dir.relative_to(CASES_DIR)
    print(f"Updating {rel_path}...")

    # Step 1: Try to compile (semantic analysis)
    source = main_src.read_text()
    try:
        parser = Parser()
        module = parser.parse(source)
        analyzer = SemanticAnalyzer()
        analyzer.analyze(module)
    except SemanticError as e:
        # Compilation failed → error test
        diag = e.format(main_src.name)
        diag_file = expected_dir / "diag.txt"
        diag_file.write_text(diag + "\n")
        print(f"  [error test] Generated: {diag_file}")
        return

    # Step 2: Compilation succeeded, generate C++ code
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir = Path(tmp_dir)
        compile_file(str(main_src), str(tmp_dir), SNAPSHOT_CODEGEN_OPTIONS)

        # Copy generated files
        module_dir = tmp_dir / f"{module_name}.d"
        for ext in [".hpp", ".cpp"]:
            src = module_dir / f"{module_name}{ext}"
            dst = expected_dir / f"{module_name}{ext}"
            shutil.copy(src, dst)
            print(f"  Generated: {dst}")

        # Empty diag.txt (no warnings yet)
        diag_file = expected_dir / "diag.txt"
        diag_file.write_text("")
        print(f"  Generated: {diag_file}")

        # Step 3: Compile and run C++
        cpp_file = module_dir / f"{module_name}.cpp"
        exe_file = tmp_dir / "program"
        result = subprocess.run(
            ["g++", "-std=c++20", "-I", str(RUNTIME_DIR),
             "-o", str(exe_file), str(cpp_file), "-lgmp"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  Error: C++ compilation failed:\n{result.stderr}")
            return

        # Run the program
        result = subprocess.run([str(exe_file)], capture_output=True, text=True)

        if result.returncode != 0:
            # Runtime panic → panic test
            panic_file = expected_dir / "panic.txt"
            panic_file.write_text(result.stderr)
            print(f"  [panic test] Generated: {panic_file}")
            # Remove output.txt if it exists from previous run
            output_file = expected_dir / "output.txt"
            if output_file.exists():
                output_file.unlink()
        else:
            # Success → normal test
            output = run_cpython(main_src)
            output_file = expected_dir / "output.txt"
            output_file.write_text(output)
            print(f"  [normal test] Generated: {output_file}")
            # Remove panic.txt if it exists from previous run
            panic_file = expected_dir / "panic.txt"
            if panic_file.exists():
                panic_file.unlink()


def find_all_cases() -> list[Path]:
    """Recursively find all test case directories (dirs with src/ subdirectory)."""
    cases = []
    for path in CASES_DIR.rglob("src"):
        if path.is_dir():
            cases.append(path.parent)
    return sorted(cases)


def find_case(name: str) -> Path | None:
    """Find a test case by name or path."""
    # Try as relative path first
    candidate = CASES_DIR / name
    if candidate.exists() and (candidate / "src").exists():
        return candidate

    # Search recursively for matching name
    for case_dir in find_all_cases():
        if case_dir.name == name:
            return case_dir

    return None


def main():
    if len(sys.argv) > 1:
        # Update specific cases
        for case_name in sys.argv[1:]:
            case_dir = find_case(case_name)
            if case_dir:
                update_case(case_dir)
            else:
                print(f"Case not found: {case_name}")
    else:
        # Update all cases
        for case_dir in find_all_cases():
            update_case(case_dir)


if __name__ == "__main__":
    main()
