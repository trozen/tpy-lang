"""The diagnostic a body gets when THIR cannot lower it.

THIR is the only author of an emitted body, so a lowering reject IS a compile
error: the message, its location and the reject positions that produce it are
pinned here. Every fixture below is a shape that rejects TODAY --
`_strict_reject` fails loudly if one starts routing, so a widened lowering arm
retires its pin instead of leaving it asserting a diagnostic nothing produces.

One position has no pin: the per-stub specialization arm for `@overload` stubs
shares the body position, but no reachable program was found that rejects
there, so the diagnostic it would produce is unwitnessed.
"""

from __future__ import annotations

import os

import pytest

from .testutil import (_assert_rejects_at, _compile, _entry,
                       _strict_reject)
from ..codegen_cpp.context import CodeGenError, CodeGenOptions

# A guarded class pattern over a union subject: the plain-function fold.
BODY_SRC = '''from tpy import Int32


class Rec:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Other:
    m: Int32

    def __init__(self, m: Int32) -> None:
        self.m = m


def pick(v: Rec | Other) -> Int32:
    match v:
        case Rec(n=1) if v.n > 0:
            return 1
        case _:
            return 0


def main() -> None:
    print(pick(Rec(1)))


main()
'''

# A record-valued conditional in the member-init list: the constructor fold.
CTOR_SRC = '''from tpy import Int32


class A:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class H:
    a: A

    def __init__(self, c: bool, other: A) -> None:
        self.a = A(1) if c else other


def main() -> None:
    o = A(2)
    h = H(True, o)
    print(h.a.n)


main()
'''

# A module-scope union local needing a rebind slot: the top-level fold.
TOP_LEVEL_SRC = '''from tpy import Int32


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Q:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


for i in range(2):
    v: P | Q = P(i)
    if isinstance(v, P):
        print(v.x)
'''

# A walrus in a generator's yield value: the resumable-frame fold.
RESUMABLE_SRC = '''from typing import Iterator
from tpy import Int32


def make_pair(i: Int32) -> tuple[Int32, Int32]:
    return (i, i * 2)


def pairs() -> Iterator[Int32]:
    yield -1
    i = 1
    t = make_pair(0)
    while i < 3:
        yield (t := make_pair(i))[0]
        print(t[1])
        i += 1


def main() -> None:
    for a in pairs():
        print(a)


main()
'''

# An Optional yield slot in a lambda-peephole generator: the simple-generator
# fold, which is a separate lowering entry from the resumable frame above.
SIMPLE_GEN_SRC = '''from typing import Iterator
from tpy import Int32


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def opts(n: Int32) -> Iterator[P | None]:
    i = 0
    while i < n:
        yield P(i)
        i += 1


def main() -> None:
    for v in opts(2):
        if v is not None:
            print(v.x)


main()
'''

# A union-element tuple constant, in each of the two non-body positions.
FINAL_GLOBAL_SRC = '''from typing import Final
from tpy import Int32

A: Final[tuple[Int32, Int32 | float]] = (1, 2)


def main() -> None:
    print("hi")


main()
'''

# BODY_SRC's guarded class pattern as an IMPORTED module, so the rejecting
# body sits in a file the caller does not name.
IMPORTED_SRC = '''from tpy import Int32


class Rec:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Other:
    m: Int32

    def __init__(self, m: Int32) -> None:
        self.m = m


def pick(v: Rec | Other) -> Int32:
    match v:
        case Rec(n=1) if v.n > 0:
            return 1
        case _:
            return 0
'''

IMPORTER_SRC = '''import rejmod


def main() -> None:
    print(rejmod.pick(rejmod.Rec(1)))


main()
'''

CLASS_CONST_SRC = '''from typing import Final
from tpy import Int32


class C:
    A: Final[tuple[Int32, Int32 | float]] = (1, 2)


def main() -> None:
    print("hi")


main()
'''

# (fixture, message, line, fallback landmark) for the folds whose reject is
# located at the offending STATEMENT. The line is part of the claim -- a reject
# reported at the enclosing `def` instead of the statement is what this
# diagnostic exists to avoid, and only an exact line can fail on it. The
# top-level row doubles as the innermost-wins pin: its reject sits inside a
# `for` and must report the nested decl, not the loop header.
_STMT_PINS = [
    pytest.param(
        BODY_SRC,
        "in function 'pick': this construct is not yet supported by C++ code "
        "generation (stmt.match)",
        19, "body:stmt.match", id="body"),
    pytest.param(
        CTOR_SRC,
        "in the constructor of 'H': this construct is not yet supported by "
        "C++ code generation (expr.ifexpr)",
        15, "ctor:expr.ifexpr", id="ctor"),
    pytest.param(
        TOP_LEVEL_SRC,
        "at module level: this construct is not yet supported by C++ code "
        "generation (top_level.slot_alloc)",
        19, "top_level:top_level.slot_alloc", id="top_level"),
    pytest.param(
        FINAL_GLOBAL_SRC,
        "in the initializer of 'A': this construct is not yet supported by "
        "C++ code generation (const.tuple_slot)",
        4, "final_global:const.tuple_slot", id="final_global"),
    pytest.param(
        CLASS_CONST_SRC,
        "in the initializer of 'A': this construct is not yet supported by "
        "C++ code generation (const.tuple_slot)",
        6, "class_const:const.tuple_slot", id="class_const"),
]

