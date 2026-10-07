"""The loan classifier (`typesys.loan_class`), the parameter-passing fact
(`TpyType.param_passing`) and the primitive-operation contract
(`TypeDef.primitive_ops`): the per-type facts the MIR leaf vocabulary reads."""

from __future__ import annotations

import pytest

from tpyc.compilation_context import activate_compiler
from tpyc.type_def_registry import ParamPassing, TypeCategory, TypeDef, _type_defs, get_type_def, zero_value_of
from tpyc.typesys import (
    ALL_FIXED_INTS, BIGINT, BOOL, BYTEARRAY, BYTES, BYTESVIEW, CHAR, FLOAT, FLOAT32, INT32, INT64, NONE, STR,
    STRING, STRVIEW,
    FloatLiteralType, FunctionInfo, IntLiteralType, Loan, LoanClass, NominalType, OptionalType, OwnType,
    PtrType, ReadonlyType, RefType, Representation, TupleType, TypeParamRef, UnionType,
    certified_primitive_comparison, certified_primitive_op, is_inert_leaf, is_owned_leaf, loan_class,
    passing_representation, return_representation, unwrap_readonly, unwrap_ref_type,
    BYTES_FAMILY, STR_FAMILY, view_family_of, view_owned_leaf,
)
from tpyc.thir.scalar_leaves import (
    binds_cursor, binds_element, container_view, declared_members, modeled_members, native_container_type,
    owned_constant, owned_leaf, plain_record_element, storage_leaf, view_compatible, view_leaf,
)
from tpyc.thir.testutil import _compile, _entry

PRIMITIVES = (*ALL_FIXED_INTS, FLOAT, FLOAT32, BOOL, CHAR)
OWNED = (BIGINT, STR, STRING, BYTES)


# --- loan_class -------------------------------------------------------------

@pytest.mark.parametrize("typ", [*PRIMITIVES, NONE], ids=str)
def test_primitives_and_none_are_inert_in_storage(typ):
    assert loan_class(typ) == LoanClass(Loan.NO, Loan.NO)
    assert is_inert_leaf(typ)


def test_bigint_is_not_inert():
    # `int` is numeric but heap-backed: excluded by its TypeDef, not a name.
    assert not get_type_def("builtins.int").loan_inert
    assert not is_inert_leaf(BIGINT)


@pytest.mark.parametrize("typ", [STR, STRING, BYTES, BYTEARRAY], ids=str)
def test_buffer_types_are_not_inert(typ):
    assert not is_inert_leaf(typ)


def test_pointer_holds_a_borrow():
    assert loan_class(PtrType(INT32)).holds is Loan.YES


@pytest.mark.parametrize("typ", [
    TupleType((INT32, FLOAT)), OptionalType(INT32), UnionType((INT32, FLOAT)),
], ids=str)
def test_aggregates_of_inert_leaves_are_lendable_not_inert(typ):
    # A tuple / union / Optional parameter binds a reference to the whole,
    # so the aggregate lends even though no leaf holds a borrow.
    assert loan_class(typ) == LoanClass(Loan.NO, Loan.YES)


def test_aggregate_holds_what_a_member_holds():
    assert loan_class(TupleType((INT32, PtrType(INT32)))).holds is Loan.YES
    assert loan_class(TupleType((INT32, STR))).holds is Loan.NO
    assert loan_class(TupleType((INT32, BYTEARRAY))).holds is Loan.UNKNOWN


# --- owned leaves -------------------------------------------------------------

@pytest.mark.parametrize("typ", OWNED, ids=str)
def test_owned_leaves_hold_nothing_and_lend_their_storage(typ):
    assert loan_class(typ) == LoanClass(Loan.NO, Loan.YES)
    assert loan_class(OwnType(typ)) == LoanClass(Loan.NO, Loan.YES)
    assert is_owned_leaf(typ) and owned_leaf(typ)
    # Owned leaves are not inert: a borrow can point into their buffer.
    assert not is_inert_leaf(typ) and not storage_leaf(typ)
    for rep in (Representation.VIEW, Representation.REFERENCE):
        assert loan_class(typ, rep) == LoanClass(Loan.YES, Loan.UNKNOWN)
        assert not is_owned_leaf(typ, rep)
    assert loan_class(typ, Representation.TRAIT) == LoanClass(Loan.UNKNOWN, Loan.UNKNOWN)
    assert not is_owned_leaf(typ, Representation.TRAIT)


