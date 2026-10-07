"""THIR facts of records with struct bases: a field keyed by its declaring
record, a layout spanning the whole struct in construction order, a call to
an inherited method resolved to the declaring record's body with the actual
receiver's access, base initializers carrying the base's identity, and the
inherited-constructor fact. Records whose hierarchy MIR does not model
(native, generic or exception bases, a `@dynamic` hierarchy's calls) keep
no facts."""

import pytest

from ..compilation_context import activate_compiler
from ..typesys import NominalType, ReadonlyType
from . import nodes as th
from .dump import dump_codegen_thir
from .lower import iter_module_callables
from ..type_def_registry import type_def_of
from .scalar_leaves import modeled_field, modeled_hierarchy, plain_record_element, record_type
from .test_method_stubs import nodes
from .testutil import _compile, _entry

SOURCE = """\
from typing import Protocol
from tpy import int32, dynamic, readonly
from tpy.extern import native


class Base:
    n: int32
    name: str
    items: list[int32]

    def __init__(self, name: str) -> None:
        self.n = 0
        self.name = name
        self.items = []

    def bump(self) -> None:
        self.n += 1

    def push(self, v: int32) -> None:
        self.items.append(v)

    @readonly
    def total(self) -> int32:
        return self.n


class Sub(Base):
    k: int32

    def __init__(self, name: str, k: int32) -> None:
        super().__init__(name)
        self.k = k

    def bump_twice(self) -> None:
        self.n += 2
        self.k += 1

    def push_k(self) -> None:
        self.items.append(self.k)


class Deep(Sub):
    d: int32

    def __init__(self, name: str) -> None:
        super().__init__(name, 1)
        self.d = 3


class Shadow(Base):
    n: int32

    def __init__(self) -> None:
        super().__init__("s")
        self.n = 100

    def own_bump(self) -> None:
        self.n += 1

    @readonly
    def both(self) -> int32:
        return self.n + Base.n


class Deeper(Shadow):
    def __init__(self) -> None:
        super().__init__()

    @readonly
    def mid(self) -> int32:
        return Shadow.n + Base.n


class Animal:
    legs: int32

    def __init__(self, legs: int32) -> None:
        self.legs = legs


class Dog(Animal):
    pass


class Puppy(Dog):
    pass


class Mixin:
    tag: int32 = 0


class Mixed(Animal, Mixin):
    pass


class Tagged(Animal):
    extra: int32 = 0


class A:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class B:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


class AB(A, B):
    def __init__(self, a: int32, b: int32) -> None:
        A.__init__(self, a)
        B.__init__(self, b)


@dynamic
class Shape(Protocol):
    def grow(self) -> None: ...


class VBase(Shape):
    size: int32

    def __init__(self) -> None:
        self.size = 0

    def grow(self) -> None:
        self.size += 1


class VSub(VBase):
    def __init__(self) -> None:
        super().__init__()

    def grow(self) -> None:
        self.size += 2


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


class IntBox(Box[int32]):
    pass


class MyErr(Exception):
    code: int32

    def __init__(self, code: int32) -> None:
        super().__init__("e")
        self.code = code


class SubErr(MyErr):
    pass


@native("CppCounter")
class Counter:
    value: int32

    def __init__(self, value: int32) -> None: ...


class NSub(Counter):
    pass


def use_sub(s: Sub) -> int32:
    s.bump()
    s.push(3)
    s.bump_twice()
    s.push_k()
    return s.total() + s.k


@readonly
def read_sub(s: readonly[Sub]) -> int32:
    return s.total()


def use_shadow(sh: Shadow) -> int32:
    sh.bump()
    sh.own_bump()
    return sh.n


def use_deep(d: Deep) -> int32:
    d.bump()
    return d.n


def use_vsub(v: VSub) -> None:
    v.grow()


def excluded(b: IntBox, e: SubErr, c: NSub) -> int32:
    return b.v + e.code + c.value


def elements(xs: list[Sub]) -> int32:
    return len(xs)
"""


