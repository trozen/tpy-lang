"""Self-checks for runtime headers whose contract is not observable from
generated code alone.

The snapshot suite covers the runtime indirectly: a header bug shows up as a
wrong `output.txt` in whichever case happens to exercise it. That leaves the
shapes no case reaches -- and for storage-form choices the gap is exactly where
the dangerous ones live, since a wrong choice is a silent copy or a dangling
pointer rather than a diagnostic. These tests compile and run a C++ TU directly
so the guarantee is pinned at its source.
"""
import os
import subprocess
from pathlib import Path

import pytest

from conftest import CPP_CONFIG, PROJECT_ROOT, RUNTIME_DIR, exec_is_cross

_CPP_TESTS = PROJECT_ROOT / "runtime" / "cpp" / "tests"

# (source, what it pins) -- each is a standalone main() returning non-zero on
# failure, so the C++ file owns its own assertions.
_SELFCHECKS = [
    ("test_frame_slot_forms.cpp", "resumable-frame slot storage forms"),
    ("test_bigint_small_ops.cpp", "BigInt small-operation values and allocations"),
    ("test_math_exception_policy.cpp", "math exceptions and IEEE special values"),
    ("test_seq_contains_identity.cpp", "containment's identity-before-== rule"),
    ("test_dict_default_args.cpp", "dict get/pop/setdefault default deduction"),
    ("test_yield_slot_forms.cpp",
     "the generic yield slot's spelling, const-ness and copy count"),
    ("test_next_step_result.cpp",
     "the iterator step result's size and the range-for adapter's stepping"),
    ("test_owning_combinator_move.cpp",
     "owning combinators stay movable until their first pull"),
]


# Self-checks also built the way a release build sees the headers: their
# debug-only guards compile out under NDEBUG, so the guarded paths must hold
# without them.
_RELEASE_SHAPE = {"test_owning_combinator_move.cpp"}
_RELEASE_FLAGS = ("-O2", "-DNDEBUG")

_BUILDS = [(s, w, ()) for s, w in _SELFCHECKS] + [
    (s, w, _RELEASE_FLAGS) for s, w in _SELFCHECKS if s in _RELEASE_SHAPE]


@pytest.mark.parametrize(
    "source, what, flags", _BUILDS,
    ids=[s + ("-release" if f else "") for s, _, f in _BUILDS])
def test_runtime_selfcheck(source, what, flags, request, tmp_path):
    """Build and run a runtime self-check TU.

    Needs a host toolchain that can also RUN the result, so a cross compiler
    only gets the compile half (a link+run would target the wrong machine).
    """
    if request.config.getoption("--no-exec"):
        pytest.skip(f"{what}: --no-exec builds nothing")
    src = _CPP_TESTS / source
    binary = tmp_path / Path(source).stem
    # A cross driver would hand the link to the host's ELF ld, which rejects
    # the Mach-O flags -- so stop at the object; the run is skipped below.
    cross = exec_is_cross()
    cmd = [
        *CPP_CONFIG.compiler, f"-std={CPP_CONFIG.std}", *flags,
        "-I", str(RUNTIME_DIR),
        *(["-c"] if cross else []),
        str(src), "-o", str(binary.with_suffix(".o") if cross else binary),
    ]
    build = subprocess.run(cmd, capture_output=True, text=True)
    if build.returncode != 0:
        pytest.fail(
            f"runtime self-check {source} failed to compile ({what}):\n"
            f"--- stderr ---\n{build.stderr}",
            pytrace=False,
        )
    if cross or request.config.getoption("--build-only"):
        return
    run = subprocess.run([str(binary)], capture_output=True, text=True)
    if run.returncode != 0:
        pytest.fail(
            f"runtime self-check {source} failed at runtime ({what}):\n"
            f"--- stdout ---\n{run.stdout}\n--- stderr ---\n{run.stderr}",
            pytrace=False,
        )