def test_views_buffers_and_records_are_not_owned_leaves():
    compiler, types = _record_types()
    with activate_compiler(compiler):
        for view in (STRVIEW, BYTESVIEW):
            assert loan_class(view) == LoanClass(Loan.YES, Loan.UNKNOWN)
            assert not is_owned_leaf(view) and not owned_leaf(view)
        # A mutable buffer is a reference type: no loan model yet.
        assert loan_class(BYTEARRAY) == LoanClass(Loan.UNKNOWN, Loan.UNKNOWN)
        assert not is_owned_leaf(BYTEARRAY)
        assert not any(is_owned_leaf(t) for t in (*types.values(), *PRIMITIVES, NONE))
    # The ownership wrapper is seen through; the leaf predicate names the bare type only.
    assert is_owned_leaf(OwnType(STR)) and not owned_leaf(OwnType(STR))


def test_owned_leaf_facts_are_declared_on_exactly_the_four():
    def declared(flag):
        return {q for q, td in _type_defs.items() if getattr(td, flag)}
    assert declared("owned_leaf") == {t.qualified_name() for t in OWNED}
    assert declared("compares_fixed_ints") == {BIGINT.qualified_name()}
    # BigInt allocation failure is a panic; the standard containers throw,
    # and the fact fails closed: a type that declares nothing may raise.
    assert {q for q in declared("owned_leaf") if not get_type_def(q).copy_may_raise} == {BIGINT.qualified_name()}
    assert TypeDef("x.undeclared", TypeCategory.RECORD).copy_may_raise is True
    for typ in OWNED:
        td = get_type_def(typ.qualified_name())
        assert td.primitive_ops and not td.loan_inert


@pytest.mark.parametrize("typ,value,other", [
    (BIGINT, 0, "0"), (STR, "abc", b"abc"), (STRING, "", 0), (BYTES, b"ab", "ab"),
], ids=str)
def test_owned_constants_take_the_zero_value_type(typ, value, other):
    assert type(zero_value_of(typ)) is type(value)
    assert owned_constant(typ, value) and not owned_constant(typ, other)
    assert not owned_constant(STRVIEW, "abc") and not owned_constant(INT32, 1)
    # A bool is no BigInt constant, though Python calls it an int.
    assert not owned_constant(BIGINT, True)


def test_wrappers_see_through_to_storage():
    assert is_inert_leaf(OwnType(INT32))
    assert is_inert_leaf(ReadonlyType(FLOAT))
    assert not is_inert_leaf(OwnType(STR))
    assert loan_class(RefType(INT32)).holds is Loan.YES


@pytest.mark.parametrize("rep", [Representation.VIEW, Representation.REFERENCE])
def test_a_borrowing_representation_holds_a_borrow(rep):
    assert loan_class(INT32, rep).holds is Loan.YES
    assert not is_inert_leaf(INT32, rep)


def test_trait_representation_and_type_param_are_unknown():
    assert loan_class(INT32, Representation.TRAIT) == LoanClass(Loan.UNKNOWN, Loan.UNKNOWN)
    assert loan_class(TypeParamRef("T")) == LoanClass(Loan.UNKNOWN, Loan.UNKNOWN)


def test_join_lets_unknown_absorb():
    assert Loan.NO.join(Loan.NO) is Loan.NO
    assert Loan.NO.join(Loan.YES) is Loan.YES
    assert Loan.YES.join(Loan.UNKNOWN) is Loan.UNKNOWN
    assert Loan.UNKNOWN.join(Loan.NO) is Loan.UNKNOWN


