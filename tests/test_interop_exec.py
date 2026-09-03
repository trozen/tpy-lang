"""CPython-extension ext-exec harness: the runtime half of interop testing.

Each tests/interop/<case>/src/ holds an `# tpy: ext_module` source plus a
driver.py (and optional ext_checks.py), mirroring the tests/cases input/
output split; expected/ and the gitignored __tpyc__/ stay at the case root.
For every case this proves, in the shape of the main snapshot harness:

  - COMP/SNAPSHOT (always): tpyc emits the module .hpp/.cpp + the CPython glue
    `_ext.cpp`; all three are snapshotted into expected/, and the front-end
    diagnostics into expected/diag.txt (with `# tpyc:` annotation validation,
    as in the main harness). This alone catches any glue/marshaller codegen
    or diagnostics drift in CI.
  - EXT-EXEC (cached, like the exec phase; Linux and macOS): tpyc's first-class
    .so build mode produces an importable `<mod>.so` (facade only -- never
    links libpython); CPython imports it and runs driver.py. Gated by a
    content-addressed marker so it re-verifies once per change, then skips.
    The link recipe is per-target (`shared_link_flags`): ELF `-shared`, Mach-O
    `-bundle -undefined dynamic_lookup`. Windows is the remaining gap, so the
    phase skips there while the snapshot, cpy-parity, and facade self-check
    still run. Under --build-only or a cross toolchain only the snapshot half
    runs (the .so can be neither imported here nor, for cross, built at all).
  - CPY-PARITY (always, cheap): the SAME driver.py over the TPy source (lib/cpy
    stubs) must match the ext-exec output snapshot.
  - ext_checks.py (ext-only marshalling-error cases) runs against the .so.

`test_facade_selfcheck` compiles the hand-mirrored facade against the real
Python ABI once (skipped when Python dev headers are absent).
"""

import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from conftest import (
    UPDATE_EXPECTED,
    CPP_CONFIG,
    PROJECT_ROOT,
    RUNTIME_DIR,
    check_or_update,
    compute_ext_exec_fingerprint,
    discover_interop_cases,
    exec_is_cross,
    exec_pass_is_cached,
    record_exec_pass,
    run_cpython,
    validate_annotations,
)

_FACADE_SELFCHECK = (
    PROJECT_ROOT / "runtime" / "cpp" / "tests" / "interop"
    / "cpython_facade_selfcheck.cpp"
)
# The abi3 floor: every glue TU and the self-check are built against the 3.12
# limited API.
_LIMITED_API = "-DPy_LIMITED_API=0x030c0000"

# The `.so` build leaves the Python symbols undefined, to be resolved against
# the host interpreter at import; `shared_link_flags` picks the per-target
# recipe (ELF `-shared` / Mach-O `-bundle -undefined dynamic_lookup`). Windows
# is the remaining gap (MSVC, and no statement-expression extension), so the
# build + ext-exec run is gated here; the portable snapshot, cpy-parity, and
# facade self-check still run everywhere.
_EXT_BUILD_SUPPORTED = platform.system() in ("Linux", "Darwin")


