"""Shared fixtures and utilities for TurboPython tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Import the compiler
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
