#!/usr/bin/env python3
"""Update expected snapshots for test cases.

Usage:
    python tests/update_snapshots.py              # Update all cases
    python tests/update_snapshots.py hello        # Update specific case
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

sys.path.insert(0, str(PROJECT_ROOT))
from tpyc.cli import compile_file, get_module_name
from tpyc.codegen_cpp import CodeGenOptions

# Same options as tests: emit source comments
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
        print(f"Warning: CPython execution failed for {src_file}:")
        print(result.stderr)
        return ""

    return result.stdout


def update_case(case_name: str) -> None:
    """Update snapshots for a single test case."""
    case_dir = CASES_DIR / case_name
    src_dir = case_dir / "src"
    expected_dir = case_dir / "expected"

    src_files = list(src_dir.glob("*.tp.py"))
    if not src_files:
        print(f"No source files in {src_dir}")
        return

    main_src = src_files[0]
    module_name = get_module_name(main_src)
    expected_dir.mkdir(parents=True, exist_ok=True)

    # Compile to temp directory (creates {tmp}/{module}.d/{module}.{hpp,cpp})
    print(f"Updating {case_name}...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        compile_file(str(main_src), tmp_dir, SNAPSHOT_CODEGEN_OPTIONS)

        # Copy generated files to expected directory with module name
        module_dir = Path(tmp_dir) / f"{module_name}.d"
        for ext in [".hpp", ".cpp"]:
            src = module_dir / f"{module_name}{ext}"
            dst = expected_dir / f"{module_name}{ext}"
            shutil.copy(src, dst)
            print(f"  Generated: {dst}")

    # Run CPython and save output
    output = run_cpython(main_src)
    output_file = expected_dir / "output.txt"
    output_file.write_text(output)
    print(f"  Generated: {output_file}")


def main():
    if len(sys.argv) > 1:
        # Update specific cases
        for case_name in sys.argv[1:]:
            if (CASES_DIR / case_name).exists():
                update_case(case_name)
            else:
                print(f"Case not found: {case_name}")
    else:
        # Update all cases
        for case_dir in sorted(CASES_DIR.iterdir()):
            if case_dir.is_dir() and (case_dir / "src").exists():
                update_case(case_dir.name)


if __name__ == "__main__":
    main()
