"""An Optional-wrapped borrow-form tuple return or yield lends exactly what
its bare twin does: the parameter it borrows is mutated-through (a
returned element pointer grants write access) and recorded in
`return_borrows_from`, so the caller holds a loan on it.

The facts are pinned over the analyzer; `tests/cases/tuple/
optional_tuple_return` pins what they render.
"""

from .. import get_lib_dir
from ..compiler import Compiler

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

_SRC = """
from typing import Iterator
from tpy import Own, int32, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def mixed_opt(b: Box, ok: bool) -> tuple[Own[Box], Box] | None:
    if ok:
        return (Box(1), b)
    return None


def both(a: Box, b: Box) -> tuple[Box, Box]:
    return (a, b)


def both_opt(a: Box, b: Box, ok: bool) -> tuple[Box, Box] | None:
    if ok:
        return (a, b)
    return None


def ro_opt(a: Box, ok: bool) -> readonly[tuple[Box, int32]] | None:
    if ok:
        return (a, 1)
    return None


def gen(a: Box, b: Box) -> Iterator[tuple[Box, Box]]:
    yield (a, b)


def gen_opt(a: Box, b: Box) -> Iterator[tuple[Box, Box] | None]:
    yield (a, b)
    yield None
"""


def _functions():
    compiler = Compiler.from_source(_SRC, lib_dirs=_STDLIB_DIRS)
    entry = [m for m in compiler.compile() if m.is_entry_point][0]
    return {name: fis[0] for name, fis in entry.exports.functions.items()}


def _facts(fi):
    return (frozenset(fi.mutated_params or ()),
            frozenset(fi.root.return_borrows_from or ()))


def test_optional_tuple_return_lends_as_its_bare_twin():
    fns = _functions()
    assert _facts(fns["mixed"]) == (frozenset({0}), frozenset({0}))
    assert _facts(fns["mixed_opt"]) == _facts(fns["mixed"])
    assert _facts(fns["both"]) == (frozenset({0, 1}), frozenset({0, 1}))
    assert _facts(fns["both_opt"]) == _facts(fns["both"])


def test_readonly_optional_tuple_return_records_provenance_only():
    mutated, borrows = _facts(_functions()["ro_opt"])
    assert mutated == frozenset()
    assert borrows == frozenset({0})


def test_optional_tuple_yield_lends_as_its_bare_twin():
    fns = _functions()
    assert _facts(fns["gen_opt"])[0] == _facts(fns["gen"])[0] == frozenset({0, 1})
