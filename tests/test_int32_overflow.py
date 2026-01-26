"""Test Int32 overflow detection - these operations should panic."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Paths
TESTS_DIR = Path(__file__).parent
PROJECT_ROOT = TESTS_DIR.parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"


def compile_and_run(source: str) -> tuple[int, str, str]:
    """Compile and run TurboPython source, returning (returncode, stdout, stderr)."""
    from tpyc.cli import compile_file
    from tpyc.codegen_cpp import CodeGenOptions

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Write source file
        src_file = tmpdir / "test.tp.py"
        src_file.write_text(source)

        # Compile to C++ (writes files to output_dir)
        hpp_path, cpp_path = compile_file(
            str(src_file),
            str(tmpdir),
            options=CodeGenOptions(emit_source_comments=False)
        )

        # Compile C++
        exe_file = tmpdir / "test"
        result = subprocess.run(
            ["g++", "-std=c++20", "-I", str(RUNTIME_DIR),
             "-o", str(exe_file), str(cpp_path), "-lgmp"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.fail(f"C++ compilation failed:\n{result.stderr}")

        # Run and capture output
        result = subprocess.run([str(exe_file)], capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr


class TestInt32Overflow:
    """Test that Int32 overflow operations panic."""

    def test_addition_overflow(self):
        """INT32_MAX + 1 should panic."""
        source = """
from tpy import Int32
x: Int32 = 2147483647  # INT32_MAX
y: Int32 = x + 1
print(y)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode != 0, "Should have panicked on overflow"
        assert "overflow" in stderr.lower() or "panic" in stderr.lower()

    def test_subtraction_overflow(self):
        """INT32_MIN - 1 should panic."""
        source = """
from tpy import Int32
x: Int32 = -2147483648  # INT32_MIN
y: Int32 = x - 1
print(y)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode != 0, "Should have panicked on overflow"
        assert "overflow" in stderr.lower() or "panic" in stderr.lower()

    def test_multiplication_overflow(self):
        """Large multiplication should panic."""
        source = """
from tpy import Int32
x: Int32 = 2147483647  # INT32_MAX
y: Int32 = x * 2
print(y)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode != 0, "Should have panicked on overflow"
        assert "overflow" in stderr.lower() or "panic" in stderr.lower()

    def test_division_overflow(self):
        """INT32_MIN // -1 should panic (result is INT32_MAX + 1)."""
        source = """
from tpy import Int32
x: Int32 = -2147483648  # INT32_MIN
y: Int32 = -1
z: Int32 = x // y
print(z)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode != 0, "Should have panicked on overflow"
        assert "overflow" in stderr.lower() or "panic" in stderr.lower()

    def test_negation_overflow(self):
        """-INT32_MIN should panic."""
        source = """
from tpy import Int32
x: Int32 = -2147483648  # INT32_MIN
y: Int32 = -x
print(y)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode != 0, "Should have panicked on overflow"
        assert "overflow" in stderr.lower() or "panic" in stderr.lower()

    def test_division_by_zero(self):
        """Division by zero should panic."""
        source = """
from tpy import Int32
x: Int32 = 42
y: Int32 = 0
z: Int32 = x // y
print(z)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode != 0, "Should have panicked on division by zero"
        assert "zero" in stderr.lower() or "panic" in stderr.lower()

    def test_modulo_by_zero(self):
        """Modulo by zero should panic."""
        source = """
from tpy import Int32
x: Int32 = 42
y: Int32 = 0
z: Int32 = x % y
print(z)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode != 0, "Should have panicked on division by zero"
        assert "zero" in stderr.lower() or "panic" in stderr.lower()


class TestInt32ValidOperations:
    """Test that valid Int32 operations at boundary values work correctly."""

    def test_max_value(self):
        """Operations at INT32_MAX that don't overflow."""
        source = """
from tpy import Int32
x: Int32 = 2147483647  # INT32_MAX
print(x)
print(x - 1)
print(x // 2)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode == 0, f"Should not panic: {stderr}"
        lines = stdout.strip().split('\n')
        assert lines[0] == "2147483647"
        assert lines[1] == "2147483646"
        assert lines[2] == "1073741823"

    def test_min_value(self):
        """Operations at INT32_MIN that don't overflow."""
        source = """
from tpy import Int32
x: Int32 = -2147483648  # INT32_MIN
print(x)
print(x + 1)
print(x // 2)
"""
        returncode, stdout, stderr = compile_and_run(source)
        assert returncode == 0, f"Should not panic: {stderr}"
        lines = stdout.strip().split('\n')
        assert lines[0] == "-2147483648"
        assert lines[1] == "-2147483647"
        assert lines[2] == "-1073741824"