class Program:
    def __init__(self, source: str) -> None:
        self.compiler, modules = _compile(source)
        self.entry = _entry(modules)
        self.ctx = self.compiler.collect_thir(self.entry, tolerate_reject=True)
        self.registry = self.entry.analyzer.registry
        self.functions: dict[tuple[str | None, str], th.THIRFunction] = {}
        for func, self_type in iter_module_callables(self.entry.ast, self.entry.analyzer):
            fn = self.ctx.thir_functions.get(func)
            if fn is not None:
                owner = self_type.name if isinstance(self_type, NominalType) else None
                self.functions[(owner, func.name)] = fn
        self.ctors = {c.record_name: c for c in self.ctx.thir_constructors.values()}
        self.inherited = {rec.name: c for rec, c in self.ctx.thir_inherited_constructors.items()}

    def type(self, name: str) -> NominalType:
        info = self.registry.get_record(name)
        return NominalType(info.name, _module_qname=info.qualified_name())

    def field(self, owner: str, name: str, typ: object) -> th.THIRFieldIdentity:
        return th.THIRFieldIdentity(self.type(owner), name, typ)

    def info(self, name: str):
        return self.registry.get_record(name)


@pytest.fixture(scope="module")
def program() -> Program:
    return Program(SOURCE)


def _calls(fn: th.THIRFunction) -> dict[str, th.THIRMethodCall]:
    return {c.method_cpp: c for c in nodes(fn, th.THIRMethodCall)}


def _field_ids(fn: th.THIRFunction) -> set[tuple[str, str, str]]:
    return {(f.field_cpp, f.field_identity.owner.name, f.field_identity.name)
            for f in nodes(fn, th.THIRFieldAccess) if f.field_identity is not None}


def _int32(program: Program, record: str, name: str) -> object:
    return next(f.type for f in program.info(record).fields if f.name == name)


# --- registry helpers ----------------------------------------------------------


def test_declaring_record_walks_from_the_record_or_a_named_ancestor(program: Program) -> None:
    reg, info = program.registry, program.info
    owner = lambda found: None if found is None else found[0].name  # noqa: E731
    assert owner(reg.declaring_record(info("Deep"), "n")) == "Base"
    assert owner(reg.declaring_record(info("Deep"), "k")) == "Sub"
    assert owner(reg.declaring_record(info("Deep"), "d")) == "Deep"
    assert reg.declaring_record(info("Deep"), "missing") is None
    # A shadowed field: the nearest declaration, or the walk from the named ancestor.
    assert owner(reg.declaring_record(info("Shadow"), "n")) == "Shadow"
    assert owner(reg.declaring_record(info("Deeper"), "n")) == "Shadow"
    assert owner(reg.declaring_record(info("Deeper"), "n", start=info("Base"))) == "Base"
    assert owner(reg.declaring_record(info("Deeper"), "n", start=info("Shadow"))) == "Shadow"
    # `start` must be the record or one of its struct-base ancestors.
    assert reg.declaring_record(info("Sub"), "n", start=info("Animal")) is None
    assert reg.declaring_record(info("Base"), "n", start=info("Sub")) is None
    assert owner(reg.declaring_record(info("AB"), "b")) == "B"


def test_construction_order_differs_from_get_all_fields_for_two_bases(program: Program) -> None:
    reg, info = program.registry, program.info
    # C++ constructs A's subobject before B's; `get_all_fields` walks the
    # MRO root-first, which lists the last base's fields first.
    assert [(r.name, f.name) for r, f in reg.construction_order_fields(info("AB"))] == [("A", "a"), ("B", "b")]
    assert [f.name for f in reg.get_all_fields(info("AB"))] == ["b", "a"]
    assert [(r.name, f.name) for r, f in reg.construction_order_fields(info("Deep"))] == [
        ("Base", "n"), ("Base", "name"), ("Base", "items"), ("Sub", "k"), ("Deep", "d")]
    assert [r.name for r in reg.iter_field_ancestors(info("Deep"))] == ["Sub", "Base"]
    assert [r.name for r in reg.iter_field_ancestors(info("AB"))] == ["A", "B"]


def test_modeled_records_need_a_plain_hierarchy(program: Program) -> None:
    def modeled(typ: NominalType) -> bool:
        td = type_def_of(typ) if record_type(typ) else None
        return td is not None and modeled_hierarchy(td.record) is not None

    with activate_compiler(program.compiler):
        for name in ("Sub", "Deep", "Shadow", "Dog", "AB", "VSub"):
            assert modeled(program.type(name)), name
        for name in ("IntBox", "SubErr", "MyErr", "NSub"):
            assert not modeled(program.type(name)), name
        # A container element: inherited fields must be leaves too.
        assert plain_record_element(program.type("Dog"))
        assert not plain_record_element(program.type("Sub"))