_RECORDS = """\
from typing import Callable, Optional, Protocol
from tpy import Own, Ptr, ValueType, StrView, int32

class Point:
    x: int32
    y: float
    def __init__(self, x: int32, y: float) -> None:
        self.x = x
        self.y = y

class Node:
    value: int32
    next: Optional[Node]
    def __init__(self, value: int32) -> None:
        self.value = value
        self.next = None

class Label(ValueType):
    text: StrView
    def __init__(self, text: StrView) -> None:
        self.text = text

class Shape(Protocol):
    def area(self) -> float: ...

def use(point: Point, node: Node, label: Label) -> None:
    print(point.x, node.value, label.text)

def shapes(shape: Shape, fn: Callable[[int32], int32], ptr: Ptr[Point], xs: list[int32],
           table: dict[str, Point], maybe: Optional[Point], either: Point | Node,
           owned: tuple[Own[Point], Own[Point]], mixed: tuple[Own[Point], Point]) -> None:
    pass

def main() -> None:
    p = Point(1, 2.0)
    n = Node(3)
    l = Label("a")
    use(p, n, l)

main()
"""


def _record_types():
    compiler, modules = _compile(_RECORDS)
    use = _entry(modules).analyzer.registry.get_function("use")[-1]
    return compiler, {p.name: unwrap_readonly(unwrap_ref_type(p.type)) for p in use.params}


_ENUM_AND_RECORD = """\
from enum import Enum
from tpy import int32

class Color(Enum):
    RED = 1

class Cell:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v

def use(color: Color, cell: Cell) -> None:
    pass
"""


def test_inert_leaf_shortcut_agrees_with_the_classifier():
    # `is_inert_leaf` skips the field walk; it must answer what `loan_class` does.
    compiler, modules = _compile(_ENUM_AND_RECORD)
    use = _entry(modules).analyzer.registry.get_function("use")[-1]
    color, cell = (unwrap_readonly(unwrap_ref_type(p.type)) for p in use.params)
    with activate_compiler(compiler):
        registered = [NominalType(qname.rsplit(".", 1)[-1], (), _module_qname=qname) for qname in sorted(_type_defs)]
        for typ in (*registered, color, cell):
            assert is_inert_leaf(typ) == loan_class(typ).inert, str(typ)
        assert is_inert_leaf(color) and not is_inert_leaf(cell)
    assert len(registered) >= 20


def test_records_are_never_inert_and_recursion_adds_no_loan():
    compiler, types = _record_types()
    with activate_compiler(compiler):
        # Scalar fields hold nothing, but a reference record lends its storage.
        assert loan_class(types["point"]) == LoanClass(Loan.NO, Loan.YES)
        # Re-entering Node through its own field adds nothing the frame that
        # entered it does not already join: Node's other fields decide.
        assert loan_class(types["node"]).holds is Loan.NO
        # A value record with a view field holds a borrow.
        assert loan_class(types["label"]).holds is Loan.YES
        assert not any(is_inert_leaf(t) for t in types.values())


# --- representation ---------------------------------------------------------

def test_passing_representation():
    assert passing_representation(ParamPassing.VALUE) is Representation.STORAGE
    assert passing_representation(ParamPassing.VIEW) is Representation.VIEW
    assert passing_representation(ParamPassing.TRAIT) is Representation.TRAIT
    for passing in (ParamPassing.CONST_REF, ParamPassing.MUT_REF, ParamPassing.OWN, ParamPassing.POINTER):
        assert passing_representation(passing) is Representation.REFERENCE


def test_return_representation():
    assert return_representation(INT32) is Representation.STORAGE
    assert return_representation(STR) is Representation.STORAGE
    assert return_representation(OwnType(INT32)) is Representation.STORAGE
    assert return_representation(RefType(INT32)) is Representation.REFERENCE
    assert return_representation(TypeParamRef("T")) is Representation.TRAIT
    assert return_representation(BYTEARRAY) is Representation.REFERENCE


