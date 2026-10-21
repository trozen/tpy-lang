"""Every lowered body carries the signature it lowers under: one built by
the callee-signature constructor before the body lowers, the very object
its `resolved_callee` publishes when it has one."""

from dataclasses import replace

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import INT32, Representation, VoidType
from . import nodes as th
from .dump import _callee_lines, _resumable_lines
from .testutil import _compile, _entry
from .validate import THIRValidationError, validate_function, validate_resumable_body

SOURCE = """\
import asyncio
from typing import Iterator, overload
from tpy import Own, auto_readonly, int32


class Q:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class P:
    v: int32
    q: Q
    oq: Q | None

    def __init__(self, v: int32) -> None:
        self.v = v
        self.q = Q(v)
        self.oq = None

    def __eq__(self, other: "P") -> bool:
        return other.v == self.v

    @staticmethod
    def make(v: int32) -> Own['P']:
        return P(v)

    @auto_readonly
    def inner(self) -> Q:
        return self.q

    @property
    def maybe(self) -> Q | None:
        return self.oq


@overload
def pick(a: int32) -> int32: ...
@overload
def pick(a: str) -> str: ...
def pick(a: int32 | str) -> int32 | str:
    if isinstance(a, int32):
        return a + 1
    return a


def count(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i


async def twice(x: int32) -> int32:
    await asyncio.sleep(0)
    return x * 2


def outer(x: int32) -> int32:
    def inner(y: int32) -> int32:
        return y + x
    return inner(1)


def main() -> None:
    p = P.make(1)
    print(p == P(1), pick(3), pick("s"), p.inner().n, p.maybe is None, outer(2))
    for i in count(2):
        print(i)
    print(asyncio.run(twice(4)))


main()
"""


@pytest.fixture(scope="module")
def ctx():
    compiler, modules = _compile(SOURCE)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return ctx


def _fns(ctx, name: str) -> list[th.THIRFunction]:
    found = [fn for fn in ctx.thir_functions.values() if fn.name == name]
    found += [fn for stubs in ctx.thir_overload_functions.values() for fn in stubs.values() if fn.name == name]
    return found


def _resumable(ctx, name: str) -> th.THIRResumableBody:
    return next(body for func, body in ctx.thir_resumables.items() if func.name == name)


def test_every_lowered_body_has_its_signature(ctx) -> None:
    bodies = [*ctx.thir_functions.values(), ctx.thir_top_level,
              *(fn for stubs in ctx.thir_overload_functions.values() for fn in stubs.values())]
    for fn in bodies:
        assert fn.signature is not None, fn.name
        if fn.resolved_callee is not None:
            assert fn.resolved_callee.signature is fn.signature, fn.name
        validate_function(fn, lowered=True)
    for name, body in ctx.thir_resumables.items():
        assert body.signature is not None, name.name


def test_bodies_no_call_binds_uniquely(ctx) -> None:
    # A dunder binds its receiver first; a staticmethod has none.
    (eq,) = _fns(ctx, "__eq__")
    assert eq.resolved_callee is None
    assert eq.signature.passings == (ParamPassing.CONST_REF, ParamPassing.CONST_REF)
    assert eq.signature.return_representation is Representation.STORAGE
    (make,) = _fns(ctx, "make")
    assert make.resolved_callee is None and make.signature.param_types == (INT32,)
    assert make.signature.return_representation is Representation.STORAGE
    # Each overload specialization lowers under its stub's types.
    assert {fn.signature.passings for fn in _fns(ctx, "pick")} == {
        (ParamPassing.VALUE,), (ParamPassing.VIEW,)}
    top = ctx.thir_top_level.signature
    assert top.param_types == () and top.return_type == VoidType() and top.passings == ()


def test_resumable_bodies(ctx) -> None:
    twice = _resumable(ctx, "twice").signature
    assert twice.param_types == (INT32,) and twice.passings == (ParamPassing.VALUE,)
    assert twice.return_representation is Representation.STORAGE
    assert _resumable(ctx, "count").signature.passings == (ParamPassing.VALUE,)
    assert _resumable_lines("twice", _resumable(ctx, "twice"))[1] == "  signature(int32: value) -> storage"


def test_getter_and_twin_results(ctx) -> None:
    # Both clones of an `@auto_readonly` pair publish the const clone's signature.
    clones = [fn for fn in _fns(ctx, "inner") if fn.receiver is not None]
    assert len(clones) == 2 and clones[0].signature == clones[1].signature
    assert clones[0].signature.result_follows_receiver
    assert clones[0].signature.return_representation is Representation.REFERENCE
    # A getter of a pointer-repr Optional hands back its field's storage by reference.
    getters = _fns(ctx, "maybe")
    assert getters and all(fn.signature.storage_reference_result for fn in getters)
    assert not any(fn.signature.storage_reference_result for fn in clones)
    assert _callee_lines(getters[0])[0].endswith("follows receiver, storage reference")


def test_dump_lists_a_signature_without_a_resolved_callee(ctx) -> None:
    (eq,) = _fns(ctx, "__eq__")
    assert _callee_lines(eq)[0] == "  signature(P: const_ref, readonly[P]: const_ref) -> storage"


def test_validator_holds_a_lowered_body_to_its_signature(ctx) -> None:
    (eq,) = _fns(ctx, "__eq__")
    (free,) = [fn for fn in _fns(ctx, "outer") if fn.resolved_callee is not None]
    signature = eq.signature
    for damaged, why in (
            (replace(eq, signature=None), "without a valid signature"),
            (replace(eq, signature=replace(signature, param_types=signature.param_types[:1],
                                           passings=signature.passings[:1])), "signature disagrees"),
            (replace(eq, signature=replace(signature, return_type=INT32,
                                           return_representation=Representation.STORAGE)),
             "signature disagrees"),
            (replace(eq, signature=replace(signature, passings=(ParamPassing.MUT_REF,) * 2)),
             "signature disagrees"),
            (replace(eq, signature=replace(signature, storage_reference_result=True)),
             "without a valid signature"),
            # An equal copy is not the object the callee publishes.
            (replace(free, signature=replace(free.signature)), "is not the body's signature")):
        with pytest.raises(THIRValidationError, match=why):
            validate_function(damaged, lowered=True)
    # A body built by hand need not carry one.
    validate_function(replace(eq, signature=None))
    twice = _resumable(ctx, "twice")
    with pytest.raises(THIRValidationError, match="without a valid signature"):
        validate_resumable_body("twice", replace(twice, signature=None), lowered=True)
