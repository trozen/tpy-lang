"""Tests for the TurboPython compiler.

Tests verify:
1. Code generation produces expected C++ output (snapshot testing)
2. Compiled C++ output matches CPython output (behavioral testing)
"""

import os
import subprocess
from pathlib import Path

import pytest

# Import the compiler
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from tpyc.cli import compile_file, get_module_name

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
        ["g++", "-std=c++17", "-I", str(RUNTIME_DIR), "-o", str(exe_file), str(cpp_file)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        pytest.fail(f"C++ compilation failed:\n{result.stderr}")

    result = subprocess.run([str(exe_file)], capture_output=True, text=True)

    if result.returncode != 0:
        pytest.fail(f"C++ execution failed:\n{result.stderr}")

    return result.stdout


def make_codegen_test(case_name: str):
    """Create a codegen snapshot test for a case."""
    def test_func(tmp_path):
        case_dir = CASES_DIR / case_name
        src_dir = case_dir / "src"
        expected_dir = case_dir / "expected"

        src_files = list(src_dir.glob("*.tp.py"))
        assert src_files, f"No .tp.py files found in {src_dir}"

        main_src = src_files[0]
        module_name = get_module_name(main_src)
        compile_file(str(main_src), str(tmp_path))

        # Generated files are in {module_name}.d/ subdirectory
        module_dir = tmp_path / f"{module_name}.d"

        for ext in [".hpp", ".cpp"]:
            expected_file = expected_dir / f"{module_name}{ext}"
            generated_file = module_dir / f"{module_name}{ext}"
            assert generated_file.exists(), f"Expected {module_name}{ext} to be generated"

            expected_content = expected_file.read_text()
            generated_content = generated_file.read_text()

            assert generated_content == expected_content, (
                f"Generated {module_name}{ext} differs from expected.\n"
                f"--- Expected ---\n{expected_content}\n"
                f"--- Generated ---\n{generated_content}"
            )

    return test_func


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
        compile_file(str(main_src), str(tmp_path))
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


# Discover cases and generate test functions
def _generate_tests():
    if not CASES_DIR.exists():
        return

    for case_dir in sorted(CASES_DIR.iterdir()):
        if case_dir.is_dir() and (case_dir / "src").exists():
            case_name = case_dir.name
            globals()[f"test_{case_name}_codegen"] = make_codegen_test(case_name)
            globals()[f"test_{case_name}_output"] = make_output_test(case_name)


_generate_tests()