def test_a_modeled_field_is_a_shape_not_a_definition(program: Program) -> None:
    base, box = program.info("Base"), program.info("Box")
    with activate_compiler(program.compiler):
        # A scalar leaf, an owned leaf, a native container, an inline record.
        for f in base.fields:
            assert modeled_field(f.type), f.name
        assert modeled_field(program.type("Sub")) and modeled_field(ReadonlyType(program.type("Sub")))
        # A record whose hierarchy MIR does not model still has a record's
        # shape: refusing its definition is MIRDefinitions' answer.
        for name in ("IntBox", "SubErr", "NSub"):
            assert modeled_field(program.type(name)), name
        # A native record and a type parameter are no modeled member.
        assert not modeled_field(program.type("Counter"))
        assert not modeled_field(next(f.type for f in box.fields if f.name == "v"))


# --- parameters, fields and calls ----------------------------------------------


def test_a_subclass_parameter_is_a_borrowed_record(program: Program) -> None:
    fn = program.functions[(None, "use_sub")]
    assert fn.params[0].borrowed_record == th.THIRBorrowedRecord(program.type("Sub"), False)
    fn = program.functions[(None, "read_sub")]
    assert fn.params[0].borrowed_record == th.THIRBorrowedRecord(program.type("Sub"), True)


def test_an_inherited_method_resolves_to_the_declaring_body(program: Program) -> None:
    calls = _calls(program.functions[(None, "use_sub")])
    sub, base = program.type("Sub"), program.type("Base")
    for name, owner, readonly in (("bump", base, False), ("push", base, False), ("total", base, False),
                                  ("bump_twice", sub, False), ("push_k", sub, False)):
        callee = calls[name].resolved_callee
        assert callee is not None, name
        assert callee.identity.owner == owner.qualified_name() and callee.identity.name == name
        # Parameter 0 is the declaring record; the access is the actual receiver's.
        assert callee.signature.param_types[0] == owner
        assert calls[name].receiver_access == th.THIRBorrowedRecord(sub, readonly)
    # The definition and every call publish one value.
    assert program.functions[("Base", "bump")].resolved_callee == calls["bump"].resolved_callee
    assert program.functions[("Sub", "bump_twice")].resolved_callee == calls["bump_twice"].resolved_callee
    total = _calls(program.functions[(None, "read_sub")])["total"]
    assert total.receiver_access == th.THIRBorrowedRecord(sub, True)


def test_a_grandchild_receiver_binds_the_grandparent_body(program: Program) -> None:
    call = _calls(program.functions[(None, "use_deep")])["bump"]
    assert call.resolved_callee.signature.param_types[0] == program.type("Base")
    assert call.receiver_access == th.THIRBorrowedRecord(program.type("Deep"), False)
    assert ("n", "Base", "n") in _field_ids(program.functions[(None, "use_deep")])


def test_subclass_bodies_key_fields_by_their_declaring_record(program: Program) -> None:
    sub = program.functions[("Sub", "bump_twice")]
    assert sub.receiver == th.THIRBorrowedRecord(program.type("Sub"), False)
    assert _field_ids(sub) == {("n", "Base", "n"), ("k", "Sub", "k")}
    assert _field_ids(program.functions[("Sub", "push_k")]) == {("items", "Base", "items"), ("k", "Sub", "k")}
    # The owner a subclass body publishes equals the base's own bodies' one.
    base_ids = {f.field_identity for f in nodes(program.functions[("Base", "bump")], th.THIRFieldAccess)}
    sub_ids = {f.field_identity for f in nodes(sub, th.THIRFieldAccess) if f.field_identity.name == "n"}
    assert base_ids == sub_ids


def test_a_shadowed_field_is_two_storages(program: Program) -> None:
    assert _field_ids(program.functions[("Shadow", "own_bump")]) == {("n", "Shadow", "n")}
    both = program.functions[("Shadow", "both")]
    assert _field_ids(both) == {("n", "Shadow", "n"), ("Base::n", "Base", "n")}
    explicit, = (f for f in nodes(both, th.THIRFieldAccess) if f.field_cpp == "Base::n")
    assert isinstance(explicit.receiver, th.THIRSelf) and explicit.receiver.result_type == program.type("Base")
    # Through an intermediate class: the walk starts at the named ancestor.
    assert _field_ids(program.functions[("Deeper", "mid")]) == {("Shadow::n", "Shadow", "n"),
                                                                 ("Base::n", "Base", "n")}
    calls = _calls(program.functions[(None, "use_shadow")])
    assert calls["bump"].resolved_callee.identity.owner == program.type("Base").qualified_name()
    assert calls["own_bump"].resolved_callee.identity.owner == program.type("Shadow").qualified_name()


