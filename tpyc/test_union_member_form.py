"""The union member form query: whether a holder renders its union in the
borrow form (`union_borrow_form`) and whether it holds a member by pointer
(`UnionType.member_is_pointer`), pinned per holder and member. Each holder's
borrow verdict is the one its producer decides -- a binding's
`BindingRepr.union_borrow`, an expression's `Source.held` through
`_borrow_of_held`, an isinstance operand's lowered test. Every member of a
union holding a reference type is a pointer at a borrow holder (the
sibling rule), so a per-member answer changing is a visible change here."""

from types import SimpleNamespace

import pytest

from . import typesys
from .compilation_context import activate_compiler
from .thir import nodes as th
from .thir import testutil
from .thir.lower import bindings
from .thir.lower.expressions import _borrow_of_held
from .thir.nodes import iter_children
from .typesys import (
    INT32, NONE, STR, NominalType, OptionalType, OwnType, ReadonlyType,
    TupleType, TypeParamRef, UnionType, repr_shape, union_borrow_form,
)

PET = NominalType("Pet")
PET_INT = UnionType((PET, INT32))
PET_INT_NONE = UnionType((NONE, PET, INT32))
INT_STR = UnionType((INT32, STR))
T_INT = UnionType((TypeParamRef("T"), INT32))
WALK_SWIM = UnionType((NominalType("Walk", is_protocol=True),
                       NominalType("Swim", is_protocol=True)))
TREE = UnionType((INT32, NominalType("list", type_args=(PET,))))

_SRC = '''
from typing import Iterator, Optional
from tpy import Own, Ptr, int32, readonly


class Pet:
    def __init__(self, n: int32) -> None:
        self.n = n


type Tree = int | list[Tree]
type V2 = int32 | str | Ptr[readonly[V2]]


class Holder:
    u: Pet | int32
    o: Optional[Pet | int32]
    vu: int32 | str
    nu: Pet | int32 | None

    def __init__(self) -> None:
        self.u = Pet(1)
        self.o = None
        self.vu = 1
        self.nu = None

    @property
    def prop(self) -> Pet | int32:
        return self.u


def va(*ys: Pet | int32) -> int32:
    for y in ys:
        if isinstance(y, Pet):
            return 1
    return 0


def mk() -> Own[Pet | int32]:
    return Pet(3)


def get(h: Holder) -> Pet | int32:
    return h.u


def opn[T](u: T | int32) -> int32:
    return 1


def use(h: Holder, xs: list[Pet | int32], u: Pet | int32,
        r: readonly[Pet | int32], o: Optional[Pet | int32],
        own: Own[Pet | int32], t: Tree, vu: int32 | str) -> int32:
    loc = u
    for x in xs:
        print(isinstance(x, Pet))
    print(isinstance(h.u, Pet), isinstance(xs[0], Pet), isinstance(mk(), Pet))
    print(isinstance(h.prop, Pet), isinstance(get(h), Pet))
    print(h.vu, h.o is None)
    return 0


def more(h: Holder, n: Pet | int32 | None, v: V2) -> int32:
    tl: Tree = 1
    vl: V2 = 2
    print(isinstance(h.nu, Pet), isinstance(n, Pet), isinstance(tl, int))
    print(isinstance(vl, int32), isinstance(v, int32))
    return 0


def tm(p: tuple[Pet | int32, int32]) -> int32:
    match p[0]:
        case Pet():
            return 1
        case _:
            return 0


def gen(u: Pet | int32, xs: list[Pet | int32]) -> Iterator[int32]:
    for x in xs:
        if isinstance(x, Pet):
            yield 1
    v: Pet | int32 = Pet(5)
    yield 3
    if isinstance(u, Pet):
        yield 2
    if isinstance(v, Pet):
        yield 4
'''


@pytest.fixture(scope="module")
def lowered():
    """The program's lowered bodies, binding tables and compiler (a
    recursive alias's wrapper is known only under it)."""
    captured = []
    compile_ = testutil._compile

    def capture(*args, **kwargs):
        compiler, modules = compile_(*args, **kwargs)
        captured.append(compiler)
        return compiler, modules

    mp = pytest.MonkeyPatch()
    mp.setattr(testutil, "_compile", capture)
    try:
        bodies, tables = testutil.lower_bodies(_SRC)
    finally:
        mp.undo()
    return bodies, tables, captured[0]


def _union_of(typ) -> UnionType:
    """The union a holder of `typ` holds: through access / ownership
    wrappers, an Optional's payload and a varargs pack's element."""
    shape = repr_shape(typ)
    if isinstance(shape, OptionalType):
        shape = repr_shape(shape.inner)
    if isinstance(shape, NominalType) and shape.name == "varargs":
        shape = repr_shape(shape.type_args[0])
    assert isinstance(shape, UnionType), typ
    return shape