def test_view_return_representation():
    # A declared borrowing view returns by value, but as a view over storage
    # the callee does not own -- not as storage of its own.
    compiler, _ = _record_types()
    with activate_compiler(compiler):
        assert return_representation(STRVIEW) is Representation.VIEW
        assert return_representation(BYTESVIEW) is Representation.VIEW
        assert return_representation(STRING) is Representation.STORAGE
        assert return_representation(BYTES) is Representation.STORAGE


# --- param_passing ----------------------------------------------------------

def _rendered_passing(typ, const: bool) -> ParamPassing:
    """Classify the SPELLED parameter form -- the independent oracle the
    declared fact must agree with."""
    decl = typ.to_cpp_const_param("x") if const else typ.to_cpp_param("x")
    form = decl.removesuffix(" x")
    if any(trait in form for trait in ("param_val_or_ref_t", "readonly_form_t", "opt_param_t", "opt_cparam_t",
                                            "own_param_t")):
        return ParamPassing.TRAIT
    if form.endswith("&&"):
        return ParamPassing.OWN
    if form.endswith("&"):
        return ParamPassing.CONST_REF if form.startswith("const ") else ParamPassing.MUT_REF
    if form.endswith("*") or form.endswith("*>"):
        return ParamPassing.POINTER
    return ParamPassing.VALUE if form == typ.to_cpp() else ParamPassing.VIEW


def _registry_types():
    for qname, td in sorted(_type_defs.items()):
        if td.cpp_formatter is None or td.param_kinds:
            continue
        yield NominalType(qname.rsplit(".", 1)[-1], (), _module_qname=qname)


@pytest.mark.parametrize("const", [False, True])
def test_param_passing_matches_every_registered_param_form(const):
    checked = 0
    for typ in _registry_types():
        try:
            expected = _rendered_passing(typ, const)
        except (TypeError, NotImplementedError, IndexError):
            continue
        assert typ.param_passing(const) is expected, str(typ)
        checked += 1
    assert checked >= 20


@pytest.mark.parametrize("typ", [
    OptionalType(INT32), OptionalType(STR), TupleType((INT32, BOOL)), TupleType((INT32, STR)),
    UnionType((INT32, FLOAT)), OwnType(INT32), OwnType(STR), ReadonlyType(INT32), ReadonlyType(STR),
    TypeParamRef("T"), RefType(TypeParamRef("T")), OwnType(TypeParamRef("T")),
], ids=str)
@pytest.mark.parametrize("const", [False, True])
def test_param_passing_matches_structural_param_forms(typ, const):
    assert typ.param_passing(const) is _rendered_passing(typ, const)


@pytest.mark.parametrize("const", [False, True])
def test_param_passing_matches_compiled_param_forms(const):
    # Pointers, Optional / union over records (the pointer representation),
    # structural protocols, callables, generic containers and tuples with an
    # owned element (fully owned or mixed: an ownership transfer either way).
    compiler, modules = _compile(_RECORDS)
    shapes = _entry(modules).analyzer.registry.get_function("shapes")[-1]
    types = [p.type for p in shapes.params]
    with activate_compiler(compiler):
        point = next(unwrap_readonly(unwrap_ref_type(t)) for t in types if isinstance(t, PtrType)).inner_pointee
        types += [PtrType(INT32), PtrType(point, is_readonly=True), OptionalType(point)]
        passings = set()
        for typ in types:
            assert typ.param_passing(const) is _rendered_passing(typ, const), str(typ)
            passings.add(typ.param_passing(const))
    assert ParamPassing.POINTER in passings
    assert ParamPassing.OWN in passings


@pytest.mark.parametrize("typ", PRIMITIVES, ids=str)
def test_primitive_params_pass_by_value(typ):
    assert typ.param_passing() is ParamPassing.VALUE
    assert typ.param_passing(True) is ParamPassing.VALUE


