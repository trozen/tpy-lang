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
import sys
from pathlib import Path

import pytest

from conftest import CPP_CONFIG, PROJECT_ROOT, RUNTIME_DIR, exec_is_cross

_CPP_TESTS = PROJECT_ROOT / "runtime" / "cpp" / "tests"

# (source, what it pins) -- each is a standalone main() returning non-zero on
# failure, so the C++ file owns its own assertions.
_SELFCHECKS = [
    ("test_frame_slot_forms.cpp", "resumable-frame slot storage forms"),
    ("test_frame_state_pin.cpp",
     "a resumable frame is never copied and moves only before its first entry"),
    ("test_bigint_small_ops.cpp", "BigInt small-operation values and allocations"),
    ("test_math_exception_policy.cpp", "math exceptions and IEEE special values"),
    ("test_seq_contains_identity.cpp", "containment's identity-before-== rule"),
    ("test_dict_default_args.cpp", "dict get/pop/setdefault default deduction"),
    ("test_container_update_widths.cpp",
     "dict/set update converts a narrower source's entries"),
    ("test_yield_slot_forms.cpp",
     "the generic yield slot's spelling, const-ness and copy count"),
    ("test_next_step_result.cpp",
     "the iterator step result's size, the range-for adapter's stepping, next_or's step form, and an iterator owning a temporary container"),
    ("test_owning_combinator_move.cpp",
     "owning combinators stay movable until their first pull"),
    ("test_combinator_elem_form.cpp",
     "combinators hand an element on in the form its source steps it"),
    ("test_iter_range.cpp",
     "every iterable kind loops through iter_range in the form it came in"),
    ("test_copy_iter_sources.cpp",
     "copy_iter borrows an lvalue source, owns a temporary and copies each element"),
    ("test_interrupt_embedding.cpp",
     "a --no-main host's Ctrl-C reaches TPy code through the embedding API"),
    ("test_no_signals.cpp",
     "TPY_NO_SIGNALS compiles the Ctrl-C layer out of every check point"),
    ("test_print_join.cpp",
     "print(*xs) separators span every segment and each source is borrowed"),
    ("test_extreme_element.cpp",
     "element-returning min/max hand back a lending source's own element, a copy otherwise; a one-way lend verdict check; size and storage checks"),
    ("test_min_max_key_forms.cpp",
     "min/max with key return the operand: T& off non-const operands, const T& off a const one"),
]

# Runtime impls (runtime/cpp/src/) a self-check links against, and the libs
# they need.
_EXTRA_SOURCES = {
    "test_interrupt_embedding.cpp": ["stdlib/signal_impl.cpp"],
    "test_no_signals.cpp": ["stdlib/signal_impl.cpp"],
}
_EXTRA_LIBS = {
    "test_interrupt_embedding.cpp": ["-pthread"],
    "test_no_signals.cpp": ["-pthread"],
}
# Defines a self-check (and the runtime impls it links) is built with.
_EXTRA_DEFINES = {
    "test_no_signals.cpp": ["-DTPY_NO_SIGNALS"],
}
# Self-checks whose contract includes compiling clean: built under the strict
# warning set of generated code, with the unused-variable warning that set
# turns off (a host may not) back on.
_STRICT_WARNINGS = {"test_no_signals.cpp"}


# Self-checks also built the way a release build sees the headers: their
# debug-only guards compile out under NDEBUG, so the guarded paths must hold
# without them.
_RELEASE_SHAPE = {"test_owning_combinator_move.cpp", "test_frame_state_pin.cpp",
                  "test_copy_iter_sources.cpp"}
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
        *_EXTRA_DEFINES.get(source, []),
        *([*CPP_CONFIG.warn_flags, "-Wunused-variable"]
          if source in _STRICT_WARNINGS else []),
        "-I", str(RUNTIME_DIR),
        *(["-c"] if cross else []),
        str(src),
        *(str(PROJECT_ROOT / "runtime" / "cpp" / "src" / extra)
          for extra in ([] if cross else _EXTRA_SOURCES.get(source, []))),
        *([] if cross else _EXTRA_LIBS.get(source, [])),
        "-o", str(binary.with_suffix(".o") if cross else binary),
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


def test_no_signals_tu_rejects_a_runtime_with_the_layer(request, tmp_path):
    """A TU built with TPY_NO_SIGNALS must not link against signal_impl.cpp
    built without it: its DeferSignals scopes are empty, and that runtime
    could still be armed and raise KeyboardInterrupt through them."""
    if request.config.getoption("--no-exec") or exec_is_cross():
        pytest.skip("needs a host link")
    # Per-symbol sections, so the section GC below has the guard to itself.
    base = [*CPP_CONFIG.compiler, f"-std={CPP_CONFIG.std}", "-I", str(RUNTIME_DIR),
            "-ffunction-sections", "-fdata-sections"]
    objects = []
    for name, src, defines in [
        ("tu.o", _CPP_TESTS / "test_no_signals.cpp", ["-DTPY_NO_SIGNALS"]),
        ("rt.o", PROJECT_ROOT / "runtime" / "cpp" / "src" / "stdlib"
         / "signal_impl.cpp", []),
    ]:
        obj = tmp_path / name
        build = subprocess.run([*base, *defines, "-c", str(src), "-o", str(obj)],
                               capture_output=True, text=True)
        assert build.returncode == 0, build.stderr
        objects.append(str(obj))
    # A host's link commonly collects unreferenced sections; nothing refers to
    # the guard, so only its `retain` keeps it (ELF linkers).
    link_modes = [[]]
    if sys.platform == "linux":
        link_modes.append(["-Wl,--gc-sections"])
    for mode in link_modes:
        link = subprocess.run(
            [*CPP_CONFIG.compiler, *objects, "-pthread", *mode,
             "-o", str(tmp_path / "mixed")],
            capture_output=True, text=True)
        assert link.returncode != 0, f"a mixed-mode link must fail ({mode})"
        assert "tpy_signals_compiled_out" in link.stderr, link.stderr


def test_no_signals_leaves_the_embedding_api_undeclared(request, tmp_path):
    """A host that calls tpy::request_interrupt() in a build whose layer is
    compiled out gets a compile error, not a call that does nothing."""
    if request.config.getoption("--no-exec"):
        pytest.skip("--no-exec builds nothing")
    src = tmp_path / "host.cpp"
    src.write_text('#include "tpy/tpy.hpp"\n'
                   "void host_handler() { tpy::request_interrupt(); }\n")
    # A plain object compile, not -fsyntax-only: zig's driver fails that with
    # FileNotFound looking for the output it was never asked to write.
    cmd = [*CPP_CONFIG.compiler, f"-std={CPP_CONFIG.std}", "-I", str(RUNTIME_DIR),
           "-c", str(src), "-o", str(tmp_path / "host.o")]
    armed = subprocess.run(cmd, capture_output=True, text=True)
    assert armed.returncode == 0, armed.stderr
    compiled_out = subprocess.run([*cmd, "-DTPY_NO_SIGNALS"],
                                  capture_output=True, text=True)
    assert compiled_out.returncode != 0
    assert "request_interrupt" in compiled_out.stderr, compiled_out.stderr
