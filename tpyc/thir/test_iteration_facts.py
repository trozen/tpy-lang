"""Loop bindings and field identities agree with the shared emitted binding decision."""

import pytest

from ..codegen_cpp.forms import LoopBinding
from ..compilation_context import activate_compiler
from ..typesys import INT32, NominalType, TupleType
from .lower.storage import field_identity
from .nodes import THIRFieldAccess, THIRFieldIdentity, THIRForEach, THIRForRange, THIRVarDecl
from .test_method_stubs import nodes
from .testutil import _compile, _entry


@pytest.fixture(scope="module")
def functions():
    source = '''from tpy import int32, readonly, Array
class Cell:
    value: int32
    def __init__(self, value: int32):
        self.value = value
def mutable(xs: list[Cell]):
    for x in xs:
        x.value = 7
def const(xs: readonly[Array[Cell, 3]]) -> int32:
    result = 0
    for x in xs:
        result = x.value
    return result
def scalar(xs: set[int32]) -> int32:
    last = 5
    for last in xs:
        pass
    return last
def range_hoist(n: int32) -> int32:
    for i in range(n):
        result = i
    else:
        result = 7
    return result
'''
    compiler, modules = _compile(source)
    _, ctx = compiler.generate_code_and_thir(_entry(modules))
    return {fn.name: fn for fn in ctx.thir_functions.values()}


def test_loop_binding_capabilities(functions) -> None:
    for name, binding in (("mutable", LoopBinding.REFERENCE), ("const", LoopBinding.CONST_REFERENCE),
                          ("scalar", LoopBinding.ASSIGN)):
        loop = next(st for st in functions[name].body if isinstance(st, THIRForEach))
        assert loop.binding.loop_binding is binding
    loop = next(st for st in functions["range_hoist"].body if isinstance(st, THIRForRange))
    assert tuple(b.name for b in loop.hoisted_bindings) == tuple(d.name for d in loop.hoist_decls)
    assert "result" in {b.name for b in loop.hoisted_bindings}


FIELDS_SOURCE = '''from tpy import int32, Span
class Base:
    pair: tuple[int32, int32]
    def __init__(self):
        self.pair = (1, 2)
class Holder(Base):
    sp: Span[int32]
    xs: list[int32]
    o: list[int32] | None
    def __init__(self, sp: Span[int32], xs: list[int32]):
        super().__init__()
        self.sp = sp
        self.xs = xs
        self.o = None
    def walk_span(self) -> int32:
        total = 0
        for x in self.sp:
            total += x
        return total
    def walk_list(self) -> int32:
        total = 0
        for x in self.xs:
            total += x
        return total
    def alias_list(self) -> int32:
        v = self.xs
        return len(v)
    def alias_optional(self) -> int32:
        if self.o is not None:
            v = self.o
            return len(v)
        return 0
'''


@pytest.fixture(scope="module")
def fields():
    compiler, modules = _compile(FIELDS_SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    return compiler, entry, {fn.name: fn for fn in ctx.thir_functions.values()}


def test_every_declared_member_has_a_field_identity(fields) -> None:
    # Members MIR does not model (a view, a tuple) are identified too;
    # whether MIR models one is `modeled_field`'s question.
    compiler, entry, _ = fields
    registry = entry.analyzer.registry
    base, holder = (registry.get_record(n) for n in ("Base", "Holder"))
    base_type = NominalType(base.name, _module_qname=base.qualified_name())
    holder_type = NominalType(holder.name, _module_qname=holder.qualified_name())
    span = NominalType("Span", (INT32,), _module_qname="tpy.Span")
    with activate_compiler(compiler):
        sp = field_identity(holder_type, "sp", entry.analyzer)
        assert sp == THIRFieldIdentity(holder_type, "sp", span)
        # Read through the subclass, keyed by the record that declares it.
        pair = field_identity(holder_type, "pair", entry.analyzer)
        assert pair == THIRFieldIdentity(base_type, "pair", TupleType((INT32, INT32)))


def test_a_view_field_and_an_optional_field_read_have_identities(fields) -> None:
    _, _, functions = fields
    span_access = next(n for n in nodes(functions["walk_span"], THIRFieldAccess) if n.field_cpp == "sp")
    assert span_access.field_identity is not None
    optional_alias = next(d for d in nodes(functions["alias_optional"], THIRVarDecl) if d.name == "v")
    assert optional_alias.init.field_identity is not None