def test_passing_distinguishes_view_from_const_reference():
    # float passes `double` and int passes `const BigInt&`: neither is a view.
    assert STR.param_passing() is ParamPassing.VIEW
    assert BYTES.param_passing() is ParamPassing.VIEW
    assert BIGINT.param_passing() is ParamPassing.CONST_REF
    assert STRING.param_passing() is ParamPassing.CONST_REF
    assert BYTEARRAY.param_passing() is ParamPassing.MUT_REF
    assert BYTEARRAY.param_passing(True) is ParamPassing.CONST_REF
    # Own is not position-transparent: an owned str is the storage form.
    assert OwnType(STR).param_passing() is ParamPassing.VALUE


# --- primitive-operation contract -------------------------------------------

@pytest.mark.parametrize("typ", PRIMITIVES, ids=str)
def test_primitives_declare_the_contract_and_their_zero(typ):
    td = get_type_def(typ.qualified_name())
    assert td.loan_inert and td.primitive_ops
    assert zero_value_of(typ) == (False if typ == BOOL else "\0" if typ == CHAR else 0)
    assert type(zero_value_of(typ)) is type(td.zero_value)


def test_contract_is_declared_on_exactly_the_primitives_and_owned_leaves():
    inert = {q for q, td in _type_defs.items() if td.loan_inert}
    assert inert == {t.qualified_name() for t in PRIMITIVES}
    contract = {q for q, td in _type_defs.items() if td.primitive_ops}
    assert contract == {t.qualified_name() for t in (*PRIMITIVES, *OWNED)}


def _method(owner: str, ret) -> FunctionInfo:
    return FunctionInfo(name="__add__", params=[], return_type=ret, owning_type_qname=owner)


def test_certified_primitive_op():
    assert certified_primitive_op(_method("tpy.int32", INT32), (INT32, INT32), INT32)
    assert certified_primitive_op(_method("builtins.float", FLOAT), (FLOAT, INT64), FLOAT)
    # Owned leaves are read through a borrow; an owned result is built fresh.
    assert certified_primitive_op(_method("builtins.int", BIGINT), (BIGINT, BIGINT), BIGINT)
    assert certified_primitive_op(_method("builtins.str", STRING), (STR, STR), STRING)
    assert certified_primitive_op(_method("builtins.float", FLOAT), (FLOAT, BIGINT), FLOAT)
    assert certified_primitive_op(_method("tpy.int32", BIGINT), (INT32,), BIGINT)
    assert not certified_primitive_op(_method(None, INT32), (INT32, INT32), INT32)
    assert not certified_primitive_op(None, (INT32, INT32), INT32)
    # A view result and a reference-type operand are not leaves.
    assert not certified_primitive_op(_method("builtins.str", STRVIEW), (STR, INT32), STRVIEW)
    assert not certified_primitive_op(_method("builtins.bytes", BYTES), (BYTES, BYTEARRAY), BYTES)


def test_certified_primitive_comparison():
    for typ in (*PRIMITIVES, *OWNED):
        assert certified_primitive_comparison(typ, typ)
    assert not certified_primitive_comparison(INT32, INT64)
    # BigInt's runtime compares every fixed-width int, and an int literal.
    for fixed in ALL_FIXED_INTS:
        assert certified_primitive_comparison(BIGINT, fixed) and certified_primitive_comparison(fixed, BIGINT)
    assert certified_primitive_comparison(BIGINT, IntLiteralType(5))
    assert not certified_primitive_comparison(BIGINT, FLOAT)
    assert not certified_primitive_comparison(BIGINT, FloatLiteralType(1.5))
    # Only a declared fact admits a mixed pair; str compares no number.
    assert not certified_primitive_comparison(STR, INT32)
    assert not certified_primitive_comparison(STR, IntLiteralType(1))
    assert not certified_primitive_comparison(STR, STRING)