# The two folds whose reject is decided before any statement lowers, so the
# location is the enclosing callable's own `def` line.
_FRAME_PINS = [
    pytest.param(
        RESUMABLE_SRC,
        "in function 'pairs': this construct is not yet supported by C++ code "
        "generation (expr.walrus)",
        9, "resumable:expr.walrus", id="resumable"),
    pytest.param(
        SIMPLE_GEN_SRC,
        "in function 'opts': this construct is not yet supported by C++ code "
        "generation (sgen.yield_type)",
        12, "body:sgen.yield_type", id="simple_generator"),
]



@pytest.mark.parametrize("source,message,line,landmark", _STMT_PINS)
def test_strict_reject_names_the_unit_the_reason_and_the_line(
        source: str, message: str, line: int, landmark: str) -> None:
    err, fallback = _strict_reject(source)
    assert err.message == message
    assert err.loc is not None and err.loc.line == line, err.format("main.py")
    _assert_rejects_at(fallback, landmark)


@pytest.mark.parametrize("source,message,line,landmark", _FRAME_PINS)
def test_strict_frame_reject_names_the_callable(
        source: str, message: str, line: int, landmark: str) -> None:
    """The two generator/async folds. The resumable's yield / return / await
    seams and the simple generator's yield-slot gate both decide outside the
    sync statement chokepoint, so the position is the callable's own `def`
    line -- coarser than the statement pins above, but still the enclosing
    unit rather than an unrelated line."""
    err, fallback = _strict_reject(source)
    assert err.message == message
    assert err.loc is not None and err.loc.line == line, err.format("main.py")
    _assert_rejects_at(fallback, landmark)


def test_strict_reject_in_an_import_names_that_module(tmp_path) -> None:
    """A reject inside an imported module is reported against THAT file.

    Codegen knows module names only, so without the stamp the diagnostic would
    be formatted with whatever file the caller is holding -- the entry point --
    and point at a line of a file that does not contain the construct."""
    helper = tmp_path / "rejmod.py"
    helper.write_text(IMPORTED_SRC)
    compiler, modules = _compile(IMPORTER_SRC, extra_lib_dirs=[tmp_path])
    target = [m for m in modules if m.name == "rejmod"][0]
    with pytest.raises(CodeGenError) as excinfo:
        compiler.generate_code_to_strings(
            target, options=CodeGenOptions())
    err = excinfo.value
    expected = os.path.relpath(helper)
    assert err.filename == expected
    assert err.loc is not None and err.loc.line == 19, err.format("main.py")
    assert err.format("main.py") == (
        f"{expected}:19: error: in function 'pick': this construct is not yet "
        "supported by C++ code generation (stmt.match)")


def test_strict_reject_in_the_entry_keeps_the_callers_name() -> None:
    """The entry point stays unstamped: the caller already names it, and the
    spelling of that name is the caller's (the test harness formats with a
    bare basename, the CLI with a repo-relative path)."""
    err, _fallback = _strict_reject(BODY_SRC)
    assert err.filename is None
    assert err.format("main.py").startswith("main.py:19: error:")


def test_internal_error_is_not_dressed_as_unsupported(monkeypatch) -> None:
    """Only a `ThirUnsupported` becomes the not-yet-supported diagnostic. A
    lowering bug must keep crashing loudly under strict, or the mode would
    turn every internal failure into a user-facing "not supported yet"."""
    from .lower import statements as thir_statements

    def boom(stmt, scope):
        raise RuntimeError("lowering bug")

    monkeypatch.setattr(thir_statements, "_lower_stmt_dispatch", boom)
    src = ("from tpy import Int32\n\n\n"
           "def add(a: Int32, b: Int32) -> Int32:\n"
           "    return a + b\n\n\n"
           "add(1, 2)\n")
    compiler, modules = _compile(src)
    with pytest.raises(RuntimeError, match="lowering bug"):
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions())