def _nodes(body) -> list:
    out: list = []

    def walk(n):
        out.append(n)
        for c in iter_children(n):
            walk(c)
    for st in body.body:
        walk(st)
    return out


def _pointers(u: UnionType, borrow: bool) -> tuple[bool, ...]:
    return tuple(u.member_is_pointer(borrow, m) for m in u.members)


# body, binding, and per member whether its binding holds it by pointer.
_BINDING_ROWS = [
    # param / local: `Union<const Pet*, const int32_t*>`.
    ("param", "use", "u", (True, True)),
    ("readonly param", "use", "r", (True, True)),
    ("local", "use", "loc", (True, True)),
    # frame PARAM field: the borrow form, as the parameter.
    ("frame param field", "gen", "u", (True, True)),
    # `Own[..]` param, loop variable, frame loop-variable field, frame
    # local, `*ys`: storage.
    ("own param", "use", "own", (False, False)),
    ("loop variable", "use", "x", (False, False)),
    ("frame loop variable field", "gen", "x", (False, False)),
    ("frame local", "gen", "v", (False, False)),
    ("varargs", "va", "ys", (False, False)),
    # `Optional[Pet | int32]` is an Optional (`const Union<Pet, int32_t>*`),
    # not a union with a None member: its payload is storage.
    ("optional param", "use", "o", (False, False)),
    # A value union has one form.
    ("value union param", "use", "vu", (False, False)),
    # A recursive-alias wrapper at a REFERENCE slot (`Tree&`): its `.value`
    # variant holds every member by value.
    ("wrapper param", "use", "t", (False, False)),
    # An open `T` member at a borrow holder: `storage_t<T>*`.
    ("open T param", "opn", "u", (True, True)),
    # A None member is `std::monostate` in the borrow form too.
    ("None-member param", "more", "n", (False, True, True)),
    # A wrapper local is the wrapper struct (`Tree tl`), and a wrapper whose
    # recursion runs through a `Ptr` (`V2&` param, `V2` local) is one too.
    ("wrapper local", "more", "tl", (False, False)),
    ("value-recursing wrapper param", "more", "v", (False, False, False)),
    ("value-recursing wrapper local", "more", "vl", (False, False, False)),
]


@pytest.mark.parametrize("holder, body, name, pointers", _BINDING_ROWS,
                         ids=[r[0] for r in _BINDING_ROWS])
def test_member_form_per_binding(lowered, holder, body, name, pointers):
    _bodies, tables, compiler = lowered
    rec = next(r for r in tables[body].records() if r.name == name)
    with activate_compiler(compiler):
        u = _union_of(rec.type)
        # Only a type with a borrow form has a binding holding it.
        assert not rec.union_borrow or union_borrow_form(rec.type) is not None
        assert _pointers(u, rec.union_borrow) == pointers
        assert u.member_pointers(rec.union_borrow) == (
            () if u.needs_wrapper() else pointers)


def test_global_member_form():
    # A union global is a pointer slot to its storage union (`extern
    # ::tpy::Union<Pet, int32_t>* g;`). Its read's `Source.held` says
    # BORROWED although the read renders that storage
    # (BUGS.md#union-global-read-held-borrowed), so the row reads the
    # module-init binding the global is declared by.
    tables = _module_tables(_GLOBAL_SRC)
    rec = next(r for r in tables["__tpy_init"].records() if r.name == "g")
    assert not rec.union_borrow
    assert _pointers(_union_of(rec.type), rec.union_borrow) == (False, False)


def test_tuple_element_member_form(lowered):
    # A borrow-form tuple parameter holds its union element in the borrow
    # form (`tuple<Union<const Pet*, const int32_t*>, int32_t>`): a match on
    # the element extracts every member through its pointer.
    bodies, _tables, _compiler = lowered
    (match,) = [n for n in _nodes(bodies["tm"]) if isinstance(n, th.THIRMatch)]
    assert match.member_pointers == (True, True)


_GLOBAL_SRC = '''
from tpy import int32


class Pet:
    def __init__(self, n: int32) -> None:
        self.n = n


g: Pet | int32 = Pet(7)


def take(u: Pet | int32) -> int32:
    return 0


print(take(g))
'''


def _module_tables(source: str) -> dict:
    """Every body's binding table as the module's C++ generation plans it,
    the module-init body (`__tpy_init`) included."""
    compiler, modules = testutil._compile(source)
    tables: dict = {}
    install = bindings.install

    def capture(lc, table) -> None:
        tables.setdefault(lc.func.name, table)
        install(lc, table)
    mp = pytest.MonkeyPatch()
    mp.setattr(bindings, "install", capture)
    try:
        with activate_compiler(compiler):
            compiler.generate_inl_and_code_to_strings(
                testutil._entry(modules))
    finally:
        mp.undo()
    return tables


def _read(nodes, kind, typ_text, pick=0):
    hits = [n for n in nodes if type(n).__name__ == kind
            and str(getattr(n, "result_type", None)) == typ_text]
    return hits[pick]