def test_views_resolve_to_their_family_and_read_through_it():
    # The borrowing-view fact is the compiled stub's, so the view rules read it
    # under the compilation that registered it.
    compiler, _ = _record_types()
    span = NominalType("Span", (INT32,), _module_qname="tpy.Span")
    with activate_compiler(compiler):
        # Every runtime member resolves to its family, the view included.
        for member in (STR, STRING, STRVIEW):
            assert view_family_of(member) is STR_FAMILY
        for member in (BYTES, BYTEARRAY, BYTESVIEW):
            assert view_family_of(member) is BYTES_FAMILY
        assert view_family_of(INT32) is None and view_family_of(span) is None
        # Only the view of an owned-leaf family reads a leaf through its borrow.
        assert view_owned_leaf(STRVIEW) == STR and view_owned_leaf(BYTESVIEW) == BYTES
        assert all(view_owned_leaf(t) is None for t in (STR, STRING, BYTEARRAY, span))
        assert view_leaf(STRVIEW) and view_leaf(BYTESVIEW) and not view_leaf(STR) and not view_leaf(span)
        # A view holder takes its family's owned leaves and views; bytearray is a reference type.
        assert all(view_compatible(STRVIEW, t) for t in (STR, STRING, STRVIEW))
        assert view_compatible(BYTESVIEW, BYTES) and not view_compatible(BYTESVIEW, BYTEARRAY)
        assert not view_compatible(STRVIEW, BYTES) and not view_compatible(STR, STR)
        # A view operand is read through the borrow it holds; a view result is still no leaf.
        assert certified_primitive_op(_method("builtins.str", STRING), (STRVIEW, STR), STRING)
        assert not certified_primitive_op(_method("builtins.str", STRVIEW), (STRVIEW, INT32), STRVIEW)
        assert certified_primitive_comparison(STRVIEW, STRVIEW)
        assert certified_primitive_comparison(STRVIEW, STR) and certified_primitive_comparison(BYTESVIEW, BYTES)
        assert not certified_primitive_comparison(STRVIEW, STRING)
        assert not certified_primitive_comparison(STRVIEW, BYTESVIEW)


# --- native containers --------------------------------------------------------

_CONTAINER_RECORDS = """\
from __future__ import annotations
from tpy import Own, StrView, Span, ValueType, int32, uint64, readonly

class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

class Named:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

class Ordered:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __lt__(self, other: Ordered) -> bool:
        return self.x < other.x

class Same:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __eq__(self, other: Same) -> bool:
        return self.x == other.x

class Hashed:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __hash__(self) -> uint64:
        return uint64(self.x)

class Copied:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __copy__(self) -> Own[Copied]:
        return Copied(self.x)

class Dropped:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def __del__(self) -> None:
        print("drop")

class Bag:
    items: list[int32]
    def __init__(self) -> None:
        self.items = []

class Pair(ValueType):
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def use(point: Point, named: Named, ordered: Ordered, same: Same, hashed: Hashed, copied: Copied,
        dropped: Dropped, bag: Bag, pair: Pair, span: Span[int32], rspan: Span[readonly[int32]],
        d: dict[str, int32]) -> None:
    print(point.x, named.name, ordered.x, same.x, hashed.x, copied.x, dropped.x, len(bag.items), pair.x,
          span[0], rspan[0], len(d.keys()), len(d.values()))
"""


def _nominal(qname: str, *args) -> NominalType:
    return NominalType(qname.rsplit(".", 1)[-1], tuple(args), _module_qname=qname)


# Per test: the borrowing-view facts are the compiled stubs', cleared between tests.
@pytest.fixture
def container_types():
    compiler, modules = _compile(_CONTAINER_RECORDS)
    use = _entry(modules).analyzer.registry.get_function("use")[-1]
    types = {p.name: unwrap_readonly(unwrap_ref_type(p.type)) for p in use.params}
    return compiler, types


def test_owns_elements_is_declared_by_exactly_the_native_container_stubs(container_types):
    declared = {q for q, td in _type_defs.items() if td.owns_elements}
    assert declared == {"builtins.list", "builtins.dict", "builtins.set", "tpy.Array"}


