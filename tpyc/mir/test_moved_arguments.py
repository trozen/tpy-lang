"""A named record moved into an `Own[...]` parameter is admitted at every
call, a constructor's included: the callee's stores into it are private to
the callee only while no holder of the caller's reaches the moved storage
after the call. MIR's own MOVE storage event of the name makes such a
holder a retention conflict -- a loop-carried alias, one relayed through a
call's borrowed result, either argument order -- whatever sema's move rule
decided (`BUGS.md#own-move-loop-carried-alias`)."""

from dataclasses import replace

import pytest

from ..thir.testutil import _compile, _entry
from .collect import enumerate_bodies
from .nodes import MIRFunction, statement_call
from .prepare import validate_function
from .validate import MIRDefiniteAssignmentError

LOOP_CARRIED = """\
from tpy import Own, StrView, int32


class Tok:
    s: StrView
    n: int32
    def __init__(self, s: StrView, n: int32) -> None:
        self.s = s
        self.n = n


class Clobber:
    n: int32
    def __init__(self, t: Own[Tok], s: StrView) -> None:
        self.n = 0
        t.s = s


def id_tok(t: Tok) -> Tok:
    return t


def loop_carried_uaf(good: StrView, k: int32) -> int:
    other = Tok(good, 0)
    t = Tok(good, 1)
    h = other
    i: int32 = 0
    while i < 2:
        if i == 1:
            local_str = "z" * k
            c = Clobber(t, local_str)
            break
        h = t
        i += 1
    return len(h.s)


def loop_carried_call_uaf(good: StrView, k: int32) -> int:
    other = Tok(good, 0)
    t = Tok(good, 1)
    h = id_tok(other)
    i: int32 = 0
    while i < 2:
        if i == 1:
            local_str = "z" * k
            c = Clobber(t, local_str)
            break
        h = id_tok(t)
        i += 1
    return len(h.s)
"""

BOTH_ORDERS = """\
from tpy import Own, StrView, int32


class Tok:
    s: StrView
    n: int32
    def __init__(self, s: StrView, n: int32) -> None:
        self.s = s
        self.n = n


def both1(t: Own[Tok], h: Tok) -> int32:
    return h.n


def both2(h: Tok, t: Own[Tok]) -> int32:
    return h.n


def caller1(good: StrView) -> int32:
    other = Tok(good, 0)
    t = Tok(good, 1)
    h = other
    i: int32 = 0
    while i < 2:
        if i == 1:
            return both1(t, h)
        h = t
        i += 1
    return 0


def caller2(good: StrView) -> int32:
    other = Tok(good, 0)
    t = Tok(good, 1)
    h = other
    i: int32 = 0
    while i < 2:
        if i == 1:
            return both2(h, t)
        h = t
        i += 1
    return 0
"""

LOOP_BORROW = """\
from tpy import Own, StrView, int32

class Tok:
    s: StrView
    n: int32
    def __init__(self, s: str, n: int32) -> None:
        self.s = s
        self.n = n

def borrow(t: Tok) -> Tok:
    return t

def touch(t: Own[Tok], s: str) -> None:
    t.s = s
    t.n = 9

class Touch:
    n: int32
    def __init__(self, t: Own[Tok], s: str) -> None:
        self.n = 0
        t.s = s
        t.n = 9

def straight() -> None:
    t = Tok("old", 1)
    h = borrow(t)
    touch(t, "new")
    print("straight", h.s, h.n)

def loop() -> None:
    t = Tok("old", 1)
    other = Tok("other", 2)
    h = borrow(other)
    for i in range(2):
        if i == 1:
            touch(t, "new")
            print("loop", h.s, h.n)
            return
        h = borrow(t)

def ctor_loop() -> None:
    t = Tok("old", 1)
    other = Tok("other", 2)
    h = borrow(other)
    for i in range(2):
        if i == 1:
            c = Touch(t, "new")
            print("ctor_loop", h.s, h.n, c.n)
            return
        h = borrow(t)
"""

RELAY = """\
from tpy import Own, StrView, int32

class Tok:
    s: StrView
    n: int32
    def __init__(self, s: str, n: int32) -> None:
        self.s = s
        self.n = n

def borrow(t: Tok) -> Tok:
    return later(t)

def later(t: Tok) -> Tok:
    return t

def touch(t: Own[Tok], s: str) -> None:
    t.s = s
    t.n = 9

class Touch:
    n: int32
    def __init__(self, t: Own[Tok], s: str) -> None:
        self.n = 0
        t.s = s
        t.n = 9

def straight() -> None:
    t = Tok("old", 1)
    h = borrow(t)
    touch(t, "new")
    print("straight", h.s, h.n)

def loop() -> None:
    t = Tok("old", 1)
    other = Tok("other", 2)
    h = borrow(other)
    for i in range(2):
        if i == 1:
            touch(t, "new")
            print("loop", h.s, h.n)
            return
        h = borrow(t)

def ctor_loop() -> None:
    t = Tok("old", 1)
    other = Tok("other", 2)
    h = borrow(other)
    for i in range(2):
        if i == 1:
            c = Touch(t, "new")
            print("ctor_loop", h.s, h.n, c.n)
            return
        h = borrow(t)
"""


CASES = [
    (LOOP_CARRIED, "loop_carried_uaf"),
    (LOOP_CARRIED, "loop_carried_call_uaf"),
    (BOTH_ORDERS, "caller1"),
    (BOTH_ORDERS, "caller2"),
    (LOOP_BORROW, "loop"),
    (LOOP_BORROW, "ctor_loop"),
    (RELAY, "straight"),
    (RELAY, "loop"),
    (RELAY, "ctor_loop"),
]


def _verdicts(source: str) -> dict[str, object]:
    compiler, modules = _compile(source)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdicts = enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                    compiler.thir_reject_by_node, mir.workspace)
        return {v.body.declaration.split("@")[0]: v for v in verdicts}


_CACHE: dict[str, dict[str, object]] = {}


@pytest.mark.parametrize("source,name", CASES)
def test_a_holder_of_moved_storage_live_after_the_call_conflicts(source: str, name: str) -> None:
    if source not in _CACHE:
        _CACHE[source] = _verdicts(source)
    verdict = _CACHE[source][name]
    assert verdict.function is not None, verdict.reason
    # The callee replaces the moved object's view member; the caller's holder still reads it.
    assert "replacement" in verdict.conflicts, verdict.conflicts


HANDED = """\
from tpy import Own, int32


class Cell:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n


def take(c: Own[Cell]) -> int32:
    return c.n


def give(k: int32) -> int32:
    c = Cell(k)
    return take(c)
"""


def test_a_call_handed_an_argument_s_storage_leaves_it_uninitialized() -> None:
    compiler, modules = _compile(HANDED)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        verdict = next(v for v in enumerate_bodies(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                                   compiler.thir_reject_by_node, mir.workspace)
                       if v.body.declaration.startswith("give@"))
        _handed_twice(verdict.function)


def _handed_twice(fn: MIRFunction | None) -> None:
    assert fn is not None
    validate_function(fn)
    call, = (s for b in fn.blocks for s in b.statements
             if (c := statement_call(s)) is not None and c.summary.callee.identity.name == "take")
    assert statement_call(call).summary.parameters[0].storage
    # The callee owns what the argument held: handing it over again reads storage the first call took.
    twice = replace(fn, blocks=tuple(replace(b, statements=tuple(
        n for s in b.statements for n in ((s, s) if s is call else (s,)))) for b in fn.blocks))
    with pytest.raises(MIRDefiniteAssignmentError, match="read after move"):
        validate_function(twice)
