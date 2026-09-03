"""Every example under examples/ still reaches C++ code generation.

The corpus cases are written against the compiler; the examples are written
against the LANGUAGE, so they are the closest thing the suite has to code a
user would type. A construct that loses its only emitter turns them into
compile errors, and nothing else in the suite notices -- a snapshot case only
covers the shapes somebody already wrote a case for.

Front end only: no C++ toolchain, no build. An example that cannot be
generated today is xfailed by NAME with the reject tag it stops on, so the
gate stays honest and turns red the moment that tag starts lowering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import TEST_CODEGEN_OPTIONS

from tpyc import get_lib_dir
from tpyc.codegen_cpp.context import CodeGenError, ThirRejectError
from tpyc.compiler import Compiler
from tpyc.diagnostics import SemanticError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

# Examples blocked on a construct THIR cannot lower yet, by the reject tag
# each one stops on. Strict: an entry that starts compiling fails until it is
# deleted, so the list cannot outlive the gap it records.
KNOWN_BLOCKED = {
    "sieve.py": "stmt.for_each:iter.range_shape",
    "net/curl.py": "stmt.var_decl:decl.ptr_union_source",
}

# Examples the FRONT END refuses before codegen is reached. A sema rejection
# is not a lowering gap, so the tag machinery below does not describe it --
# it gets its own record, keyed on the message, and is verified to fail
# identically on the pre-cutover checkout so it cannot be mistaken for
# fallout from deleting the AST body emitters.
KNOWN_SEMA_FAILURES = {
    "net/tcp_server.py": (
        "Cannot call method 'getpeername' on type "
        "tuple[Own[socket], tuple[str, Int32]]"),
}

# Detection sanity: a glob that silently stopped matching would make this
# whole gate pass while compiling nothing. Well below the count today.
MIN_EXAMPLES = 5


def _example_paths() -> list[Path]:
    # Recursive: `examples/net/` holds the socket programs, and a glob that
    # stops at the top level would gate none of them.
    paths = sorted(p for p in EXAMPLES.rglob("*.py")
                   if "__tpyc__" not in p.parts)
    assert len(paths) >= MIN_EXAMPLES, (
        f"only {len(paths)} examples found under {EXAMPLES} -- the glob is "
        f"broken, not the examples directory")
    return paths


def _rel(path: Path) -> str:
    return path.relative_to(EXAMPLES).as_posix()


def _ids(paths: list[Path]) -> list[str]:
    return [_rel(p) for p in paths]


_PATHS = _example_paths()


def _emit(path: Path) -> None:
    compiler = Compiler(path, lib_dirs=[get_lib_dir() / "tpy"])
    modules = compiler.compile()
    entry = [m for m in modules if m.is_entry_point][0]
    compiler.generate_code_to_strings(entry, options=TEST_CODEGEN_OPTIONS)


@pytest.mark.parametrize("path", _PATHS, ids=_ids(_PATHS))
def test_example_generates_cpp(path: Path, request) -> None:
    name = _rel(path)
    blocked = KNOWN_BLOCKED.get(name) or KNOWN_SEMA_FAILURES.get(name)
    if blocked is not None:
        request.node.add_marker(
            pytest.mark.xfail(strict=True, reason=blocked))
    try:
        _emit(path)
    except CodeGenError as err:
        pytest.fail(f"{name} no longer generates C++: {err}")


@pytest.mark.parametrize("name,tag", sorted(KNOWN_BLOCKED.items()))
def test_blocked_example_stops_on_its_named_tag(name: str, tag: str) -> None:
    """The blocked list names a TAG, and this is what holds it to it.

    The strict xfail above only knows the example failed; a blocker that
    moved to an unrelated construct would keep xfailing under a label that
    no longer describes it. This test is not xfailed, so a drifted tag fails
    loudly and the list has to be re-read rather than trusted."""
    with pytest.raises(ThirRejectError) as caught:
        _emit(EXAMPLES / name)
    assert caught.value.reason == tag, (
        f"{name} now stops on {caught.value.reason!r}, not the recorded "
        f"{tag!r} -- re-derive the blocker before updating the entry")


@pytest.mark.parametrize("name,message", sorted(KNOWN_SEMA_FAILURES.items()))
def test_sema_blocked_example_stops_on_its_recorded_message(
        name: str, message: str) -> None:
    """The sema twin of the tag assert: a front-end rejection has no reject
    tag, so the message is what pins it. Not xfailed, so a drifted message
    -- or a rejection that turns into a lowering one -- fails loudly."""
    with pytest.raises(SemanticError) as caught:
        _emit(EXAMPLES / name)
    assert str(caught.value) == message, (
        f"{name} now fails with {str(caught.value)!r}, not the recorded "
        f"{message!r} -- re-derive the blocker before updating the entry")
