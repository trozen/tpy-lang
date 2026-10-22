"""A binding's access comes from every fact its declaration gives it
(`value.loan_access`): a loop variable sema made const over a container the
body writes elsewhere, one over a readonly source, and a parameter of a
`@readonly` function, whose type is `readonly[R]` though its borrowed-record
fact is mutable. A local alias's THIR fact is checked against that access,
never read: a forged one is refused."""

from dataclasses import replace

import pytest

from ..compilation_context import activate_compiler
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import ReadonlyType
from .lower import lower_function
from .nodes import MIRBodyId, MIRFunction, MIRNotCovered, MIRSlotKind

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


class H:
    p: P

    def __init__(self, p: P) -> None:
        self.p = p


def container_alias(xs: list[int32]) -> int32:
    ys = xs
    return len(ys)


def record_alias(p: P) -> int32:
    q = p
    return q.x


def field_borrow(h: H) -> int32:
    q = h.p
    q.x = 1
    return q.x
"""


@pytest.fixture(scope="module")
def compiled():
    return _compile(SOURCE)


@pytest.fixture(scope="module")
def program(compiled):
    compiler, modules = compiled
    with activate_compiler(compiler):
        entry = _entry(modules)
        _, ctx = compiler.generate_code_and_thir(entry)
        with compiler.mir_analysis(((entry, ctx),)) as mir:
            def lower(fn: th.THIRFunction) -> MIRFunction | MIRNotCovered:
                return lower_function(fn, MIRBodyId("access", fn.name), definitions=mir.definitions,
                                      summaries=mir.workspace.summaries)
            result = {fn.name: fn for fn in ctx.thir_functions.values()}, lower
    return result


@pytest.fixture(scope="module")
def bodies(program, compiled) -> dict[str, object]:
    with activate_compiler(compiled[0]):
        functions, lower = program
        return {name: lower(fn) for name, fn in functions.items()}


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


def _forged(program, name: str, **changes) -> MIRFunction | MIRNotCovered:
    """`name`'s body with its one declaration rebuilt: `changes` replace the
    declaration's own fields, a callable value is given the old one."""
    functions, lower = program
    fn = functions[name]
    decl = next(s for s in fn.body if isinstance(s, th.THIRVarDecl))
    decl = replace(decl, **{k: v(getattr(decl, k)) if callable(v) else v for k, v in changes.items()})
    return lower(replace(fn, body=tuple(decl if isinstance(s, th.THIRVarDecl) else s for s in fn.body)))


def _refused(result: MIRFunction | MIRNotCovered, reason: str) -> bool:
    return isinstance(result, MIRNotCovered) and result.reason == reason


def _flip(fact):
    return replace(fact, readonly=not fact.readonly)


def test_a_forged_container_alias_fact_is_not_read(program) -> None:
    # `ys = xs` binds const: its access is the declaration's, whatever a fact says.
    plain = _forged(program, "container_alias")
    assert isinstance(plain, MIRFunction)
    assert _forged(program, "container_alias", native_container=_flip) == plain
    # A `readonly[...]` declaration reads only although it is not bound
    # const, a mutable fact or none.
    declared = _forged(program, "container_alias", resolved_type=ReadonlyType, is_const=False)
    assert isinstance(declared, MIRFunction), declared
    mutable = _forged(program, "container_alias", resolved_type=ReadonlyType, is_const=False,
                      native_container=lambda f: replace(f, readonly=False))
    assert mutable == declared


def test_a_forged_record_alias_fact_is_not_read(program) -> None:
    plain = _forged(program, "record_alias")
    assert isinstance(plain, MIRFunction)
    reference = lambda ab: replace(ab, reference=_flip(ab.reference))  # noqa: E731
    assert _forged(program, "record_alias", alias_binding=reference) == plain
    # Declared `readonly[P]` and not bound const, the holder reads only,
    # whatever access a fact states.
    declared = _forged(program, "record_alias", resolved_type=ReadonlyType, is_const=False)
    assert isinstance(declared, MIRFunction)
    mutable = lambda ab: replace(ab, reference=replace(ab.reference, readonly=False))  # noqa: E731
    assert _forged(program, "record_alias", resolved_type=ReadonlyType, is_const=False,
                   alias_binding=mutable) == declared


def test_a_forged_storage_borrow_fact_is_not_read(program) -> None:
    # `q = h.p` written through binds mutable, whatever access a fact states.
    plain = _forged(program, "field_borrow")
    assert isinstance(plain, MIRFunction)
    assert _forged(program, "field_borrow", storage_borrow=_flip) == plain
    # Declared `readonly[P]`, it is never written through: the write is refused.
    declared = _forged(program, "field_borrow", resolved_type=ReadonlyType)
    assert isinstance(declared, MIRNotCovered) and declared.reason == "readonly field store", declared