def test_no_type_owns_elements_before_its_stub_declares_it():
    # The fact is the stub's `@native(..., elements=True)`: the registry holds no list of containers.
    assert not any(td.owns_elements or td.native_members is not None for td in _type_defs.values())
    assert not native_container_type(_nominal("builtins.list", INT32))


@pytest.mark.parametrize("qname", ["builtins.list", "builtins.set", "tpy.Array"])
@pytest.mark.parametrize("const", [False, True])
def test_a_native_container_lends_by_reference(qname, const, container_types):
    # Lendable YES is the reference parameter form the renderer spells.
    args = (INT32, 3) if qname == "tpy.Array" else (INT32,)
    typ = _nominal(qname, *args)
    assert loan_class(typ) == LoanClass(Loan.NO, Loan.YES)
    assert typ.param_passing(const) is _rendered_passing(typ, const)
    assert typ.param_passing(const) is (ParamPassing.CONST_REF if const else ParamPassing.MUT_REF)
    assert native_container_type(typ) and not is_inert_leaf(typ) and not is_owned_leaf(typ)


def test_a_container_holds_what_its_elements_hold(container_types):
    compiler, types = container_types
    with activate_compiler(compiler):
        assert loan_class(_nominal("builtins.dict", STR, INT32)) == LoanClass(Loan.NO, Loan.YES)
        assert loan_class(_nominal("builtins.list", types["point"])) == LoanClass(Loan.NO, Loan.YES)
        assert loan_class(_nominal("builtins.list", STRVIEW)) == LoanClass(Loan.YES, Loan.YES)
        assert loan_class(_nominal("builtins.dict", STR, STRVIEW)).holds is Loan.YES
        assert loan_class(_nominal("builtins.list", PtrType(INT32))).holds is Loan.YES
        # A nested container answers through its own elements.
        assert loan_class(_nominal("builtins.list", _nominal("builtins.list", INT32))) == LoanClass(Loan.NO, Loan.YES)
        # Unproved elements and an unparameterized container prove nothing.
        assert loan_class(_nominal("builtins.list", TypeParamRef("T"))).holds is Loan.UNKNOWN
        assert loan_class(_nominal("builtins.list", BYTEARRAY)).holds is Loan.UNKNOWN
        assert loan_class(_nominal("builtins.list")) == LoanClass(Loan.UNKNOWN, Loan.UNKNOWN)
        # A record holds what its container field holds.
        assert loan_class(types["bag"]) == LoanClass(Loan.NO, Loan.YES)
        # Views keep the borrow class: they are no storage of their own.
        for view in (types["span"], types["rspan"]):
            assert loan_class(view) == LoanClass(Loan.YES, Loan.UNKNOWN)
            assert container_view(view) and not native_container_type(view)


def test_container_views_are_compatible_with_the_containers_they_view(container_types):
    compiler, types = container_types
    span, rspan = types["span"], types["rspan"]
    lst, arr = _nominal("builtins.list", INT32), _nominal("tpy.Array", INT32, 3)
    d = _nominal("builtins.dict", STR, INT32)
    with activate_compiler(compiler):
        keys = _nominal("builtins.dict_keys", STR, INT32)
        values = _nominal("builtins.dict_values", STR, INT32)
        assert container_view(keys) and container_view(values)
        assert not container_view(STRVIEW) and not container_view(lst)
        for holder in (span, rspan):
            assert view_compatible(holder, lst) and view_compatible(holder, arr) and view_compatible(holder, span)
            assert not view_compatible(holder, _nominal("builtins.list", INT64))
            assert not view_compatible(holder, d) and not view_compatible(holder, STR)
        # A mutable view never views readonly elements.
        assert view_compatible(rspan, rspan) and not view_compatible(span, rspan)
        assert view_compatible(keys, d) and view_compatible(values, d) and view_compatible(keys, keys)
        assert not view_compatible(keys, values) and not view_compatible(keys, _nominal("builtins.dict", STR, INT64))
        assert not view_compatible(keys, lst) and not view_compatible(STRVIEW, lst)
        # The leaf views keep their family rule.
        assert view_compatible(STRVIEW, STR) and not view_compatible(STRVIEW, span)