# node kind, its result type, and per member whether the read holds it by
# pointer: a member or element read names its slot's storage, an `Own`
# result is fresh storage, a property getter hands back the field's storage
# by reference.
_EXPR_ROWS = [
    ("field", "use", "THIRFieldAccess", "Pet | int32", (False, False)),
    ("element", "use", "THIRSubscript", "Pet | int32", (False, False)),
    ("own return", "use", "THIRCall", "Pet | int32", (False, False)),
    ("property getter storage ref", "use", "THIRMethodCall", "Pet | int32",
     (False, False)),
    # A value union field and an `Optional[Pet | int32]` field: storage.
    ("value union field", "use", "THIRFieldAccess", "int32 | str",
     (False, False)),
    ("optional field", "use", "THIRFieldAccess", "Pet | int32 | None",
     (False, False)),
    # A `Pet | int32 | None` field: the storage union, monostate first.
    ("None-member field", "more", "THIRFieldAccess", "None | Pet | int32",
     (False, False, False)),
]


@pytest.mark.parametrize("holder, body, kind, typ_text, pointers", _EXPR_ROWS,
                         ids=[r[0] for r in _EXPR_ROWS])
def test_member_form_per_read(lowered, holder, body, kind, typ_text,
                              pointers):
    bodies, _tables, compiler = lowered
    node = _read(_nodes(bodies[body]), kind, typ_text)
    with activate_compiler(compiler):
        u = _union_of(node.result_type)
        assert _pointers(u, _borrow_of_held(node.source.held)) == pointers


def test_isinstance_operand_member_form(lowered):
    # An isinstance operand with no binding: a borrow-returning call is
    # tested in the borrow form, a field, element or property read in its
    # storage, an `Own` result as the storage it is.
    bodies, _tables, _compiler = lowered
    tests = [n for n in _nodes(bodies["use"])
             if isinstance(n, th.THIRIsinstance) and n.union_test is None
             and not isinstance(n.subject, th.THIRName)]
    operands = {type(t.subject).__name__ + (
        f".{t.subject.callee}" if isinstance(t.subject, th.THIRCall) else ""):
        t.member_cpps for t in tests}
    pointer = {k: tuple(c.endswith("*") for c in v) for k, v in operands.items()}
    assert pointer == {
        "THIRFieldAccess": (False,),
        "THIRSubscript": (False,),
        "THIRCall.mk": (False,),
        "THIRMethodCall": (False,),
        "THIRCall.get": (True,),
    }


def test_member_form_by_type():
    # A storage-form tuple's union element (`Own[tuple[Pet | int32, ..]]`)
    # keeps a type-level row: no read of it lowers
    # (BUGS.md#own-union-leaf-tuple-param-read-rejects).
    assert _pointers(PET_INT, False) == (False, False)
    # A None member is `std::monostate` in both forms.
    assert _pointers(PET_INT_NONE, True) == (False, True, True)
    assert _pointers(PET_INT_NONE, False) == (False, False, False)
    # A value union holds no member by pointer even when asked for the
    # borrow form directly.
    assert _pointers(INT_STR, True) == (False, False)
    assert _pointers(INT_STR, False) == (False, False)


def test_borrow_form_of_each_union_shape():
    assert union_borrow_form(PET_INT) == PET_INT
    assert union_borrow_form(ReadonlyType(PET_INT)) == PET_INT
    assert union_borrow_form(PET_INT_NONE) == PET_INT_NONE
    assert union_borrow_form(T_INT) == T_INT
    # An owned union is the storage form; a value union has one form.
    assert union_borrow_form(OwnType(PET_INT)) is None
    assert union_borrow_form(INT_STR) is None
    # `Optional[Pet | int32]` is an Optional, not a union with a None member.
    assert union_borrow_form(OptionalType(PET_INT)) is None
    assert union_borrow_form(TupleType((PET_INT, INT32))) is None
    # A structural protocol union is answered as any reference union is;
    # its readers (one template parameter) stay outside the query.
    assert union_borrow_form(WALK_SWIM) == WALK_SWIM


def test_recursive_alias_wrapper_holds_no_member_by_pointer(monkeypatch):
    fake = SimpleNamespace(union_wrapper_index={TREE.members: object()},
                           union_alias_names={}, union_display_names={})
    monkeypatch.setattr(typesys, "get_current_compiler", lambda: fake)
    assert TREE.needs_wrapper()
    assert union_borrow_form(TREE) is None
    assert _pointers(TREE, True) == (False, False)
    assert _pointers(TREE, False) == (False, False)


def test_borrow_renders_follow_the_member_answers():
    assert PET_INT.to_cpp_ptr_variant() == "::tpy::Union<Pet*, int32_t*>"
    assert PET_INT_NONE.to_cpp_const_ptr_variant() == (
        "::tpy::Union<std::monostate, const Pet*, const int32_t*>")