def test_a_virtual_hierarchy_keeps_its_calls_unresolved(program: Program) -> None:
    call = _calls(program.functions[(None, "use_vsub")])["grow"]
    assert call.resolved_callee is None and call.receiver_access is None
    assert program.functions[("VSub", "grow")].resolved_callee is None
    assert program.functions[("VSub", "grow")].receiver == th.THIRBorrowedRecord(program.type("VSub"), False)


def test_unmodeled_hierarchies_keep_no_facts(program: Program) -> None:
    fn = program.functions[(None, "excluded")]
    assert all(p.borrowed_record is None for p in fn.params)
    assert not _field_ids(fn)
    assert program.functions[(None, "elements")].params[0].native_container is None
    assert all(name not in program.inherited for name in ("IntBox", "SubErr", "NSub"))


# --- constructors ----------------------------------------------------------------


def test_a_subclass_layout_spans_the_struct_in_construction_order(program: Program) -> None:
    t = program.type
    i32 = _int32(program, "Sub", "k")
    base = program.info("Base")
    name_t, items_t = (next(f.type for f in base.fields if f.name == n) for n in ("name", "items"))
    inherited = (th.THIRFieldIdentity(t("Base"), "n", i32), th.THIRFieldIdentity(t("Base"), "name", name_t),
                 th.THIRFieldIdentity(t("Base"), "items", items_t))
    sub = program.ctors["Sub"]
    assert sub.record_layout.fields == (*inherited, th.THIRFieldIdentity(t("Sub"), "k", i32))
    assert sub.record_layout.ancestors == (t("Base"),)
    base_init, = sub.base_inits
    assert base_init.base_cpp == "Base" and base_init.base == t("Base")
    assert [(type(a), a.name) for a in base_init.args] == [(th.THIRName, "name")]
    deep = program.ctors["Deep"].record_layout
    assert deep.ancestors == (t("Sub"), t("Base"))
    assert deep.fields == (*inherited, th.THIRFieldIdentity(t("Sub"), "k", i32),
                           th.THIRFieldIdentity(t("Deep"), "d", i32))
    ab = program.ctors["AB"]
    assert ab.record_layout.ancestors == (t("A"), t("B"))
    assert ab.record_layout.fields == (th.THIRFieldIdentity(t("A"), "a", i32), th.THIRFieldIdentity(t("B"), "b", i32))
    assert [b.base for b in ab.base_inits] == [t("A"), t("B")]


def test_a_shadowing_layout_holds_both_fields(program: Program) -> None:
    t = program.type
    i32 = _int32(program, "Shadow", "n")
    shadow = program.ctors["Shadow"]
    assert shadow.record_layout.fields[0] == th.THIRFieldIdentity(t("Base"), "n", i32)
    assert shadow.record_layout.fields[-1] == th.THIRFieldIdentity(t("Shadow"), "n", i32)
    # The own `self.n = 100` initializes Shadow::n.
    mil, = shadow.mil_inits
    assert mil.field_identity == th.THIRFieldIdentity(t("Shadow"), "n", i32)


def test_an_inherited_constructor_is_published_for_a_plain_subclass(program: Program) -> None:
    t = program.type
    dog = program.inherited["Dog"]
    assert dog.base == t("Animal")
    assert dog.record_layout.type == t("Dog") and dog.record_layout.ancestors == (t("Animal"),)
    assert dog.record_layout.fields == (th.THIRFieldIdentity(t("Animal"), "legs", _int32(program, "Animal", "legs")),)
    # Its own `__init__` fact: a record with no `__init__` of its own has no unique constructor.
    assert not dog.record_layout.unique_constructor
    puppy = program.inherited["Puppy"]
    assert puppy.base == t("Dog") and puppy.record_layout.ancestors == (t("Dog"), t("Animal"))
    # A second (default-constructed) base or an own field: nothing published.
    assert "Mixed" not in program.inherited and "Tagged" not in program.inherited


def test_the_dump_names_inherited_owners_and_base_identities(program: Program) -> None:
    with activate_compiler(program.compiler):
        text = dump_codegen_thir(program.entry.ast, program.entry.analyzer, program.ctx)
    assert "%self->n <Base::n> = binop(%self->n <Base::n>, +, lit(2))" in text
    assert "%self->Base::n" in text and "%self->Base::n <" not in text
    assert "  base Base(%name) <Base>" in text

