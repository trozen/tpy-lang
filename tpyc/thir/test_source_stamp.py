"""Invariants of the read facts a lowered expression carries (`Source`):
the node's own pointer / id-expression answers stay equal to the stamped
ones whenever an arm rebuilds the node, a NAME's pointer-ness comes from its
binding, and a const method's receiver is const. Hand-built nodes; no arm's
render is pinned here."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from dataclasses import replace
from types import SimpleNamespace
from typing import Callable

import pytest

from ..compilation_context import activate_compiler
from ..identity_map import IdentityMap
from ..typesys import INT32, NominalType, TupleType
from . import nodes as th
from .lower import iter_module_callables, lower_function
from .lower.bindings import BindingTable, _held_of, project_held
from .lower.context import Slot, SlotConstruct, SlotHolds, _Prescan
from .lower.convert import Refuse, convert
from .lower.expressions import (_HELD_BY_KIND, _held_from_facts,
                                element_source, stamped)
from .nodes import (
    Form, THIRExpr, THIRFieldAccess, THIRLiteral, THIRName, THIRSelf,
    THIRSubscript,
)
from .source import (UNBOUND, BindingForm, BindingRepr, Held, Source,
                     TraitAbi)
from .testutil import _compile, _entry, lower_bodies
from .validate import _iter_children

_REC = NominalType("P")


def _binding(name: str, *, pointer: bool) -> BindingRepr:
    return BindingRepr(name=name, site=None, type=_REC,
                       form=(BindingForm.POINTER if pointer
                             else BindingForm.NONE),
                       site_kind="decl", pointer=pointer)


def _stamped(node):
    """The node with a stamp whose read facts are deliberately wrong: the
    node must rewrite them to its own."""
    return replace(node, source=Source(
        held=Held.BORROWED, pointer_held=not node.reads_raw_pointer,
        id_read=not node.reads_binding, binding=(
            node.source.binding if node.source is not None else None)))


def _tuple_elem(*, elem_pointer: bool) -> THIRSubscript:
    recv = THIRName(result_type=TupleType((_REC, INT32)), name="t",
                    form=Form.STORAGE)
    return THIRSubscript(result_type=_REC, receiver=recv,
                         index=THIRLiteral(result_type=INT32, value=0),
                         tuple_index=0, form=Form.BORROW,
                         elem_pointer=elem_pointer)


_NODES = {
    "pointer_name": replace(
        THIRName(result_type=_REC, name="p", form=Form.BORROW),
        source=Source(held=Held.BORROWED,
                      binding=_binding("p", pointer=True))),
    "plain_name": replace(
        THIRName(result_type=_REC, name="q", form=Form.BORROW),
        source=Source(held=Held.BORROWED,
                      binding=_binding("q", pointer=False))),
    "self": THIRSelf(result_type=_REC, form=Form.BORROW, pointer=True),
    "frame_self": THIRSelf(result_type=_REC, form=Form.BORROW,
                           cpp="__self", pointer=False),
    "pointer_elem": _tuple_elem(elem_pointer=True),
    "by_value_elem": _tuple_elem(elem_pointer=False),
}


@pytest.mark.parametrize("name", sorted(_NODES))
@pytest.mark.parametrize("deref", [False, True])
def test_rebuilt_node_rewrites_its_read_facts(name: str, deref: bool) -> None:
    node = replace(_stamped(_NODES[name]), deref=deref)
    assert node.source.pointer_held == node.reads_raw_pointer
    assert node.source.id_read == node.reads_binding


def test_deref_is_never_a_raw_pointer() -> None:
    for node in _NODES.values():
        assert not replace(node, deref=True).reads_raw_pointer


def test_by_value_tuple_element_is_no_pointer() -> None:
    # A record element of a tuple holding it by value yields `T&`.
    assert not _tuple_elem(elem_pointer=False).reads_raw_pointer
    assert _tuple_elem(elem_pointer=True).reads_raw_pointer


def test_name_indirection_follows_its_binding() -> None:
    assert _NODES["pointer_name"].indirect
    assert _NODES["pointer_name"].reads_raw_pointer
    assert not _NODES["plain_name"].indirect
    other = replace(_NODES["pointer_name"], name="r")
    assert not other.indirect


def test_frame_receiver_is_no_pointer() -> None:
    assert _NODES["self"].reads_raw_pointer
    assert not _NODES["frame_self"].reads_raw_pointer


@pytest.mark.parametrize("held,temporary", [
    (Held.FRESH, False), (Held.VALUE, True), (Held.STORAGE, True),
    (Held.STORAGE, None), (Held.BORROWED, None)])
def test_reference_slot_refuses_a_dying_source(held: Held,
                                               temporary: bool) -> None:
    # Sema lets some temporaries reach a reference result; the conversion
    # must not bind one, nor a source whose lifetime nothing states.
    node = replace(THIRName(result_type=_REC, name="x"),
                   source=Source(held=held, temporary=temporary))
    slot = Slot(_REC, SlotConstruct.RETURN, holds=SlotHolds.BORROWS)
    assert convert(node, slot, None) == Refuse("borrow_source")
    lvalue = replace(node, source=Source(held=Held.BORROWED))
    assert not isinstance(convert(lvalue, slot, None), Refuse)


def _lc(*, readonly: bool):
    # The receiver's record: const for a read-only method (a nested def
    # reads the enclosing method's record through its scope chain).
    receiver = BindingRepr(name="self", site=None, type=_REC,
                           form=BindingForm.NONE, site_kind="receiver",
                           const=readonly)
    return SimpleNamespace(
        record_name="P", analyzer=None, self_receiver="self",
        binding=lambda name: receiver)


def test_readonly_receiver_is_const() -> None:
    # A bare receiver an arm builds beside `_lower_expr` is stamped by the
    # same stamp, and a member read off it inherits its const-ness.
    recv = THIRSelf(result_type=INT32, form=Form.BORROW, pointer=True)
    field = THIRFieldAccess(result_type=INT32, receiver=recv, field_cpp="v",
                            is_arrow=True)
    assert stamped(recv, _lc(readonly=True)).source.const
    assert stamped(field, _lc(readonly=True)).source.const
    assert not stamped(recv, _lc(readonly=False)).source.const


def test_rebuilt_name_repicks_its_held() -> None:
    # A value-optional name read whole is its storage and through its
    # narrowed unwrap a borrow; stripping the deref must re-pick it.
    node = replace(THIRName(result_type=_REC, name="o", deref=True),
                   source=Source(held=Held.BORROWED,
                                 held_by_deref=(Held.STORAGE, Held.BORROWED)))
    assert node.source.held is Held.BORROWED
    whole = replace(node, deref=False)
    assert whole.source.held is Held.STORAGE
    assert replace(whole, deref=True).source.held is Held.BORROWED


_LITERALS = '''\
from tpy import BytesView


def size(b: BytesView) -> int:
    return len(b)


def lits(raw: bytes) -> int:
    v: BytesView = b"xy"
    owned = b"zz"
    s = "ab"
    n = size(b"abc") + len(v) + len(owned) + len(s)
    if raw == b"q":
        n += 1
    print(b"p", "q")
    return n
'''


def test_every_literal_is_held_as_it_renders() -> None:
    # A str / bytes literal's stamped `held` follows its render choice: the
    # view over its static storage is a borrow, the owning buffer fresh, the
    # char array a plain value.
    compiler, modules = _compile(_LITERALS)
    entry = _entry(modules)
    func = next(f for f, _st in iter_module_callables(entry.ast,
                                                      entry.analyzer)
                if f.name == "lits")
    with activate_compiler(compiler):
        fn = lower_function(func, entry.analyzer)

    def walk(node):
        yield node
        for child in _iter_children(node):
            yield from walk(child)
    # A print argument's literal is built beside the stamp and carries none.
    lits = [n for stmt in fn.body for n in walk(stmt)
            if isinstance(n, (th.THIRStrLiteral, th.THIRBytesLiteral))
            and n.source is not None]
    assert {n.static_view for n in lits} == {False, True}
    for n in lits:
        owned = (Held.FRESH if isinstance(n, th.THIRBytesLiteral)
                 else Held.VALUE)
        assert n.source.held is (Held.BORROWED if n.static_view
                                 else owned), n.value


def _concrete(base: type) -> set[type]:
    out: set[type] = set()
    stack = [base]
    while stack:
        for sub in stack.pop().__subclasses__():
            stack.append(sub)
            out.add(sub)
    return out


def _isinstance_targets(fn) -> set[str]:
    """Class names `fn` tests with `isinstance(x, THIRFoo)` or a tuple of
    them -- parsed, so a merely imported class does not count."""
    tree = ast.parse(inspect.getsource(fn))
    names: set[str] = set()
    for call in ast.walk(tree):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "isinstance" and len(call.args) == 2):
            target = call.args[1]
            elts = target.elts if isinstance(target, ast.Tuple) else [target]
            names.update(e.id for e in elts if isinstance(e, ast.Name))
    return names


def test_every_expression_kind_is_classified() -> None:
    """A node kind the stamp cannot classify fails at its first program;
    this walk fails at test time instead, the moment the kind is added."""
    named = _isinstance_targets(_held_from_facts) | {
        k.__name__ for k in _HELD_BY_KIND}
    missing = sorted(
        cls.__name__ for cls in _concrete(THIRExpr)
        if not any(b.__name__ in named for b in cls.__mro__[:-1]))
    assert missing == [], f"unclassified THIR expression kinds: {missing}"


_READS = '''\
from typing import Callable, Optional
from tpy import Own


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def reads(p: P, o: Optional[int], s: str, q: Own[P], xs: list[P]) -> int:
    x = P(1)
    t = s + "!"
    v = s[1:]
    pair = (P(2), 3)
    ys = [x.v for x in xs]
    total = len(ys) + len(t) + len(v) + pair[1] + x.v + p.v
    if (w := len(xs)) > 0:
        total += w
    if o is not None:
        total += o
    add: Callable[[int], int] = lambda n: n + total
    keep = q
    return add(keep.v)


def nested(p: P) -> int:
    base = p.v

    def inner(k: int) -> int:
        return k + base

    return inner(base)
'''


def _thir_names(fn) -> list[THIRName]:
    def walk(node):
        yield node
        for child in _iter_children(node):
            yield from walk(child)
    return [n for stmt in fn.body for n in walk(stmt)
            if isinstance(n, THIRName) and n.cpp is None]


@pytest.mark.parametrize("name", ["reads", "nested"])
def test_every_name_read_is_held_as_its_record_projects(name: str) -> None:
    # Every NAME read of a lowered body resolves to a record of the binding
    # table, the stamped `held` is that record's projection under the
    # read's type, and the name arm's `form` is that held's form.
    compiler, modules = _compile(_READS)
    entry = _entry(modules)
    func = next(f for f, _st in iter_module_callables(entry.ast,
                                                      entry.analyzer)
                if f.name == name)
    with activate_compiler(compiler):
        fn = lower_function(func, entry.analyzer)
        pre = _Prescan(func, entry.analyzer)
    reads = [n for n in _thir_names(fn) if not n.name.startswith("_")]
    assert reads
    for node in reads:
        rec = node.source.binding
        assert rec is not None and rec.name == node.name, node.name
        assert node.source.held is project_held(
            rec, node.result_type, node.deref, entry.analyzer,
            pre.param_names, pre.owned_viewfam_params), node.name
        assert _held_of(node.form, Form) is node.source.held, node.name


# One fixture per declaring node kind the lowering stamps: the function
# whose body reaches it, by the node kind.
_DECLARING = '''\
import asyncio
from typing import Iterator, Protocol, overload
from tpy import Own, ReturnException, auto_readonly, dynamic, error_return


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Res:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> "Res":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


class E(Exception, ReturnException):
    pass


@error_return(E)
def parse(n: int) -> int:
    if n < 0:
        raise E()
    return n


def with_item(r: Res) -> int:
    with r as t:
        t.n += 1
    return r.n


def match_bind(o: P | None) -> int:
    match o:
        case P(v=w):
            return w
        case _:
            return 0


def unpack(pair: tuple[P, int]) -> int:
    a, b = pair
    return a.v + b


@error_return(E)
def er_bind(n: int) -> int:
    k = parse(n)
    return k + 1


def make() -> Own[P]:
    return P(4)


def frame_slot(xs: list[P]) -> Iterator[int]:
    p = make()
    yield p.v
    p.v += 1
    yield p.v


class Q:
    w: int

    def __init__(self, w: int) -> None:
        self.w = w


@overload
def match_fold(o: P) -> int: ...
@overload
def match_fold(o: Q) -> int: ...
def match_fold(o: P | Q) -> int:
    match o:
        case P(v=x):
            return x
        case Q(w=y):
            return y
    return 0


def for_range(n: int) -> int:
    t = 0
    for i in range(n):
        t += i
    return t


def for_proto(it: Iterator[int]) -> int:
    t = 0
    for v in it:
        t += v
    return t


def for_each(xs: list[P]) -> int:
    t = 0
    for p in xs:
        t += p.v
    return t


def or_capture(o: P | Q) -> int:
    match o:
        case P(v=w) | Q(w=w):
            return w
    return 0


def same_capture(o: P | Q) -> int:
    match o:
        case P(v=x):
            return x
        case Q(w=x):
            return x + 1
    return 0


def loop_after() -> int:
    t = 0
    for k in [1, 2, 3]:
        t += k
    return t + k


class Count:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v

    def __enter__(self) -> int:
        return self.v

    def __exit__(self, et, ev, tb) -> None:
        pass


def with_comp(xs: list[int]) -> int:
    with Count(1) as a, Count(sum([b + a for b in xs])) as b:
        return a + b


def head_unpack(ps: list[tuple[int, P]]) -> int:
    t = 0
    for a, p in ps:
        t += a + p.v
    return t


class Bag:
    items: list[P]

    def __init__(self) -> None:
        self.items = [P(1)]

    @auto_readonly
    def at(self, i: int) -> P:
        b = self.items[i]
        return b


@dynamic
class Shape(Protocol):
    def area(self) -> int: ...


class Sq:
    def __init__(self, n: int) -> None:
        self.n = n

    def area(self) -> int:
        return self.n * self.n


def ptr_local(c: bool) -> int:
    s: Shape = Sq(1)
    if c:
        s = Sq(2)
    return s.area()


async def add_one(n: int) -> int:
    return n + 1


def coro_move() -> int:
    c = add_one(41)
    d = add_one(7)
    c = d
    return asyncio.run(c)
'''

# Each kind `_declaring_nodes` walks, by the body that reaches it (an
# @overload body keyed by its stub, `lower_bodies`).
_DECLARING_KINDS = {
    "with_item": (th.THIRWithItem,), "match_bind": (th.THIRMatchBinding,),
    "unpack": (th.THIRTupleUnpack,), "er_bind": (th.THIRErrorReturnBind,),
    "frame_slot": (th.THIRFrameSlotWrite,),
    "match_fold@0": (th.THIRMatchFoldBind,),
    "for_range": (th.THIRForRange, th.THIRVarDecl),
    "for_proto": (th.THIRForIterProto,), "for_each": (th.THIRForEach,),
    "ptr_local": (th.THIRPtrLocalRebind, th.THIRPtrLocalDecl),
    "coro_move": (th.THIRCoroHandleMove,),
    "or_capture": (th.THIRMatchBinding,),
    "same_capture": (th.THIRMatchBinding,),
    "loop_after": (th.THIRForEach,),
    "head_unpack": (th.THIRTupleUnpack, th.THIRForEach),
    "with_comp": (th.THIRWithItem, th.THIRForEach),
}


def _declaring_nodes(body):
    """Every node of a lowered body (a function or a frame's leaf tables)
    that declares (or reseats) a binding, with the names it declares, the
    records it carries and its source line (its own, else the nearest
    enclosing node's)."""
    one = {th.THIRVarDecl: "name", th.THIRPtrLocalDecl: "name",
           th.THIRPtrLocalRebind: "name", th.THIRErrorReturnBind: "name",
           th.THIRFrameSlotWrite: "name", th.THIRCoroHandleMove: "target",
           th.THIRForEach: "var", th.THIRForIterProto: "var",
           th.THIRForRange: "var", th.THIRWithItem: "target",
           th.THIRMatchBinding: "name", th.THIRMatchFoldBind: "name_cpp"}
    out = []

    def walk(node, line):
        if isinstance(node, (list, tuple)):
            for x in node:
                walk(x, line)
            return
        if isinstance(node, IdentityMap):
            for _key, x in node.items():
                walk(x, line)
            return
        if (not dataclasses.is_dataclass(node) or isinstance(node, type)
                or type(node).__module__ != th.__name__):
            return
        loc = getattr(node, "loc", None)
        line = loc.line if loc is not None else line
        if type(node) in one:
            out.append((node, (getattr(node, one[type(node)]),),
                        (node.binding,), line))
        elif isinstance(node, th.THIRTupleUnpack):
            out.append((node, node.targets, node.bindings, line))
        for f in dataclasses.fields(node):
            if f.name not in ("binding", "bindings", "source"):
                walk(getattr(node, f.name), line)
    walk(body.body if isinstance(body, th.THIRFunction) else body, None)
    return out


# The record families (a row's classifier, `_Site.kind`) a declaring node
# of each kind may carry as the record its own source site declares.
_LOOP_NODES = (th.THIRForEach, th.THIRForIterProto, th.THIRForRange)
_OWN_FAMILIES: dict[type, frozenset[str]] = {
    th.THIRWithItem: frozenset({"with"}),
    th.THIRMatchBinding: frozenset({"match"}),
    th.THIRMatchFoldBind: frozenset({"match"}),
    th.THIRTupleUnpack: frozenset({"unpack"}),
    **{k: frozenset({"loop", "comp"}) for k in _LOOP_NODES},
}
_SCOPED_FAMILIES = frozenset({"with", "match", "unpack", "loop", "comp",
                              "lambda", "param", "nested_param"})


def _site_lines(table: BindingTable) -> IdentityMap:
    """Each keyed site's source line; a comprehension clause carries none
    of its own and takes its comprehension's."""
    lines: IdentityMap = IdentityMap()
    for key in [*table.scope_at, *table.sites]:
        loc = getattr(key, "loc", None)
        if loc is not None:
            lines[key] = loc.line
            for gen in getattr(key, "generators", ()):
                lines[gen] = loc.line
    return lines


def _expected_record(table: BindingTable, node: object, name: str,
                     line: int | None) -> BindingRepr:
    """The record a declaring node must carry, derived from the SOURCE
    alone: the one record of its family some site on its line declares
    for `name`; with none, the binding of `name` visible at a statement of
    that line (a reassignment); else UNBOUND. Ambiguity fails the test."""
    fams = _OWN_FAMILIES.get(type(node))
    lines = _site_lines(table)

    def _line_of(x: object) -> int | None:
        return lines.get(x)

    def own_at(want: Callable[[int | None], bool]) -> list[BindingRepr]:
        found = {id(rec): rec for site in table.sites if want(_line_of(site))
                 for rec in table.sites[site] if rec.name == name
                 and ((rec.site_kind in fams) if fams is not None
                      else rec.site_kind not in _SCOPED_FAMILIES)}
        return list(found.values())
    own = own_at(lambda ln: ln == line)
    if not own and fams == {"match"} and line is not None:
        # A capture whose arm carries no line of its own sits under the
        # match statement's: the first case after it that binds the name.
        after = [ln for site in table.sites
                 if (ln := _line_of(site)) is not None and ln > line
                 and any(r.name == name and r.site_kind == "match"
                         for r in table.sites[site])]
        if after:
            own = own_at(lambda ln: ln == min(after))
    if len(own) == 1:
        return own[0]
    assert not own, (type(node).__name__, name, line,
                     [r.kind for r in own])
    seen = {id(r): r for key, scope in table.scope_at.items()
            if _line_of(key) == line
            for r in (scope.lookup(name),) if r is not None}
    assert len(seen) <= 1, (type(node).__name__, name, line,
                            [r.kind for r in seen.values()])
    return next(iter(seen.values()), UNBOUND)


def _table_records(table: BindingTable) -> set[int]:
    seen: set[int] = set()
    nodes = [table.root, *table.scope_at.values(),
             *table.stmt_expr_scope.values()]
    while nodes:
        node = nodes.pop()
        if node is None:
            continue
        seen.update(id(r) for r in node.bindings.values())
        nodes.append(node.parent)
    for site in table.sites:
        seen.update(id(r) for r in table.sites[site])
    return seen


def _assert_carries_table_records(body: object, table: BindingTable
                                  ) -> list:
    known = _table_records(table)
    decls = _declaring_nodes(body)
    assert decls
    for node, names, recs, line in decls:
        assert len(names) == len(recs), type(node).__name__
        for n, rec in zip(names, recs):
            if n is None:
                continue
            assert rec is not None, (type(node).__name__, n)
            assert rec is UNBOUND or (id(rec) in known and rec.name == n), (
                type(node).__name__, n, rec.form, rec.construct)
            want = _expected_record(table, node, n, line)
            assert rec is want, (type(node).__name__, n, line, rec.form,
                                 rec.construct, want.form, want.construct)
    return decls


@pytest.mark.parametrize("name", ["reads", "nested"])
def test_every_declaration_carries_the_tables_record(name: str) -> None:
    # Every binding-emitting node of a lowered body carries a record of
    # the body's own binding table -- the one that decided its
    # representation -- or the table's UNBOUND answer for a synthesized
    # name.
    bodies, tables = lower_bodies(_READS)
    _assert_carries_table_records(bodies[name], tables[name])


@pytest.mark.parametrize("name", sorted(_DECLARING_KINDS))
def test_each_declaring_node_kind_carries_the_tables_record(
        name: str) -> None:
    bodies, tables = lower_bodies(_DECLARING)
    decls = _assert_carries_table_records(bodies[name], tables[name])
    for kind in _DECLARING_KINDS[name]:
        assert any(isinstance(node, kind)
                   for node, _names, _recs, _line in decls), (
                       name, kind.__name__)


def test_auto_readonly_clones_each_carry_their_own_tables_records() -> None:
    # The two clones of an @auto_readonly method are two bodies with two
    # tables; a record from the other clone's table is a mismatch.
    bodies, tables = lower_bodies(_DECLARING)
    for name in ("Bag.at", "Bag.at[const]"):
        assert name in bodies and name in tables, name
        _assert_carries_table_records(bodies[name], tables[name])


# --- a tuple source's leaves (`element_source`) ---------------------------

_PAIR = TupleType((_REC, INT32))
# A parent whose every fact is set, so each leaf shows which it inherits.
_PARENT = Source(held=Held.STORAGE, const=True, movable=True,
                 must_copy=True, captured=True, temporary=True,
                 copy_declared=True, analyzed_type=_PAIR, named_type=_PAIR,
                 binding=_binding("t", pointer=False))


@pytest.mark.parametrize("layout", ["inline", "trait"])
def test_inline_leaf_is_part_of_its_parent(layout: str) -> None:
    # An inline element (by value, or an open `T` spelled through its
    # trait) lives in the parent's object: const, captured and dying with
    # it, copied where the whole must be, typed by its element.
    trait = TraitAbi.VAL_OR_PTR if layout == "trait" else None
    leaf = element_source(_PARENT, 0, held=Held.STORAGE, trait=trait)
    assert (leaf.held, leaf.trait, leaf.pointer_held) == (
        Held.STORAGE, trait, False)
    assert leaf.const and leaf.captured and leaf.must_copy
    assert leaf.temporary is True
    assert leaf.analyzed_type == _REC and leaf.named_type == _REC
    # A leaf is no NAME read of the parent's binding.
    assert leaf.binding is None and not leaf.id_read


def test_pointer_leaf_has_its_pointees_const_and_lifetime() -> None:
    # A pointer element's const is its pointee's, which the layout gives;
    # the pointee does not die with the tuple, and how long it lives is
    # not known here.
    mutable = element_source(_PARENT, 0, held=Held.BORROWED, pointer=True)
    const = element_source(_PARENT, 0, held=Held.BORROWED, pointer=True,
                           const=True)
    assert mutable.pointer_held and not mutable.const and const.const
    assert mutable.temporary is None
    assert mutable.captured and mutable.must_copy
    assert mutable.analyzed_type == _REC


@pytest.mark.parametrize("layout", ["inline", "pointer", "trait"])
def test_unknown_leaf_facts_grant_nothing(layout: str) -> None:
    # The parent's whole-value move and copy permission are not a leaf's:
    # what the layout does not state grants no move and no copy.
    leaf = element_source(
        _PARENT, 1, held=Held.VALUE, pointer=layout == "pointer",
        trait=TraitAbi.VAL_OR_PTR if layout == "trait" else None)
    assert not leaf.movable and not leaf.copy_declared
    assert leaf.pointee_owned is None and not leaf.undeclared
    assert leaf.analyzed_type == INT32


def test_layout_facts_are_the_producers() -> None:
    # What the producer states -- the occurrence's move, the pointee's
    # ownership, an undeclared read -- is kept as stated.
    leaf = element_source(_PARENT, 0, held=Held.BORROWED, pointer=True,
                          movable=True, pointee_owned=True)
    assert leaf.movable and leaf.pointee_owned is True
    leaf = element_source(_PARENT, 0, held=Held.BORROWED, pointer=True,
                          pointee_owned=False, undeclared=True)
    assert not leaf.movable and leaf.pointee_owned is False
    assert leaf.undeclared


@pytest.mark.parametrize("pointer", [False, True])
def test_own_expression_leaf_keeps_its_facts(pointer: bool) -> None:
    # A literal's element is an expression with a Source of its own: the
    # leaf is that Source, held as a pointer where the layout takes its
    # address, never inheriting the literal's facts.
    own = Source(held=Held.FRESH, temporary=True, movable=True,
                 analyzed_type=_REC)
    leaf = element_source(_PARENT, 0, own=own, pointer=pointer)
    assert leaf == replace(own, pointer_held=pointer)
