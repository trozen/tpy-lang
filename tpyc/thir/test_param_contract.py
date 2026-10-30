"""The parameter contract (`param_contract`): one worked example per fact,
asked of the callees a compiled program resolves."""

import pytest

from ..type_def_registry import ParamPassing
from ..typesys import FunctionInfo
from .param_contract import (
    Adapts, Materializer, PointerKind, Retention, bound_passing, param_const, param_contract, published_passing,
    retention,
)
from .lower.callables import resolved_callee
from .nodes import THIRParam
from .testutil import _compile, _entry

SOURCE = """\
from typing import Iterator, Protocol
from tpy import int32, Own, Ptr, readonly, dynamic, take_ptr
from tpy.extern import native

class A:
    v: int32
    def __init__(self, v: int32):
        self.v = v

class B:
    v: int32
    def __init__(self, v: int32):
        self.v = v

class Shape(Protocol):
    def area(self) -> int32: ...

@dynamic
class Pet(Protocol):
    def name(self) -> int32: ...

class Cat(Pet):
    def name(self) -> int32:
        return 7

class Sq:
    side: int32
    def __init__(self, side: int32):
        self.side = side
    def area(self) -> int32:
        return self.side * self.side

def eat(a: Own[A]) -> int32:
    return a.v

def first(a: A, b: A) -> A:
    return a

def count(a: A) -> Iterator[int32]:
    yield a.v

def pick(u: readonly[A | B]) -> int32:
    return 1

def poke(p: Ptr[A]) -> int32:
    return 1

def measure(s: Shape) -> int32:
    return s.area()

def call(p: Pet) -> int32:
    return p.name()

@readonly
def maybe_read(b: A, flag: bool) -> int32:
    x: A | None = None
    if flag:
        x = b
    if x is not None:
        return x.v
    return 0

@native("probe_peek")
def probe_peek(a: readonly[A]) -> int32: ...

def main() -> None:
    xs: list[A] = []
    a = A(1)
    xs.append(a)
    print(eat(A(2)), first(a, a).v, sum(count(a)), pick(a), poke(take_ptr(a)), measure(Sq(3)), call(Cat()), probe_peek(a), maybe_read(a, True))

main()
"""


@pytest.fixture(scope="module")
def analyzer():
    _, modules = _compile(SOURCE)
    return _entry(modules).analyzer


@pytest.fixture
def registry(analyzer):
    return analyzer.registry


def _fn(registry, name: str) -> FunctionInfo:
    fi, = registry.get_function(name)
    return fi


def _list_append(registry) -> FunctionInfo:
    info = registry.find_record_by_qname("builtins.list")
    fi, = info.get_method_overloads("append")
    return fi


def test_body_own_parameter_is_materialized_by_the_caller(registry) -> None:
    # `def eat(a: Own[A])` renders `A&&`: no lvalue binds it, so the caller copies.
    fi = _fn(registry, "eat")
    c = param_contract(fi, 0, fi.params[0].type)
    assert c.materializes is Materializer.CALLER
    assert c.passing is ParamPassing.OWN
    assert c.retention is None and not c.frame_captured


def test_native_insert_is_materialized_by_the_callee(registry) -> None:
    # `xs.append(a)` stays `xs.push_back(a)`: the runtime overload copies the lvalue.
    fi = _list_append(registry)
    assert param_contract(fi, 0, fi.params[0].type).materializes is Materializer.CALLEE


def test_generator_factory_captures_its_arguments(registry) -> None:
    fi = _fn(registry, "count")
    assert param_contract(fi, 0, fi.params[0].type).frame_captured


def test_result_borrowing_a_parameter_is_retained(registry) -> None:
    # `first` returns a borrow of parameter 0; parameter 1 is only read.
    fi = _fn(registry, "first")
    assert 0 in fi.root.return_borrows_from
    assert param_contract(fi, 0, fi.params[0].type).retention is Retention.MEASURED
    assert param_contract(fi, 1, fi.params[1].type).retention is None


def test_binding_retention_reads_its_signature(registry) -> None:
    # A body-less insert stub may keep a copy of what it is handed
    # (`xs.append(a)` keeps it, in fact).
    fi = _list_append(registry)
    assert retention(fi, 0) & Retention.COPIES
    # A free stub returning no borrow keeps nothing of a pointer-free record.
    peek = _fn(registry, "probe_peek")
    assert retention(peek, 0) is None
    # No parameter index: unknown, so it is retained.
    assert retention(_fn(registry, "first"), -1) is Retention.MEASURED


def test_readonly_union_parameter_has_a_const_pointee(registry) -> None:
    # `pick(u: readonly[A | B])` takes `Union<const A*, const B*>`.
    fi = _fn(registry, "pick")
    c = param_contract(fi, 0, fi.params[0].type)
    assert c.pointer is PointerKind.BORROW and c.const_pointee and c.const


def test_ptr_parameter_is_a_by_value_pointer(registry) -> None:
    fi = _fn(registry, "poke")
    c = param_contract(fi, 0, fi.params[0].type)
    assert c.pointer is PointerKind.BY_VALUE and not c.const_pointee


def test_protocol_parameter_adapts(registry) -> None:
    # A structural protocol deduces the argument's type; a @dynamic one adapts it.
    measure = _fn(registry, "measure")
    assert param_contract(measure, 0, measure.params[0].type).adapts is Adapts.STRUCTURAL
    call = _fn(registry, "call")
    assert param_contract(call, 0, call.params[0].type).adapts is Adapts.DYNAMIC
    first = _fn(registry, "first")
    assert param_contract(first, 0, first.params[0].type).adapts is None


def test_const_verdict_and_stub_passing(registry) -> None:
    # `first` reads parameter 1 only: sema's verdict makes it a const borrow.
    first = _fn(registry, "first")
    assert param_contract(first, 1, first.params[1].type).const
    # A free stub publishes nothing of its own: the contract derives its
    # passing from the declared `readonly[A]`.
    peek = _fn(registry, "probe_peek")
    assert param_contract(peek, 0, peek.params[0].type).passing is ParamPassing.CONST_REF


def test_published_passing_wins(registry, analyzer) -> None:
    # `first`'s definition publishes `const A&` for the parameter it only reads.
    fi = _fn(registry, "first")
    sig = resolved_callee(fi, analyzer).signature
    assert published_passing(sig, 1) is ParamPassing.CONST_REF
    assert param_contract(fi, 1, fi.params[1].type, signature=sig).passing is sig.passings[1]
    assert published_passing(sig, 5) is None
    assert bound_passing(THIRParam("a", fi.params[0].type)) is None
    assert bound_passing(THIRParam("a", fi.params[0].type, passing=ParamPassing.MUT_REF)) is ParamPassing.MUT_REF


def test_published_const_ref_is_const(registry, analyzer) -> None:
    # An explicit `@readonly` free function renders `const A& b`; sema's
    # verdict leaves `b` out (it is bound to a local), so only the published
    # passing says const -- and the signature is what the callee renders.
    fi = _fn(registry, "maybe_read")
    sig = resolved_callee(fi, analyzer).signature
    assert sig.passings[0] is ParamPassing.CONST_REF
    assert not param_const(fi, 0)
    assert param_contract(fi, 0, fi.params[0].type, signature=sig).const
