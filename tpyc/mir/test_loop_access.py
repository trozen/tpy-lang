"""A binding's access comes from every fact its declaration gives it
(`lower.access_of`): a loop variable sema made const over a container the
body writes elsewhere, one over a readonly source, and a parameter of a
`@readonly` function, whose type is `readonly[R]` though its borrowed-record
fact is mutable."""

import pytest

from ..thir.testutil import _compile, _entry
from .lower import lower_function
from .nodes import MIRBodyId, MIRFunction, MIRSlotKind

SOURCE = """\
from tpy import int32, readonly


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def get(self) -> int32:
        return self.x


def const_loop(ps: list[P]) -> int32:
    t: int32 = 0
    for p in ps:
        t += p.x
    ps[0] = P(9)
    return t


def written_loop(ps: list[P]) -> int32:
    t: int32 = 0
    for p in ps:
        p.x = 1
        t += p.x
    return t


class Ps:
    items: list[P]

    def __init__(self) -> None:
        self.items = [P(1)]

    def sum_got(self) -> int32:
        # `self` is inferred readonly; `c` is bound by reference, not made const.
        t = 0
        for c in self.items:
            t += c.get()
        return t


@readonly
def readonly_param(a: P, b: P, flag: bool) -> int32:
    x = a
    if flag:
        x = b
    return x.x
"""


@pytest.fixture(scope="module")
def bodies() -> dict[str, object]:
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        yield {fn.name: lower_function(fn, MIRBodyId("access", fn.name), definitions=mir.definitions,
                                       summaries=mir.workspace.summaries)
               for fn in ctx.thir_functions.values()}


def _slot(fn: MIRFunction, name: str):
    return next(s for s in fn.slots if s.name == name and s.kind is not MIRSlotKind.TEMPORARY)


def test_a_const_loop_variable_over_a_written_container_binds_readonly(bodies) -> None:
    # Sema made `p` const (it is only read); the container is written after the loop.
    fn = bodies["const_loop"]
    assert isinstance(fn, MIRFunction), fn
    assert _slot(fn, "p").readonly and not _slot(fn, "ps").readonly


def test_a_loop_variable_over_a_readonly_source_binds_readonly(bodies) -> None:
    fn = bodies["sum_got"]
    assert isinstance(fn, MIRFunction), fn
    assert _slot(fn, "c").readonly and _slot(fn, "self").readonly


def test_a_written_loop_variable_binds_mutable(bodies) -> None:
    fn = bodies["written_loop"]
    assert isinstance(fn, MIRFunction), fn
    assert not _slot(fn, "p").readonly


def test_a_readonly_parameter_binds_readonly(bodies) -> None:
    fn = bodies["readonly_param"]
    assert isinstance(fn, MIRFunction), fn
    assert _slot(fn, "a").readonly and _slot(fn, "b").readonly