def test_plain_record_elements_run_no_user_code(container_types):
    compiler, types = container_types
    with activate_compiler(compiler):
        # Scalar and owned-leaf fields, no special member.
        assert plain_record_element(types["point"]) and plain_record_element(types["named"])
        # A comparison or hash dunder, custom copy or destructor, a container
        # field, a value record and a leaf are no plain element.
        for name in ("ordered", "same", "hashed", "copied", "dropped", "bag", "pair"):
            assert not plain_record_element(types[name]), name
        assert not plain_record_element(INT32) and not plain_record_element(STR)
        point = types["point"]
        assert modeled_members(_nominal("builtins.list", point))
        assert modeled_members(_nominal("tpy.Array", STR, 2))
        assert modeled_members(_nominal("builtins.dict", STR, INT32))
        # A type with a value member holds leaves only; a set element is any
        # admitted element (sema refuses one with no hash), nested containers are out.
        assert modeled_members(_nominal("builtins.set", point))
        assert not modeled_members(_nominal("builtins.set", types["hashed"]))
        assert not modeled_members(_nominal("builtins.dict", STR, point))
        assert not modeled_members(_nominal("builtins.list", _nominal("builtins.list", INT32)))
        assert not modeled_members(_nominal("builtins.list", types["ordered"]))
        assert not modeled_members(INT32) and not modeled_members(STR)


def test_declared_members_are_the_stub_type_parameters_at_the_type_arguments(container_types):
    compiler, types = container_types
    with activate_compiler(compiler):
        point = types["point"]
        assert declared_members(_nominal("builtins.list", point)) == (point, None, False)
        assert declared_members(_nominal("builtins.set", INT32)) == (INT32, None, False)
        assert declared_members(_nominal("tpy.Array", STR, 2)) == (STR, None, False)
        assert declared_members(types["rspan"]) == (ReadonlyType(INT32), None, False)
        assert declared_members(_nominal("tpy.varargs", INT32)) == (INT32, None, False)
        # A dict subscript is keyed by its element (the key) and yields the value.
        assert declared_members(_nominal("builtins.dict", STR, INT32)) == (STR, INT32, True)
        # Members are positions, not types: a dict of two equal arguments has two.
        assert declared_members(_nominal("builtins.dict", STR, STR)) == (STR, STR, True)
        # A view declares its own members: keys its key, values its value.
        assert declared_members(_nominal("builtins.dict_keys", STR, INT32)) == (STR, None, False)
        assert declared_members(_nominal("builtins.dict_values", STR, ReadonlyType(INT32))) \
            == (ReadonlyType(INT32), None, False)
        # An items view iterates both members at once: no cursor binds one.
        assert declared_members(_nominal("builtins.dict_items", STR, INT32)) == (STR, INT32, False)
        assert not binds_cursor(_nominal("builtins.dict_items", STR, INT32))
        for typ in (_nominal("builtins.dict", STR, INT32), _nominal("builtins.dict_values", STR, INT32),
                    _nominal("tpy.varargs", INT32), types["span"]):
            assert binds_cursor(typ), typ
        # An Array whose length is unbound names no storage.
        assert declared_members(_nominal("tpy.Array", INT32, TypeParamRef("N"))) is None
        # No declared element: a leaf view, a Span iterator, a leaf.
        for typ in (INT32, STRVIEW, _nominal("tpy.SpanIter", INT32)):
            assert declared_members(typ) is None and not binds_cursor(typ), typ


def test_a_loop_variable_binds_the_element_or_views_it(container_types):
    compiler, types = container_types
    with activate_compiler(compiler):
        assert binds_element(INT32, INT32) and binds_element(ReadonlyType(types["point"]), types["point"])
        assert binds_element(STRVIEW, STR) and binds_element(STR, STR)
        assert not binds_element(STRVIEW, INT32) and not binds_element(INT64, INT32)