def _run_tpyc(args: list[str], what: str) -> str:
    """Invoke the real `tpyc` CLI (exercises the first-class .so build mode)
    and return stderr -- under `-q` on the emit path that is exactly the
    front-end diagnostics, which the caller snapshots into diag.txt."""
    cmd = [sys.executable, "-m", "tpyc", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        pytest.fail(
            f"tpyc {what} failed ({' '.join(args)}):\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}",
            pytrace=False,
        )
    return result.stderr


def _assert_facade_only(so_path: Path) -> None:
    """A tpy extension links the hand-mirrored facade, not libpython: the
    symbols resolve against the host interpreter at import time.

    Both platforms that build the .so are covered, each with its own linked-
    libraries tool -- otherwise the guarantee would silently go unchecked on
    whichever one lacks it."""
    tool = {"Linux": "ldd", "Darwin": "otool"}.get(platform.system())
    if tool is None:
        return
    argv = [tool, str(so_path)] if tool == "ldd" else [tool, "-L", str(so_path)]
    result = subprocess.run(argv, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        # otool echoes the object's own path as a header line; a repo path that
        # itself contains "python" (e.g. turbo-python) would false-positive.
        if line.startswith(str(so_path)):
            continue
        if "python" in line.lower():
            pytest.fail(
                f"{so_path.name} links libpython ({line.strip()}); the facade "
                f"must keep Python.h and libpython out of the .so",
                pytrace=False,
            )


def test_facade_selfcheck(request):
    """Compile the facade layout/constant guard against the real Python ABI.

    A hand-mirroring slip in tpy/interop/cpython_abi.hpp becomes a compile
    error here instead of silent ABI corruption in a shipped .so. Needs Python
    dev headers; skipped (not failed) when they're unavailable."""
    py_include = sysconfig.get_path("include")
    if not py_include or not (Path(py_include) / "Python.h").exists():
        pytest.skip("Python dev headers (Python.h) unavailable")
    if request.config.getoption("--no-exec") or exec_is_cross():
        # This is a COMPILE check, so it needs a real host toolchain: --no-exec
        # builds nothing (and a toolchain-free run has no compiler), and a
        # cross compiler paired with the host's Python.h checks nothing.
        pytest.skip("facade self-check needs a host toolchain")
    cmd = [
        *CPP_CONFIG.compiler, f"-std={CPP_CONFIG.std}", _LIMITED_API,
        "-I", str(RUNTIME_DIR), "-I", py_include,
        "-c", str(_FACADE_SELFCHECK), "-o", os.devnull,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(
            f"CPython facade self-check failed to compile against the real "
            f"Python ABI:\n--- stderr ---\n{result.stderr}",
            pytrace=False,
        )


_INTEROP_CASES = [
    pytest.param(case_dir, mod_py, id=name)
    for name, case_dir, mod_py in discover_interop_cases()
]


@pytest.mark.parametrize("case_dir, mod_py", _INTEROP_CASES)
def test_exec_on_ext_module_rejected(case_dir, mod_py, tmp_path):
    """`tpyc --exec` on an `# tpy: ext_module` must fail loudly: a .so is not
    runnable, so the CLI rejects it instead of building something it can't
    launch."""
    cmd = [sys.executable, "-m", "tpyc", str(mod_py), "-x", "-o", str(tmp_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if result.returncode == 0:
        pytest.fail(
            f"tpyc --exec on ext_module {mod_py.name} unexpectedly succeeded:\n"
            f"{result.stdout}",
            pytrace=False,
        )
    if "ext_module" not in result.stderr or ".so" not in result.stderr:
        pytest.fail(
            f"tpyc --exec rejection lacked the expected message:\n{result.stderr}",
            pytrace=False,
        )


@pytest.mark.parametrize("case_dir, mod_py", _INTEROP_CASES)
def test_interop_exec(case_dir, mod_py, request):
    build_dir = case_dir / "__tpyc__"
    expected_dir = case_dir / "expected"
    mod = mod_py.stem
    driver = case_dir / "src" / "driver.py"
    ext_checks = case_dir / "src" / "ext_checks.py"
    cxx = request.config.getoption("--cxx")
    force = (
        UPDATE_EXPECTED
        or request.config.getoption("--force-exec")
        or request.config.getoption("--clean")
    )
    no_exec = request.config.getoption("--no-exec")
    build_only = request.config.getoption("--build-only") or exec_is_cross()

    shutil.rmtree(build_dir, ignore_errors=True)

    # ----- COMP / SNAPSHOT (always) ------------------------------------------
    # Emit-only (no -b): codegen writes the module .hpp/.cpp and the glue
    # `_ext.cpp` regardless of building, so this gives us everything to
    # snapshot and to fingerprint the build inputs -- without paying the C++
    # build on a cache hit.
    # `-q` suppresses progress lines, so stderr is exactly the front-end
    # diagnostics -- pinned in diag.txt (the exec marker cache never skips
    # this phase, so a lost or spurious warning can't hide behind it).
    diag = _run_tpyc([str(mod_py), "-q", "-o", str(build_dir), "--cxx", cxx], "emit")
    src_prefix = str((case_dir / "src").relative_to(PROJECT_ROOT)) + "/"
    diag = diag.replace(src_prefix, "")
    check_or_update(diag, expected_dir / "diag.txt", "Diagnostics")

    if not UPDATE_EXPECTED:
        annotation_errors: list[str] = []
        for src_file in sorted((case_dir / "src").glob("*.py")):
            annotation_errors.extend(validate_annotations(src_file, diag))
        if annotation_errors:
            pytest.fail("\n".join(annotation_errors), pytrace=False)

    gen_hpp = build_dir / "include" / f"{mod}.hpp"
    gen_cpp = build_dir / "src" / f"{mod}.cpp"
    gen_ext = build_dir / "src" / f"{mod}_ext.cpp"
    for gen, rel in (
        (gen_hpp, Path("include") / f"{mod}.hpp"),
        (gen_cpp, Path("src") / f"{mod}.cpp"),
        (gen_ext, Path("src") / f"{mod}_ext.cpp"),
    ):
        if not gen.exists():
            pytest.fail(f"{gen} not generated", pytrace=False)
        check_or_update(gen.read_text(), expected_dir / rel, str(rel))

    output_txt = expected_dir / "output.txt"

    # ----- EXT-EXEC build + run (cached marker, like the exec phase) ---------
    # Skipped under --no-exec (no C++ build), under build-only/cross (the .so
    # is imported+RUN under host CPython and records exec markers -- neither
    # is possible or wanted; a cross .so also needs the target's Python.h),
    # and on platforms with no link recipe yet (see _EXT_BUILD_SUPPORTED).
    # The snapshot half above always runs.
    if not no_exec and not build_only and _EXT_BUILD_SUPPORTED:
        companions = [driver] + ([ext_checks] if ext_checks.exists() else [])
        fingerprint = compute_ext_exec_fingerprint([gen_hpp, gen_cpp, gen_ext], companions)
        must_build = force or not output_txt.exists() or not exec_pass_is_cached(fingerprint)

        if must_build:
            _run_tpyc([str(mod_py), "-b", "-o", str(build_dir), "--cxx", cxx], "build")
            so_path = build_dir / "debug" / f"{mod}.so"
            if not so_path.exists():
                pytest.fail(f"extension .so not built: {so_path}", pytrace=False)
            _assert_facade_only(so_path)

            # CPython imports the .so by stem from the run dir (sys.path[0]);
            # only the .so + companions live there, so `import {mod}` binds the
            # compiled extension, not the source.
            run_dir = build_dir / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(so_path, run_dir / f"{mod}.so")
            shutil.copy(driver, run_dir / "driver.py")
            ext_out = run_cpython(run_dir / "driver.py")
            check_or_update(ext_out, output_txt, "ext-exec output")

            if ext_checks.exists():
                shutil.copy(ext_checks, run_dir / "ext_checks.py")
                run_cpython(run_dir / "ext_checks.py")  # asserts; nonzero -> fail

            record_exec_pass(fingerprint, request.node.name)

    # ----- CPY-PARITY ---------------------------------------------------------
    # The SAME driver over the TPy source via lib/cpy stubs must reproduce the
    # ext-exec snapshot. run_cpython puts lib/cpy + the driver's src/ dir
    # (holding the source) on PYTHONPATH, so `import {mod}` binds the .py
    # here. Compares to the committed output.txt (under --no-exec the build
    # didn't refresh it). Skipped under build-only/cross for symmetry with
    # the main harness's cpy phase (toolchain-independent, covered by
    # ordinary runs).
    if output_txt.exists() and not build_only:
        cpy_out = run_cpython(driver)
        check_or_update(cpy_out, output_txt, "cpy-parity output", compare_only=True)
