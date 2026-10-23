"""The binding table's scoping: which record a name resolves to at each
statement and scoping expression of a planned body. One record per
declaration site; a sema-hoisted name has one record at the statement that
predeclares it; with targets outlive their statement and comprehension
variables do not; a walrus target is visible from its statement on; a
nested def's own names shadow the enclosing ones; a binding that follows
another (`s = t`) reads the visible record. No lowering arm is pinned."""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from pathlib import Path

import pytest

from ..codegen_cpp.forms import LoopBinding
from ..compilation_context import activate_compiler
from ..parse.nodes import (TpyAssign, TpyExprStmt, TpyForEach,
                           TpyFunction, TpyIf,
                           TpyLambda, TpyListComprehension, TpyMatch,
                           TpyName, TpyNestedDef,
                           TpyReturn, TpyVarDecl, TpyWith, walk_body_stmts,
                           walk_expr_tree)
from ..sema.binding_ownership import (NOT_OWNED, Acquired, BindingOwnership,
                                     ReadKind, local_ownership)
from . import nodes as th
from .lower import bindings as _bindings
from .lower import iter_module_callables, lower_function
from .lower import match as _match
from .lower.bindings import (BindingTable, PlanInputs, ScopeNode,
                             plan_bindings, project_held)
from .lower.comprehensions import _clause_bindings, _clause_route
from .lower.context import (BindingTableError, FrameLocalClass, _LowerCtx,
                            _LowerScope, _Prescan, ValueOptKind)
from .lower.statements import _admit_hoist, _Hoist, _lower_first_decl
from .reject import ThirUnsupported, begin_stmt
from .source import BindingConstruct as C
from .source import BindingForm as F
from .source import BindingRefinement as R
from .source import BindingRepr, Held
from .source import MoveRow as M
from .testutil import (_assert_byte_identical, _compile, _entry,
                       lower_bodies)
from ..typesys import (OwnType, PendingListType, TupleType, unwrap_readonly,
                       unwrap_ref_type)
from .lower.storage import tuple_layout
from .validate import (THIRValidationError, _contract_problem,
                       validate_function, validate_stmts)

_PRELUDE = '''\
from tpy import Own


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Outer:
    p: P

    def __init__(self) -> None:
        self.p = P(2)


class Holder:
    pair: tuple[P, P]

    def __init__(self) -> None:
        self.pair = (P(1), P(2))


class Ctx:
    def __enter__(self) -> Own[P]:
        return P(3)

    def __exit__(self, et, ev, tb) -> None:
        pass

'''


_ANALYZERS: dict = {}


def _compile_analyzer(func):
    return _ANALYZERS[id(func)]


def _plan(source: str, name: str, prelude: str = _PRELUDE):
    compiler, modules = _compile(prelude + source)
    entry = _entry(modules)
    func = next(f for f, _st in iter_module_callables(entry.ast,
                                                      entry.analyzer)
                if f.name == name)
    with activate_compiler(compiler):
        lc = _LowerCtx(func, entry.analyzer, None)
        table = plan_bindings(PlanInputs(
            func=func, analyzer=entry.analyzer, prescan=lc.prescan,
            params=lc.params, seeds={n: t for n, t in func.params}),
            func.body)
    _ANALYZERS[id(func)] = (compiler, entry.analyzer)
    return func, table


def _sites(table, name: str) -> list:
    return [r for r in table.records() if r.name == name]


def _shape(rec: BindingRepr) -> tuple:
    """A record's declaring site, form and construction."""
    return (rec.site_kind, rec.form, rec.construct)


def test_sibling_arms_get_a_record_per_declaration_site() -> None:
    func, table = _plan('''
def pick(c: bool, o: Outer) -> int:
    if c:
        x = P(1)
        return x.v
    else:
        x = o.p
        return x.v
''', "pick")
    if_stmt = func.body[0]
    assert isinstance(if_stmt, TpyIf)
    then_decl, then_ret = if_stmt.then_body
    else_decl, else_ret = if_stmt.else_body
    recs = _sites(table, "x")
    assert len(recs) == 2
    assert {id(r.site) for r in recs} == {id(then_decl), id(else_decl)}
    # Each arm's read resolves to its own arm's record.
    assert table.visible(then_ret, "x").site is then_decl
    assert table.visible(else_ret, "x").site is else_decl
    # Neither is visible at the `if` itself.
    assert table.visible(if_stmt, "x") is None
    # The two declarations bind the same representation.
    a, b = recs
    assert (a.pointer, a.ref_alias) == (b.pointer, b.ref_alias)


def test_a_hoisted_name_has_one_record_at_its_predeclaring_statement() -> None:
    func, table = _plan('''
def pick2(c: bool, o: Outer) -> int:
    if c:
        x = P(1)
    else:
        x = o.p
    return x.v
''', "pick2")
    if_stmt, ret = func.body
    recs = _sites(table, "x")
    assert len(recs) == 1
    assert recs[0].site is if_stmt
    assert table.visible(ret, "x") is recs[0]
    # The arms' bindings are reseats of the predeclared record.
    assert table.visible(if_stmt.then_body[0], "x") is recs[0]
    assert table.visible(if_stmt.else_body[0], "x") is recs[0]


def test_a_with_target_outlives_its_statement() -> None:
    func, table = _plan('''
def w() -> int:
    with Ctx() as t:
        n = t.v
    return t.v + n
''', "w")
    with_stmt, ret = func.body
    assert isinstance(with_stmt, TpyWith)
    rec = table.visible(with_stmt.body[0], "t")
    assert rec is not None and rec.site is with_stmt.items[0]
    assert table.visible(ret, "t") is rec
    # A body name read after the statement is sema-hoisted to it.
    assert table.visible(ret, "n").site is with_stmt


def test_a_comprehension_variable_shadows_inside_and_is_gone_after() -> None:
    func, table = _plan('''
def g(xs: list[int]) -> int:
    x = 5
    ys = [x * 2 for x in xs]
    return x + len(ys)
''', "g")
    decl_x, decl_ys, ret = func.body
    outer = table.visible(ret, "x")
    assert outer is not None and outer.site is decl_x
    comp = decl_ys.init
    gen = comp.generators[0]
    inner = table.visible(gen, "x")
    assert inner is not None and inner.site is gen and inner is not outer
    assert table.visible(comp, "x") is inner
    # The source is evaluated in the enclosing scope.
    assert table.visible(decl_ys, "x") is outer


def test_a_walrus_target_is_visible_from_its_statement_on() -> None:
    func, table = _plan('''
def wal(xs: list[int]) -> int:
    if (n := len(xs)) > 2:
        return n
    return n + 1
''', "wal")
    if_stmt, ret = func.body
    rec = table.visible(if_stmt, "n")
    assert rec is not None
    assert table.visible(if_stmt.then_body[0], "n") is rec
    assert table.visible(ret, "n") is rec


def test_a_nested_def_param_shadows_the_enclosing_local() -> None:
    func, table = _plan('''
def outer_fn() -> int:
    v = 3

    def inner(v: int) -> int:
        return v + 1

    return inner(v)
''', "outer_fn")
    decl_v, nested, ret = func.body
    assert isinstance(nested, TpyNestedDef)
    outer_v = table.visible(ret, "v")
    assert outer_v.site is decl_v
    inner_ret = nested.func.body[0]
    assert isinstance(inner_ret, TpyReturn)
    inner_v = table.visible(inner_ret, "v")
    assert inner_v is not outer_v and inner_v.is_param
    # The nested def's name is a binding of the enclosing block.
    assert table.visible(ret, "inner") is not None


def test_a_name_bound_from_another_reads_its_record() -> None:
    func, table = _plan('''
def f(h: Holder) -> int:
    t = h.pair
    s = t
    return s[0].v
''', "f")
    decl_t, decl_s, ret = func.body
    t = table.visible(decl_s, "t")
    s = table.visible(ret, "s")
    assert t.site is decl_t and s.site is decl_s
    assert t.storage_tuple and s.storage_tuple


def test_a_loop_body_declaration_is_not_visible_before_it() -> None:
    func, table = _plan('''
def loop(xs: list[int]) -> int:
    total = 0
    for x in xs:
        print(total)
        y = x + 1
        total = total + y
    return total
''', "loop")
    _decl, loop_stmt, _ret = func.body
    assert isinstance(loop_stmt, TpyForEach)
    first, decl_y, last = loop_stmt.body
    assert isinstance(first, TpyExprStmt) and isinstance(decl_y, TpyVarDecl)
    assert table.visible(first, "y") is None
    assert table.visible(last, "y").site is decl_y
    assert table.visible(first, "x").site is loop_stmt


def test_a_comprehension_variable_has_the_type_its_clause_binds() -> None:
    func, table = _plan('''
from tpy import int32


class Q:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def lens(xs: list[int], names: list[str], qs: list[tuple[str, Q]]) -> int:
    ys = [x * 2 for x in xs]
    ns = [len(s) for s in names]
    zs = [len(a) + b.v for a, b in qs]
    return len(ys) + len(ns) + len(zs)
''', "lens")
    declared = dict(func.params)
    compiler, an = _compile_analyzer(func)
    for decl in func.body[:3]:
        gen = decl.init.generators[0]
        with activate_compiler(compiler):
            route = _clause_route("list", gen, declared, an)
        assert route is not None, decl.name
        # The record's type is the one the clause's body declares the name
        # at -- the same producer, so a str element is the resolved view
        # or owned str and an unpack target its element's type.
        for name, t in _clause_bindings(gen, route):
            rec = table.visible(gen, name)
            assert rec is not None and rec.site is gen
            assert rec.type == t


def test_a_match_capture_lives_in_its_arm_and_ends_with_it() -> None:
    func, table = _plan('''
def cap(o: Outer | P) -> int:
    match o:
        case Outer(p=q):
            n = q.v
        case P(v=w):
            n = w
    return n
''', "cap")
    match_stmt, ret = func.body
    arm, other = match_stmt.cases
    rec = table.visible(arm.body[0], "q")
    assert rec is not None and rec.site is arm
    # Typed to the captured field.
    assert rec.type is not None and rec.type.name == "P"
    # Not visible to a sibling arm, nor after the statement: its C++
    # binding is local to the arm.
    assert table.visible(other.body[0], "q") is None
    assert table.visible(other.body[0], "w").site is other
    assert table.visible(ret, "q") is None


def test_a_later_binding_of_a_closed_capture_declares_its_own() -> None:
    # A second match's capture and a `with` target after a match bind the
    # name afresh: the earlier arm's binding has ended.
    func, table = _plan('''
def again(u: int, w: int, o: Outer, c: Ctx) -> int:
    match u:
        case 1:
            pass
        case x:
            print(x)
    match w:
        case 2:
            pass
        case x:
            x += 5
            print(x)
    match o:
        case Outer(p=q):
            q.v += 1
    with c as q:
        q.v += 2
    return q.v
''', "again")
    first, second, third, with_stmt, _ret = func.body
    x2 = table.visible(second.cases[1].body[0], "x")
    assert x2 is not None and x2.site is not first.cases[1]
    q = table.visible(with_stmt.body[0], "q")
    assert q is not None and q.site is not third.cases[0]
    assert q.site_kind != "match"


def test_a_str_read_is_held_as_its_read_type_over_an_owned_record() -> None:
    # The declaration after the block falls back to owned `str` while the
    # read resolves through the arm binding to a view
    # (BUGS.md#str-rebind-after-block-local-owns): the projection takes
    # the view / owned axis from the read's type, everything else from the
    # record, so the read stays BORROWED as the lowering renders it.
    func, table = _plan('''
from tpy import int32


def rebind_after(k: int32) -> None:
    if k == 1:
        print("one")
    elif k == 2:
        r = "two"
        print("x " + r)
    else:
        r = "other"
        print("x " + r)
    r = "after"
    print("x " + r)
''', "rebind_after")
    _if_stmt, decl, use = func.body
    rec = table.visible(use, "r")
    assert rec is not None and rec.site is decl
    compiler, an = _compile_analyzer(func)
    reads = []
    walk_expr_tree(use.expr, lambda x: (isinstance(x, TpyName)
                                        and x.name == "r"
                                        and reads.append(x)) or True)
    read, = reads
    with activate_compiler(compiler):
        pre = _Prescan(func, an)
        owned = project_held(rec, rec.type, False, an, pre.param_names,
                             pre.owned_viewfam_params)
        held = project_held(rec, an.get_expr_type(read), False, an,
                            pre.param_names, pre.owned_viewfam_params)
    assert owned is Held.STORAGE
    assert held is Held.BORROWED


_DECLS = '''
from tpy import int32


def decls(o: Outer, other: Outer, xs: list[int32], c: bool) -> int:
    alias = o.p
    cur = o.p
    if c:
        cur = other.p
    total = 0
    for x in xs:
        total += x
    return alias.v + cur.v + total
'''


def _lowered_decls():
    compiler, modules = _compile(_PRELUDE + _DECLS)
    entry = _entry(modules)
    func = next(f for f, _st in iter_module_callables(entry.ast,
                                                      entry.analyzer)
                if f.name == "decls")
    with activate_compiler(compiler):
        return lower_function(func, entry.analyzer)


def test_a_lowered_declaration_carries_its_record_and_passes() -> None:
    # The lowering stamps every declaring node with the table's record of
    # what it declares; its representation agrees with the record, so the
    # strict (lowered) validation passes.
    fn = _lowered_decls()
    decls = {s.name: s for s in fn.body if isinstance(s, th.THIRVarDecl)}
    assert decls["alias"].binding.ref_alias
    assert decls["cur"].binding.pointer
    loop, = [s for s in fn.body if isinstance(s, th.THIRForEach)]
    assert loop.binding.name == "x" and loop.binding.site_kind == "loop"
    validate_function(fn, lowered=True)
    # Hand-built THIR (no lowering behind it) may leave the record out.
    bare = replace(fn, body=tuple(replace(s, binding=None)
                                  if isinstance(s, th.THIRVarDecl) else s
                                  for s in fn.body))
    validate_function(bare)


def test_a_lowered_declaration_needs_its_record() -> None:
    # A lowered declaration with no record at all is an internal error.
    fn = _lowered_decls()
    alias = next(s for s in fn.body
                 if isinstance(s, th.THIRVarDecl) and s.name == "alias")
    with pytest.raises(THIRValidationError, match="carries no record"):
        validate_function(replace(fn, body=tuple(
            replace(alias, binding=None) if s is alias else s
            for s in fn.body)), lowered=True)


def test_a_lambda_parameter_shadows_the_enclosing_local() -> None:
    func, table = _plan('''
from typing import Callable


def lam() -> int:
    x = 5
    f: Callable[[int], int] = lambda x: x + 1
    return f(2) + x
''', "lam")
    decl_x, decl_f, ret = func.body
    lam_e = decl_f.init
    while not isinstance(lam_e, TpyLambda):
        lam_e = lam_e.expr
    inner = table.scope_at[lam_e].lookup("x")
    assert inner is not None and inner.site is lam_e and inner.is_param
    # The read after the lambda is the enclosing local's.
    assert table.visible(ret, "x").site is decl_x


# Bodies reaching each declaration contract the validator checks; every
# negative below edits one real lowered record (or node) to contradict it.
_CONTRACTS = _PRELUDE + '''
from typing import Iterator


def ptr_union(c: bool, p: P) -> int:
    u: P | Outer = p
    if c:
        u = Outer()
    if isinstance(u, P):
        return u.v
    return 0


def unpack(pair: tuple[P, int]) -> int:
    a, b = pair
    return a.v + b


def make() -> Own[P]:
    return P(4)


def frame_slot() -> Iterator[int]:
    p = make()
    yield p.v
    p.v += 1
    yield p.v


def consume() -> None:
    # Spelled: the inferred literal's loop does not lower as consuming.
    items: list[P] = [P(1), P(2)]
    out: list[P] = []
    for x in items:
        out.append(x)
    print(len(out))
'''


def _swap(node: object, old: object, new: object) -> object:
    """`node` with `old` (by identity) replaced by `new` wherever it sits."""
    if node is old:
        return new
    if isinstance(node, tuple):
        items = tuple(_swap(x, old, new) for x in node)
        return items if any(a is not b for a, b in zip(items, node)) else node
    if (not dataclasses.is_dataclass(node) or isinstance(node, type)
            or type(node).__module__ != th.__name__):
        return node
    changes = {}
    for f in dataclasses.fields(node):
        if f.name in ("binding", "bindings", "source"):
            continue
        v = getattr(node, f.name)
        nv = _swap(v, old, new)
        if nv is not v:
            changes[f.name] = nv
    return replace(node, **changes) if changes else node


def _find(fn: th.THIRFunction, kind: type, name: str | None = None
          ) -> object:
    found: list[object] = []

    def walk(node: object) -> None:
        if isinstance(node, tuple):
            for x in node:
                walk(x)
            return
        if (not dataclasses.is_dataclass(node) or isinstance(node, type)
                or type(node).__module__ != th.__name__):
            return
        if isinstance(node, kind) and (
                name is None or getattr(node, "name", None) == name
                or getattr(node, "var", None) == name):
            found.append(node)
        for f in dataclasses.fields(node):
            if f.name not in ("binding", "bindings", "source"):
                walk(getattr(node, f.name))
    walk(fn.body)
    return found[0]


@pytest.fixture(scope="module")
def contracts() -> dict[str, object]:
    bodies, _tables = lower_bodies(_CONTRACTS)
    return bodies


def _rejects(fn: th.THIRFunction, old: object, new: object,
             match: str) -> None:
    validate_function(fn, lowered=True)
    with pytest.raises(THIRValidationError, match=match):
        validate_function(_swap(fn, old, new), lowered=True)


def test_a_union_slot_of_a_non_pointer_variant_record_is_rejected(
        contracts) -> None:
    fn = contracts["ptr_union"]
    decl = _find(fn, th.THIRPtrLocalDecl, "u")
    _rejects(fn, decl, replace(decl, binding=replace(
        decl.binding, ptr_variant=False)), "non-pointer-variant binding")


def test_an_unpack_needs_one_record_per_target(contracts) -> None:
    fn = contracts["unpack"]
    up = _find(fn, th.THIRTupleUnpack)
    _rejects(fn, up, replace(up, bindings=up.bindings[:1]),
             "one record per unpack target")


def test_a_consuming_loop_variable_must_be_movable(contracts) -> None:
    fn = contracts["consume"]
    loop = _find(fn, th.THIRForEach, "x")
    assert loop.consuming
    _rejects(fn, loop, replace(loop, binding=replace(
        loop.binding, movable=False)), "consuming loop variable")


def test_a_frame_emplace_into_a_non_frame_record_is_rejected(
        contracts) -> None:
    write = next(w for w in contracts["frame_slot"].leaves.values()
                 if isinstance(w, th.THIRFrameSlotWrite) and w.name == "p")
    validate_stmts("frame_slot", [write], lowered=True)
    wrong = replace(write, binding=replace(write.binding, frame_class=None,
                                           form=F.VALUE, coro_frame=False))
    with pytest.raises(THIRValidationError, match="no frame slot"):
        validate_stmts("frame_slot", [wrong], lowered=True)


# One body per scope the planner keys apart from a statement: with items (a
# lambda in a later item's manager), an except handler, every match route
# (record, poly, optional, optional chain, str switch, union, guarded
# union, folded), a folded `if` with a live link, comprehension clauses, a
# nested def, a constructor member-init, and a generator whose `with` and
# `match` span a yield.
_SCOPES = '''\
from typing import Callable, Iterator, Optional, Protocol, overload

from tpy import dynamic, int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Q:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


@dynamic
class Pet(Protocol):
    def size(self) -> int32: ...


class Dog(Pet):
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def size(self) -> int32:
        return self.k


class Count:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __enter__(self) -> int32:
        return self.v

    def __exit__(self, et, ev, tb) -> None:
        pass


class Cb:
    f: Callable[[], int32]

    def __init__(self, f: Callable[[], int32]) -> None:
        self.f = f

    def __enter__(self) -> int32:
        return self.f() + 100

    def __exit__(self, et, ev, tb) -> None:
        pass


class Grid:
    cells: list[int32]

    def __init__(self, xs: list[int32]) -> None:
        self.cells = [x * 2 for x in xs]


def with_items(xs: list[int32]) -> int32:
    with Count(7) as n, Cb(lambda: n) as r:
        return n + r


def handler(k: int32) -> int32:
    try:
        if k > 0:
            raise ValueError("v")
    except ValueError as e:
        return 1
    return 0


def m_record(o: P) -> int32:
    match o:
        case P(v=w) if w > 1:
            return w
        case P(v=w):
            return -w


def m_poly(o: Pet) -> int32:
    match o:
        case Dog() as d if d.k > 0:
            return d.k
        case other:
            return other.size()


def m_optional(o: P | None) -> int32:
    match o:
        case None:
            return 0
        case P(v=w):
            return w


def m_optional_chain(o: Optional[int32]) -> int32:
    match o:
        case 1:
            return 10
        case None:
            return 0
        case v:
            return v


def m_switch_str(s: str) -> int32:
    match s:
        case "a":
            return 1
        case "b":
            return 2
        case "c":
            return 3
        case "d":
            return 4
        case "e":
            return 5
        case t:
            return len(t)


def m_scalar(k: int32) -> int32:
    match k:
        case 1:
            return 10
        case 2 if k > 0:
            return 20
        case 3 | 4:
            return 30
        case v:
            return v


def m_union(o: P | Q) -> int32:
    match o:
        case P(v=x):
            return x
        case Q(w=x):
            return x + 1


def m_guarded_union(o: P | Q, flag: bool) -> int32:
    match o:
        case P(v=x) if flag:
            return x
        case Q(w=x) if x > 0:
            return x + 1
        case _:
            return 0


@overload
def m_fold(o: P) -> int32: ...
@overload
def m_fold(o: Q) -> int32: ...
def m_fold(o: P | Q) -> int32:
    match o:
        case P(v=x):
            return x
        case Q(w=y):
            return y
    return 0


@overload
def if_fold(o: P, flag: bool) -> int32: ...
@overload
def if_fold(o: Q, flag: bool) -> int32: ...
def if_fold(o: P | Q, flag: bool) -> int32:
    if isinstance(o, Q):
        return o.w
    elif flag:
        n = 1
        return n
    return 0


def comp(xs: list[int32]) -> int32:
    return sum([x * y for x in xs for y in xs if x > y])


def nested(k: int32) -> int32:
    def inner(j: int32) -> int32:
        m = j + k
        return m
    return inner(2)


def gen(o: P | Q) -> Iterator[int32]:
    with Count(3) as n:
        yield n
        match o:
            case P(v=x):
                yield x
            case Q(w=x):
                yield x + 1
        yield n


def gen_try(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        try:
            yield x
            if x > 1:
                raise ValueError("v")
        except ValueError as e:
            yield len(str(e))
        if x == 0:
            continue
        if x > 2:
            break


'''


def test_every_planned_scope_is_entered_by_the_lowering() -> None:
    sink: list = []
    saved = _bindings.SHADOW_SINK
    _bindings.SHADOW_SINK = sink
    try:
        lower_bodies(_SCOPES)
    finally:
        _bindings.SHADOW_SINK = saved
    functions = {e[1] for e in sink if e[0] == "_stat:functions"}
    for name in ("with_items", "handler", "m_record", "m_poly", "m_optional",
                 "m_optional_chain", "m_switch_str", "m_scalar", "m_union",
                 "m_guarded_union", "m_fold", "if_fold", "comp", "nested",
                 "gen", "gen_try", "Grid.__init__"):
        assert f"__main__:{name}" in functions, name
    missed = [e for e in sink
              if e[0].startswith(("scope_unentered:", "cursor_miss:"))]
    assert not missed, missed
    # Only the generators' CFGs decompose a statement.
    assert {e[1] for e in sink if e[0].startswith("scope_unentered_cfg:")} \
        <= {"__main__:gen", "__main__:gen_try"}


def _installed(source: str,
               name: str) -> tuple[TpyFunction, BindingTable, _LowerCtx]:
    """`name`'s planned table installed on a lowering context, the cursor
    at the body's root."""
    func, table = _plan(source, name)
    compiler, analyzer = _compile_analyzer(func)
    with activate_compiler(compiler):
        lc = _LowerCtx(func, analyzer, None)
    _bindings.install(lc, table)
    return func, table, lc


def test_declaring_a_name_its_site_has_no_record_for_is_an_error() -> None:
    func, _table, lc = _installed('''
def one() -> int:
    x = 1
    return x
''', "one")
    _bindings.enter(lc, func.body[0])
    assert lc.declaring("x").site is func.body[0]
    with pytest.raises(BindingTableError, match="no record for 'y'"):
        lc.declaring("y")


def test_a_with_item_handler_or_case_built_outside_its_scope_is_an_error(
        ) -> None:
    func, _table, lc = _installed('''
class Count:
    def __init__(self, v: int) -> None:
        self.v = v

    def __enter__(self) -> int:
        return self.v

    def __exit__(self, et, ev, tb) -> None:
        pass


def parts(k: int) -> int:
    with Count(k) as n:
        pass
    try:
        n += 1
    except ValueError as e:
        n = 0
    match k:
        case 1:
            return n
    return 0
''', "parts")
    item = func.body[0].items[0]
    handler = func.body[1].handlers[0]
    case = func.body[2].cases[0]
    # The cursor is at the body's root, not at any of the three.
    for key in (item, handler, case):
        with pytest.raises(BindingTableError,
                           match="built outside its scope"):
            _bindings.require_entered(lc, key)
    with pytest.raises(BindingTableError, match="built outside its scope"):
        _match._case_entry(case, lc)
    for key in (item, handler, case):
        with _bindings.at(lc, key):
            _bindings.require_entered(lc, key)


def test_two_records_of_one_name_at_a_site_are_an_error() -> None:
    site = object()
    _func, planned = _plan('''
def two(k: int) -> int:
    return k
''', "two")
    table = BindingTable(ScopeNode(None), planned.inp)
    one = BindingRepr(name="x", site=site, type=None, form=F.NONE)
    table.sites[site] = (one,)
    assert table.declared_by(site, "x") is one
    assert table.declared_by(site, "y") is None
    table.sites[site] = (one, replace(one))
    with pytest.raises(BindingTableError, match="2 records for 'x'"):
        table.declared_by(site, "x")


def test_entering_an_unplanned_key_is_an_error_but_a_lambda_is_not() -> None:
    func, _table, lc = _installed('''
def two(k: int) -> int:
    return k
''', "two")
    stray = TpyName(name="k")
    with pytest.raises(BindingTableError, match="no scope planned"):
        _bindings.enter(lc, stray)
    # A lambda a lowering arm builds in place of a callable name was never
    # walked: entering it opens its parameter scope over the cursor.
    lam = TpyLambda(param_names=["q"], body=TpyName(name="q"))
    _bindings.enter(lc, lam)
    assert lc.cursor[0] is lam
    rec = lc.binding("q")
    assert rec.site is lam and rec.is_param
    assert lc.binding("k").is_param


def test_a_later_item_manager_and_a_guard_see_their_scopes() -> None:
    func, table = _plan('''
class Pass:
    n: int

    def __init__(self, p: P) -> None:
        self.n = p.v

    def __enter__(self) -> int:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        pass


def items() -> int:
    with Ctx() as t, Pass(t) as u:
        return t.v + u
''', "items")
    with_stmt = func.body[0]
    assert isinstance(with_stmt, TpyWith)
    first, second = with_stmt.items
    # The second item's manager reads the first item's target.
    rec = table.stmt_expr_scope[second.context_expr].lookup("t")
    assert rec is not None and rec.site is first
    assert table.stmt_expr_scope[first.context_expr].lookup("t") is None
    func, table = _plan('''
def guard(o: Outer | P) -> int:
    match o:
        case P(v=w) if w > 1:
            return w
        case _:
            return 0
''', "guard")
    match_stmt = func.body[0]
    assert isinstance(match_stmt, TpyMatch)
    case = match_stmt.cases[0]
    # The guard is planned at its case's node, where the capture is bound.
    assert table.stmt_expr_scope[case.guard] is table.scope_at[case]
    assert table.scope_at[case].lookup("w").site is case


def test_a_folded_away_elif_link_has_no_scope() -> None:
    _bodies, tables = lower_bodies('''\
from typing import overload


class P:
    def __init__(self, v: int) -> None:
        self.v = v


class Q:
    def __init__(self, w: int) -> None:
        self.w = w


@overload
def fold(o: P, flag: bool) -> int: ...
@overload
def fold(o: Q, flag: bool) -> int: ...
def fold(o: P | Q, flag: bool) -> int:
    if flag:
        return 1
    elif isinstance(o, Q):
        return o.w
    else:
        return 0
''')
    table = tables["fold@0"]
    head = next(k for k in table.scope_at
                if isinstance(k, TpyIf) and k.else_body
                and isinstance(k.else_body[0], TpyIf))
    # Under the P stub the `isinstance(o, Q)` link folds away; the live
    # link before it and the statement keep their scopes.
    assert head in table.scope_at
    assert head.else_body[0] not in table.scope_at


def test_a_tuple_target_write_binds_its_records_pointer_tuple() -> None:
    # A tuple-target write (`t = <rvalue>` of the pointer tuple) carries the
    # record of the tuple it binds: a local's `ptr_tuple`, or a module
    # global held as pointer slots. A record of any other form, a target
    # type that is not the record's pointer tuple, an element landed
    # against its form and a missing record each fail the contract.
    func, table = _plan('''
def pair(t: tuple[bool, P]) -> int:
    return t[1].v
''', "pair")
    compiler, an = _compile_analyzer(func)
    tt = unwrap_readonly(unwrap_ref_type(table.root.lookup("t").type))
    with activate_compiler(compiler):
        layout = tuple_layout(tt, an, borrow=True)
    assert layout is not None
    glob = BindingRepr(name="M", site=None, type=tt,
                       form=F.BORROW_TUPLE, site_kind="module_global",
                       tuple_layout=layout)
    local = BindingRepr(name="t", site=None, type=tt,
                        form=F.BORROW_TUPLE, site_kind="decl",
                        ptr_tuple=tt)
    plain = replace(local, ptr_tuple=None)
    elems = (th.THIRTupleElemTarget(index=0, elem_type=tt.element_types[0],
                                    bind=th.TupleElemBind.VALUE),
             th.THIRTupleElemTarget(index=1, elem_type=tt.element_types[1],
                                    bind=th.TupleElemBind.OWNED,
                                    slot_cpp="P"))

    def write(name: str, rec, *, decl: bool, elem_targets=elems):
        return th.THIRTupleUnpack(
            source="", targets=(), target_cpps=(),
            source_bind=th.TupleSourceBind.RVALUE,
            source_expr=th.THIRName(result_type=tt, name="src"),
            tuple_target=th.THIRName(result_type=tt, name=name),
            elem_targets=elem_targets,
            tuple_decl_cpp="std::tuple<bool, P*>" if decl else None,
            tuple_binding=rec)

    def fails(node, match: str) -> None:
        with pytest.raises(THIRValidationError, match=match):
            validate_stmts("pair", [node], lowered=True)

    validate_stmts("pair", [write("t", local, decl=True)], lowered=True)
    validate_stmts("pair", [write("M", glob, decl=False)], lowered=True)
    fails(write("t", plain, decl=True), "no pointer tuple")
    fails(write("M", glob, decl=True), "no pointer tuple")
    fails(write("t", replace(local, ptr_tuple=TupleType((tt.element_types[1],
                                                       tt.element_types[1]))),
                decl=True), "not its record's pointer tuple")
    fails(write("t", local, decl=True, elem_targets=(replace(
        elems[0], bind=th.TupleElemBind.BORROW), elems[1])),
        "against its form")
    fails(write("t", None, decl=True), "carries no record")


def test_a_reassigned_borrow_tuple_declares_the_rebound_kind() -> None:
    # A reassigned pointer-repr tuple local is declared in the one borrow
    # shape every binding of it holds, a literal first initializer included.
    func, table = _plan('''
def pick(h: Holder, a: P, b: P) -> int:
    t = (a, b)
    t = h.pair
    return t[0].v
''', "pick")
    rec = table.declared_by(func.body[0], "t")
    assert _shape(rec) == ("decl", F.BORROW_TUPLE, C.BTUPLE_REBOUND)
    assert rec.effective_type is not None


def test_an_annotation_only_value_decl_has_its_kind() -> None:
    # Every default-constructed value slot is the no-init kind, not only a
    # value-repr Optional one.
    func, table = _plan('''
def pick(c: bool) -> int:
    x: int
    if c:
        x = 1
    else:
        x = 2
    return x
''', "pick")
    rec = table.declared_by(func.body[0], "x")
    assert _shape(rec) == ("decl", F.VALUE, C.NO_INIT_VALUE)


# The kinds whose builders refuse some or all of the shapes the planner
# gives them: the planner alone picks the kind, the builder rejects.
_REJECT_KINDS = '''\
from typing import Protocol

from tpy import dynamic


class Q:
    w: int

    def __init__(self, w: int) -> None:
        self.w = w


@dynamic
class Pet(Protocol):
    def size(self) -> int: ...


class Dog(Pet):
    def __init__(self, k: int) -> None:
        self.k = k

    def size(self) -> int:
        return self.k

'''


def _decl_rec(source: str, name: str, var: str) -> BindingRepr:
    func, table = _plan(_REJECT_KINDS + source, name)
    decls = [r for r in table.records()
             if r.name == var and isinstance(r.site, TpyVarDecl)]
    return decls[0]


def test_a_branch_first_rebound_pointer_variant_is_the_slot_kind() -> None:
    # Its rebind slot would hoist out of the branch the declaration sits
    # in: `decl.branch_ptr_union`.
    rec = _decl_rec('''
def pick(c: bool, a: P) -> int:
    if c:
        u: P | Q = a
        u = Q(2)
        return 1
    return 0
''', "pick", "u")
    assert _shape(rec) == ("decl", F.POINTER_UNION, C.PTR_VARIANT_SLOT)
    assert rec.rebind_slot


def test_a_ternary_of_differently_inline_borrow_tuples_is_its_own_kind(
        ) -> None:
    # No one `auto&` aliases arms holding different elements inline:
    # `decl.btuple_alias_inline_arms`.
    rec = _decl_rec('''
def pick(c: bool, a: P, b: P) -> int:
    t = (P(1), b)
    s = (a, b)
    v = t if c else s
    return v[0].v
''', "pick", "v")
    assert _shape(rec) == ("decl", F.BORROW_TUPLE, C.BTUPLE_INLINE_ARMS)


def _plan_inputs(source: str, name: str, **prescan_facts):
    compiler, modules = _compile(_PRELUDE + _REJECT_KINDS + source)
    entry = _entry(modules)
    func = next(f for f, _st in iter_module_callables(entry.ast,
                                                      entry.analyzer)
                if f.name == name)
    with activate_compiler(compiler):
        lc = _LowerCtx(func, entry.analyzer, None)
        for fact, names in prescan_facts.items():
            setattr(lc.prescan, fact, getattr(lc.prescan, fact) | names)
        inp = PlanInputs(
            func=func, analyzer=entry.analyzer, prescan=lc.prescan,
            params=lc.params, seeds={n: t for n, t in func.params})
    return compiler, func, inp


def test_a_hoisted_dynamic_local_is_the_dynamic_kind() -> None:
    # No program hoists a @dynamic local declared outside a branch (an escape
    # hoist's source sits in a loop or branch body), so the fact is given.
    compiler, func, inp = _plan_inputs('''
def pick(n: int) -> int:
    d: Pet = Dog(n)
    return d.size()
''', "pick", hoisted=frozenset({"d"}))
    with activate_compiler(compiler):
        table = plan_bindings(inp, func.body)
    assert _shape(table.declared_by(func.body[0], "d")) == ("decl", F.DYN, C.DYN)


def test_an_assign_to_a_name_nothing_binds_is_an_assign_site() -> None:
    # The Python parser spells a first `z = n` as a declaration; a plugin
    # front end (`frontend_ir`) hands over a plain assign to the name.
    compiler, func, inp = _plan_inputs('''
def pick(n: int) -> int:
    z = n
    return z
''', "pick")
    body = [TpyAssign(target=TpyName("z"), value=func.body[0].init),
            *func.body[1:]]
    with activate_compiler(compiler):
        table = plan_bindings(inp, body)
    rec = table.declared_by(body[0], "z")
    assert _shape(rec) == ("assign", F.NONE, None)


def test_an_unresolved_pending_list_is_the_container_slot_kind() -> None:
    # Planning sees every list literal resolved, so the row is asked with an
    # unresolved literal type directly.
    compiler, func, inp = _plan_inputs('''
def pick(c: bool) -> int:
    xs = [1]
    ys = [2, 3]
    xs = ys
    return len(xs)
''', "pick")
    stmt = func.body[0]
    with activate_compiler(compiler):
        pending = PendingListType(
            element_type=inp.analyzer.get_expr_type(stmt.init.elements[0]),
            size=1, literal_id=0)
        rec = _bindings._decl_row(
            _bindings._Site("decl", node=stmt, init=stmt.init,
                            storage=_bindings.StorageVerdict(pointer=True)),
            "xs", pending, inp, lambda n: None)
    assert _shape(rec) == ("decl", F.POINTER, C.CONTAINER_SLOT)


def test_a_frame_slot_reaching_the_first_declaration_is_an_error(
        monkeypatch) -> None:
    # A frame slot is written by the frame's own arms and the planner never
    # gives a local declaration one, so the record is forged.
    decl_row = _bindings._decl_row

    def frame_row(site, name, typ, inp, visible):
        rec = decl_row(site, name, typ, inp, visible)
        return (replace(rec, frame_class=FrameLocalClass.FRAME_SLOT,
                        form=F.FRAME_SLOT)
                if name == "x" else rec)
    monkeypatch.setitem(_bindings._ROWS, "decl", (frame_row,))
    func, _table, lc = _installed('''
def one() -> int:
    x = P(1)
    return x.v
''', "one")
    stmt = func.body[0]
    _bindings.enter(lc, stmt)
    compiler, _an = _compile_analyzer(func)
    with activate_compiler(compiler), pytest.raises(
            BindingTableError, match="frame slot 'x'"):
        _lower_first_decl(stmt, lc.declaring("x").type,
                          _LowerScope(lc=lc, declared={}), None)


def test_a_borrow_tuple_alias_refers_unless_its_mixed_source_is_rebound(
        ) -> None:
    # `auto&` over a source holding an element inline, and over a mixed
    # render no write replaces; a reassigned mixed source binds `auto`.
    func, table = _plan('''
def split(p: P) -> tuple[P, Own[P]]:
    return (p, P(2))


def pick(a: P, b: P) -> int:
    t = (P(1), b)
    m = split(a)
    r = split(a)
    r = split(b)
    u = t
    v = m
    w = r
    return u[0].v + v[0].v + w[0].v
''', "pick")
    kinds = {r.name: _shape(r) for r in table.records()
             if isinstance(r.site, TpyVarDecl)}
    assert kinds["u"] == ("decl", F.BORROW_TUPLE, C.BTUPLE_REF_ALIAS)
    assert kinds["v"] == ("decl", F.BORROW_TUPLE, C.BTUPLE_REF_ALIAS)
    assert kinds["w"] == ("decl", F.BORROW_TUPLE, C.BTUPLE_ALIAS)


def test_an_open_t_call_local_carries_its_source_const() -> None:
    # `self.b.ga()` follows a receiver `touch` reads const (inferred
    # readonly), so the open-T local binds const; off a T-returning free
    # call it does not.
    source = '''
from tpy import Ptr, auto_readonly


class PBox[T]:
    p: Ptr[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.p = p

    @auto_readonly
    def ga(self) -> auto_readonly[T]:
        q = self.p
        return q


class PHold[T]:
    b: PBox[T]

    def __init__(self, p: Ptr[T]) -> None:
        self.b = PBox[T](p)

    def touch(self) -> int:
        x = self.b.ga()
        return 1


def ident[T](x: T) -> T:
    return x


def pick[T](y: T) -> None:
    b = ident(y)
'''
    _bodies, tables = lower_bodies(source)

    def decl(body: str, var: str) -> BindingRepr:
        return next(r for r in tables[body].records()
                    if r.name == var and isinstance(r.site, TpyVarDecl))
    rec = decl("PHold.touch", "x")
    assert _shape(rec) == ("decl", F.VALUE, C.TPARAM_CALL)
    assert rec.const
    rec = decl("pick", "b")
    assert _shape(rec) == ("decl", F.VALUE, C.TPARAM_CALL)
    assert not rec.const


def test_an_own_element_tuple_keeps_the_facts_its_row_decided(
        monkeypatch) -> None:
    # The Own-element overlay adds storage to whatever row decided the
    # declaration and keeps that row's kind and facts.
    source = '''
def alias(p: tuple[Own[P], Own[P]]) -> int:
    q = p
    q[0].v = 9
    return p[0].v
'''
    rec = _decl_rec(source, "alias", "q")
    assert _shape(rec) == ("decl", F.STORAGE_TUPLE, C.STORAGE_TUPLE_CHAIN)
    assert rec.storage_tuple
    # No program's row gives an Own-element tuple a layout, a const or a
    # pointer, so the row is forged to hold all three (a pointer slot's
    # const is then the fixpoint's, not the row's).
    layout = object()
    value_rows = _bindings._decl_value_rows

    def forged(site, name, typ, *args):
        rec = value_rows(site, name, typ, *args)
        return (replace(rec, tuple_layout=layout, const=True, pointer=True)
                if name == "q" else rec)
    monkeypatch.setattr(_bindings, "_decl_value_rows", forged)
    rec = _decl_rec(source, "alias", "q")
    assert _shape(rec) == ("decl", F.STORAGE_TUPLE, C.STORAGE_TUPLE_CHAIN)
    assert rec.storage_tuple
    assert rec.tuple_layout is layout and rec.pointer


def test_a_borrow_tuple_hoist_is_the_branch_or_the_loop_kind() -> None:
    # The branch-bound borrow tuple (if / try) spells its const from the
    # fixpoint and, off an owning call, is the pointer tuple whose owning
    # binding backs its elements at its own site; the loop variable's is the
    # plain borrow spelling; a with body's has no predecl.
    func, table = _plan('''
def make(v: int) -> tuple[Own[P], Own[P]]:
    return (P(v), P(v))


def pick(c: bool, a: P, b: P) -> int:
    if c:
        t = (a, b)
    else:
        t = make(1)
    for u in [(a, b), (b, a)]:
        pass
    try:
        w = (a, b)
    except ValueError:
        w = (b, a)
    with Ctx() as x:
        z = (a, b)
    return t[0].v + u[0].v + w[1].v + z[0].v
''', "pick")
    if_stmt, for_stmt, try_stmt, with_stmt, _ret = func.body
    t = table.declared_by(if_stmt, "t")
    assert _shape(t) == ("hoist", F.BORROW_TUPLE, None) and not t.rebind_slot
    assert t.ptr_tuple is not None
    assert all(not isinstance(et, OwnType) for et in t.ptr_tuple.element_types)
    # The statement keys the loop variable too.
    u, = (r for r in table.sites[for_stmt]
          if r.name == "u" and r.site_kind == "hoist")
    assert _shape(u) == ("hoist", F.BORROW_TUPLE, None) and not u.rebind_slot
    assert u.ptr_tuple is None
    w = table.declared_by(try_stmt, "w")
    assert _shape(w) == ("hoist", F.BORROW_TUPLE, None) and not w.rebind_slot
    assert w.ptr_tuple is None
    assert _shape(table.declared_by(with_stmt, "z")) == ("hoist", F.NONE, None)


def test_a_nested_match_pointer_hoist_off_const_subjects_is_const() -> None:
    # Neither subject is written, so every source of the `q` both matches
    # re-seat is const: one `const Inner*`, and `r = q` aliases const.
    func, table = _plan('''
def both_const(h: Outer, g: Outer) -> int:
    match h:
        case Outer(p=q):
            match g:
                case Outer(p=q):
                    pass
            r = q
            return r.v
    return -1
''', "both_const")
    q = table.declared_by(func.body[0], "q")
    assert _shape(q) == ("hoist", F.POINTER, None) and q.const and q.pointer
    # The borrow-only capture both matches assign by address.
    assert q.borrowed_capture


def test_a_nested_match_slot_off_a_const_and_a_mutable_subject_rejects() -> None:
    # `h` is written, `g` only read: the shared `q` holds a mutable and a
    # const source, rejected at the match binding the const one.
    with pytest.raises(ThirUnsupported) as exc:
        _plan('''
def mixed(h: Outer, g: Outer) -> int:
    match h:
        case Outer(p=q):
            h.p.v = 5
            match g:
                case Outer(p=q):
                    pass
            return q.v
    return -1
''', "mixed")
    assert exc.value.reason == "stmt.match:decl.reseat_source"
    assert exc.value.loc.line == _PRELUDE.count("\n") + 6


def test_a_chain_of_const_aliases_is_const_to_its_end() -> None:
    # Each alias is hoisted by its own `try`, so each one's const comes
    # from the one before: the fixpoint closes the chain whatever its length.
    func, table = _plan('''
def chain(t: tuple[P, int], other: P) -> int:
    a, n = t
    try:
        d1 = a
    finally:
        n += 1
    try:
        d2 = d1
    finally:
        n += 1
    try:
        d3 = d2
    finally:
        n += 1
    try:
        d4 = d3
    finally:
        n += 1
    try:
        d5 = d4
    finally:
        n += 1
    other.v += 10
    return d5.v + n
''', "chain")
    for i in range(1, 6):
        rec = next(r for r in _sites(table, f"d{i}") if r.site is not None)
        assert _shape(rec) == ("hoist", F.POINTER, None) and rec.const, rec
        assert rec.const and rec.const_source


def test_a_mixed_slot_rejects_at_its_const_source() -> None:
    # `y` holds a mutable borrow in one arm and a const one in the other:
    # its one pointer has no C++ form whether or not anything writes
    # through it, rejected where the const source binds.
    with pytest.raises(ThirUnsupported) as exc:
        _plan('''
from tpy import readonly


def pick(xs: readonly[list[P]], b: P, c: bool) -> int:
    r = xs[0]
    if c:
        y = b
    else:
        y = r
    return y.v
''', "pick")
    assert exc.value.reason == "stmt.var_decl:decl.reseat_source"
    # The prelude's lines come first; the const source is `y = r`.
    assert exc.value.loc.line == _PRELUDE.count("\n") + 10


def test_a_const_declaration_re_pointed_at_a_mutable_object_rejects() -> None:
    # The declaration is the slot's const source, so the reject sits on it.
    with pytest.raises(ThirUnsupported) as exc:
        _plan('''
from tpy import readonly


def repoint(p: readonly[P], b: P, c: bool) -> int:
    y = p
    if c:
        y = b
    return y.v
''', "repoint")
    assert exc.value.reason == "stmt.var_decl:decl.reseat_source"
    assert exc.value.loc.line == _PRELUDE.count("\n") + 6


def test_a_const_declaration_re_pointed_at_a_const_source_is_const() -> None:
    # The initializer is one source of the declaration's slot, judged like
    # the re-point: both const, so the slot is `const P*` (the row decides
    # no const of its own).
    func, table = _plan('''
from tpy import readonly


def repoint(p: readonly[P], q: readonly[P], c: bool) -> int:
    y = p
    if c:
        y = q
    return y.v
''', "repoint")
    y = table.declared_by(func.body[0], "y")
    assert y.pointer and y.const and y.const_source


def test_a_mixed_with_slot_rejects_at_its_const_source() -> None:
    # A `with` target off a readonly `__enter__` re-pointed at a mutable
    # object: one pointer slot holding both, rejected at the `with`.
    with pytest.raises(ThirUnsupported) as exc:
        _plan('''
from tpy import readonly


class CM:
    inner: P

    def __init__(self) -> None:
        self.inner = P(1)

    @readonly
    def __enter__(self) -> readonly[P]:
        return self.inner

    def __exit__(self, a, b, c) -> None:
        pass


def f(cm: CM, b: P, c: bool) -> int:
    with cm as v:
        if c:
            v = b
    return v.v
''', "f")
    assert exc.value.reason == "stmt.with:decl.reseat_source"
    # The prelude's lines come first; the const source is the `with`.
    assert exc.value.loc.line == _PRELUDE.count("\n") + 20


def test_a_mixed_union_pointer_slot_rejects_at_its_const_source() -> None:
    # A pointer-variant union local is a pointer slot: bound to a mutable
    # union and re-pointed at a readonly one, it rejects at the re-point.
    with pytest.raises(ThirUnsupported) as exc:
        _plan('''
from tpy import readonly


def g(y: P | Outer, x: readonly[P | Outer], c: bool) -> int:
    u = y
    if c:
        u = x
    return 1
''', "g")
    assert exc.value.reason == "stmt.var_decl:decl.reseat_source"
    # The prelude's lines come first; the const source is `u = x`.
    assert exc.value.loc.line == _PRELUDE.count("\n") + 8


def test_an_all_const_union_pointer_slot_is_const() -> None:
    # Re-pointed between two readonly unions: one const pointer union.
    func, table = _plan('''
from tpy import readonly


def g(y: readonly[P | Outer], x: readonly[P | Outer], c: bool) -> int:
    u = y
    if c:
        u = x
    return 1
''', "g")
    u = table.declared_by(func.body[0], "u")
    assert u.ptr_variant and u.const and u.const_source


def test_two_slots_of_one_name_are_judged_apart() -> None:
    # The block's `alias` and the one bound after it are two C++
    # variables: the first holds only a const borrow, the second only
    # mutable ones, so neither is mixed.
    func, table = _plan('''
from tpy import readonly


def two(p: readonly[P], q: P, c: bool) -> int:
    if c:
        alias = p
        n = alias.v
    else:
        n = 0
    alias = P(5)
    alias = q
    alias.v += 1
    return n + alias.v
''', "two")
    first = table.declared_by(func.body[0].then_body[0], "alias")
    assert first is not None and first.const and first.const_source
    later = table.declared_by(func.body[1], "alias")
    assert later is not None and not later.const and not later.const_source


def test_a_borrow_tuple_is_a_slot_const_when_every_source_is() -> None:
    # A borrow tuple's element pointers are one slot: the walrus binds only
    # a field of the unwritten `h`, so its pointers are const; the rebound
    # tuple's every binding marks its source's root written, so neither
    # source is const and the pointers are not.
    func, table = _plan('''
def walrus(h: Holder) -> int:
    n = 0
    if (t := h.pair)[0].v > 0:
        n = t[1].v
    return n
''', "walrus")
    (t,) = _sites(table, "t")
    assert t.form is F.BORROW_TUPLE and t.const and t.const_source
    func, table = _plan('''
def rebound(h: Holder, g: Holder, c: bool) -> int:
    t = h.pair
    if c:
        t = g.pair
    return t[0].v
''', "rebound")
    t = table.declared_by(func.body[0], "t")
    assert _shape(t) == ("decl", F.BORROW_TUPLE, C.BTUPLE_REBOUND)
    assert not t.const and not t.const_source


def test_two_records_of_a_borrow_tuple_name_keep_their_own_const() -> None:
    # The const is per record: the walrus's tuple holds only a const
    # source, the loop variable of the same name is a fresh C++ variable
    # bound per element, so it does not take the walrus's const.
    func, table = _plan('''
def two(h: Holder, ys: list[tuple[P, P]]) -> int:
    n = 0
    if (t := h.pair)[0].v > 0:
        n = t[1].v
    for t in ys:
        n += t[0].v
    return n
''', "two")
    walrus = next(r for r in _sites(table, "t") if r.site_kind == "walrus")
    loop = next(r for r in _sites(table, "t") if r.site_kind == "loop")
    assert walrus.form is F.BORROW_TUPLE and walrus.const
    assert not loop.const


def test_the_hoist_kinds_with_no_cpp_form() -> None:
    # No program reaches these: a const borrow rebound to rvalues is the
    # const pointer at an if (and a match) and no form elsewhere; a
    # move-through payload outside the owned-slot family has none; and a
    # resumable body's non-value hoist has none outside a loop head's plain
    # non-value (planned here without the frame, whose members take every
    # resumable local). No form is the one kind `hoist.plain`.
    src = '''
from typing import Iterator

from tpy import readonly


def pick(c: bool, o: readonly[Outer], m: P | None) -> int:
    try:
        x = o.p
    except ValueError:
        x = o.p
    if c:
        y = o.p
    else:
        y = o.p
    try:
        z = m
    except ValueError:
        z = m
    return x.v + y.v + (z.v if z is not None else 0)


def gen(c: bool, o: Outer, m: P | None) -> Iterator[int]:
    if c:
        a = o.p
    else:
        a = P(1)
    while True:
        b = P(2)
        d = m
        break
    yield a.v + b.v + (d.v if d is not None else 0)
'''
    compiler, func, inp = _plan_inputs(
        src, "pick", rvalue_reassigned=frozenset({"x", "y"}),
        move_through=frozenset({"z"}))
    with activate_compiler(compiler):
        table = plan_bindings(inp, func.body)
    kinds = {r.name: _shape(r) for r in table.records()}
    assert kinds["x"] == ("hoist", F.NONE, None)
    assert kinds["y"] == ("hoist", F.POINTER, None)
    assert kinds["z"] == ("hoist", F.NONE, None)
    compiler, func, inp = _plan_inputs(src, "gen")
    with activate_compiler(compiler):
        table = plan_bindings(inp, func.body)
    kinds = {r.name: _shape(r) for r in table.records()}
    assert kinds["a"] == ("hoist", F.NONE, None)
    assert kinds["b"] == ("hoist", F.OWNED_SLOT, None)
    assert kinds["d"] == ("hoist", F.NONE, None)


def _admit_tag(compiler, stmt, ladder: str, rec: BindingRepr) -> str | None:
    """The reject tag `_admit_hoist` records for `rec` at `stmt`."""
    with activate_compiler(compiler):
        begin_stmt()
        with pytest.raises(ThirUnsupported):
            _admit_hoist(_Hoist(stmt, ladder), rec.name, rec, None)
        return compiler._thir_reject_detail


def test_a_hoist_with_no_cpp_form_rejects_with_its_statement_tag() -> None:
    # A hoist with no C++ form is `hoist.plain` whatever the reason, and the
    # statement rejects by that kind alone, with its one tag. Planned
    # without the frame, whose members take every resumable local.
    src = '''
from typing import Iterator

from tpy import readonly


def pick(c: bool, o: readonly[Outer], m: P | None, k: int) -> int:
    try:
        x = o.p
    except ValueError:
        x = o.p
    if c:
        w = m
    else:
        w = m
    if c:
        g = 1
    else:
        g = 2
    while True:
        h = k
        break
    return x.v + g + h + (w.v if w is not None else 0)


def gen(c: bool, o: Outer, m: P | None) -> Iterator[int]:
    if c:
        a = o.p
    else:
        a = P(1)
    if c:
        d: Pet = Dog(1)
    else:
        d = Dog(2)
    if c:
        t = (o.p, o.p)
    else:
        t = (o.p, a)
    while True:
        e = m
        break
    for u in [(o.p, o.p)]:
        pass
    yield a.v + d.size() + t[0].v + (e.v if e is not None else 0) + u[0].v
'''
    compiler, func, inp = _plan_inputs(
        src, "pick", rvalue_reassigned=frozenset({"x"}),
        move_through=frozenset({"w"}), global_seeded=frozenset({"g", "h"}))
    # The names read as module globals own nothing, as sema says of a name
    # the body declares `global`.
    ctx = inp.analyzer.ctx
    body = ctx.body_ownership[func]
    ctx.body_ownership[func] = replace(body, locals={
        n: o for n, o in body.locals.items() if n not in ("g", "h")})
    with activate_compiler(compiler):
        table = plan_bindings(inp, func.body)
    try_x, if_w, if_g, while_h, _ret = func.body
    cases = [(try_x, "try", "x", ("hoist", F.NONE, None), "try.hoist"),
             (if_w, "if", "w", ("hoist", F.NONE, None), "if.hoist_type"),
             (if_g, "if", "g", ("hoist", F.NONE, None), "if.hoist_type"),
             (while_h, "while", "h", ("hoist", F.NONE, None),
              "while.hoist_type")]
    compiler_g, func_g, inp_g = _plan_inputs(src, "gen")
    with activate_compiler(compiler_g):
        table_g = plan_bindings(inp_g, func_g.body)
    if_a, if_d, if_t, while_e, for_u, _yield = func_g.body
    cases_g = [(if_a, "if", "a", ("hoist", F.NONE, None), "if.hoist_type"),
               (if_d, "if", "d", ("hoist", F.NONE, None), "if.hoist_type"),
               (if_t, "if", "t", ("hoist", F.NONE, None), "if.hoist_type"),
               (while_e, "while", "e", ("hoist", F.NONE, None),
                "while.hoist_type")]
    for stmt, ladder, name, kind, tag in cases:
        rec = table.declared_by(stmt, name)
        assert _shape(rec) == kind, name
        assert _admit_tag(compiler, stmt, ladder, rec) == tag, name
    for stmt, ladder, name, kind, tag in cases_g:
        rec = table_g.declared_by(stmt, name)
        assert _shape(rec) == kind, name
        assert _admit_tag(compiler_g, stmt, ladder, rec) == tag, name
    u, = (r for r in table_g.sites[for_u]
          if r.name == "u" and r.site_kind == "hoist")
    assert _shape(u) == ("hoist", F.NONE, None)
    assert _admit_tag(compiler_g, for_u, "foreach", u) == "foreach.hoist_type"


_LADDER = '''
from tpy import readonly


class Src:
    p: P

    def __init__(self) -> None:
        self.p = P(1)

    def view(self) -> readonly[P]:
        return self.p


def mk(n: int) -> Own[P]:
    return P(n)


def owned_then_lvalue(o: Outer, c: bool) -> int:
    if c:
        x = mk(1)
    else:
        x = o.p
    try:
        y = mk(1)
    except ValueError:
        y = o.p
    match o:
        case Outer(p=P(v=1)):
            z = mk(1)
        case _:
            z = o.p
    return x.v + y.v + z.v


def readonly_rebound(s: Src, o: Outer, c: bool) -> int:
    if c:
        x = s.view()
    else:
        x = s.view()
    match o:
        case Outer(p=P(v=1)):
            z = s.view()
        case _:
            z = s.view()
    return x.v + z.v


def in_branch(o: Outer, c: bool, d: bool) -> int:
    if c:
        if d:
            x = mk(1)
        else:
            x = mk(2)
        match o:
            case Outer(p=P(v=1)):
                z = mk(1)
            case _:
                z = mk(2)
        return x.v + z.v
    return 0


def const_capture(o: readonly[Outer], k: readonly[Outer], c: bool) -> int:
    if c:
        x = k.p
    else:
        x = o.p
    match o:
        case Outer(p=P(v=1)):
            z = k.p
        case Outer(p=z):
            pass
    return x.v + z.v


def owned_rebound(o: Outer, c: bool) -> int:
    if c:
        x = mk(1)
    else:
        x = mk(2)
    try:
        y = mk(1)
    except ValueError:
        y = mk(2)
    match o:
        case Outer(p=P(v=1)):
            z = mk(1)
        case _:
            z = mk(2)
    return x.v + y.v + z.v


def borrow_only(o: Outer, c: bool) -> int:
    if c:
        x = o.p
    else:
        x = o.p
    match o:
        case Outer(p=z):
            pass
    return x.v + z.v
'''


@pytest.mark.parametrize("func_name, facts, expected", [
    # Owned then re-pointed at an lvalue: the pointer local at an if, a try
    # and a match alike, rebound (the storage policy) -- the rebind slot at
    # an if and a match, none at a try.
    ("owned_then_lvalue", {}, {
        "x": (("hoist", F.POINTER, None), False, True, False),
        "y": (("hoist", F.POINTER, None), False, False, False),
        "z": (("hoist", F.POINTER, None), False, True, False)}),
    # A readonly type: the const pointer at an if and a match.
    ("readonly_rebound", {}, {
        "x": (("hoist", F.POINTER, None), True, False, False),
        "z": (("hoist", F.POINTER, None), True, False, False)}),
    # Inside a branch: the rebind-slot pointer at both.
    ("in_branch", {}, {
        "x": (("hoist", F.POINTER, None), False, True, False),
        "z": (("hoist", F.POINTER, None), False, True, False)}),
    # A const borrow: the const pointer at both; the match's is also the
    # capture it assigns by address.
    ("const_capture", {}, {
        "x": (("hoist", F.POINTER, None), True, False, False),
        "z": (("hoist", F.POINTER, None), True, False, True)}),
    # Rvalue-rebound: the rebind slot at an if and a match, none at a try.
    ("owned_rebound", {}, {
        "x": (("hoist", F.POINTER, None), False, True, False),
        "y": (("hoist", F.POINTER, None), False, False, False),
        "z": (("hoist", F.POINTER, None), False, True, False)}),
    # Borrow-only: the pointer local at both; the match's is the capture a
    # nested match re-seats by address. The if's, bound in both arms, is
    # rebound: its rebind slot.
    ("borrow_only", {}, {
        "x": (("hoist", F.POINTER, None), False, True, False),
        "z": (("hoist", F.POINTER, None), False, False, True)}),
    # Borrow-only and rebound (no program found; the fact is given): the
    # rebind slot at both.
    ("borrow_only", {"reassigned": frozenset({"x", "z"})}, {
        "x": (("hoist", F.POINTER, None), False, True, False),
        "z": (("hoist", F.POINTER, None), False, True, True)}),
])
def test_the_one_hoist_ladder_answers_per_statement(func_name, facts,
                                                      expected) -> None:
    compiler, func, inp = _plan_inputs(_LADDER, func_name, **facts)
    with activate_compiler(compiler):
        table = plan_bindings(inp, func.body)
    got = {r.name: (_shape(r), r.const, r.rebind_slot, r.borrowed_capture)
           for r in table.records()
           if r.site_kind == "hoist" and r.name in expected}
    assert got == expected


_STATEMENT_BINDS = '''
def unpacks(t: tuple[P, int], u: tuple[P | None, int],
            w: tuple[int, int]) -> int:
    a, n = t
    b, m = u
    c, d = w
    return a.v + n + m + c + d + (b.v if b is not None else 0)


def heads(ts: list[tuple[P | None, int]], ps: list[tuple[int, int]],
          qs: list[tuple[P, int]]) -> int:
    s: int = 0
    for b, m in ts:
        s += m + (b.v if b is not None else 0)
    for s, y in ps:
        s += y
    for q, j in qs:
        s += q.v + j
    return s


class Mgr:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v

    def __enter__(self) -> "Mgr":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


def withs(o: Outer) -> int:
    with Mgr(1) as p:
        pass
    with Mgr(2) as r:
        pass
    with Mgr(3) as r:
        pass
    with Ctx() as c:
        pass
    return p.v + r.v + c.v


def captures(o: Outer, h: Outer) -> int:
    h.p.v = 1
    match o:
        case Outer(p=q):
            return q.v
    match h:
        case Outer(p=z):
            return z.v
    return 0


def comps(pairs: list[tuple[P, int]], opts: list[tuple[P | None, int]]
          ) -> int:
    xs = [a.v + n for a, n in pairs]
    ys = [k for b, k in opts if b is not None]
    return len(xs) + len(ys)
'''


@pytest.mark.parametrize("func_name, expected", [
    # A record target aliases its element, const off an unmutated tuple;
    # an Optional one is the element pointer, const the same way; value
    # elements copy (an expensive one is a `const T&`).
    ("unpacks", {"a": (("unpack", F.REFERENCE, C.REF), True),
                 "n": (("unpack", F.REFERENCE, C.CREF), False),
                 "b": (("unpack", F.POINTER, C.OPT_PTR), True),
                 "c": (("unpack", F.REFERENCE, C.CREF), False)}),
    # A for head's Optional target is const as the iteration source; a
    # head target naming a visible local writes it (no record of its own).
    ("heads", {"b": (("unpack", F.POINTER, C.OPT_PTR), True),
               "y": (("unpack", F.REFERENCE, C.CREF), False),
               "q": (("unpack", F.REFERENCE, C.REF), True)}),
    # A record bound once is the reference, a reassigned one the pointer
    # the second `with` re-points; an owned enter result has no form here
    # (no program in the corpus reaches it).
    ("withs", {"p": (("with", F.REFERENCE, None), False),
               "r": (("with", F.POINTER, None), False),
               "c": (("with", F.NONE, None), False)}),
    # A capture aliases the field it matched, const off an unmutated
    # subject only.
    ("captures", {"q": (("match", F.REFERENCE, None), True),
                  "z": (("match", F.REFERENCE, None), False)}),
    # A record or Optional element of a comprehension's unpack is a
    # reference, const as the clause's loop variable; a value its copy.
    ("comps", {"a": (("comp", F.REFERENCE, None), True),
               "n": (("comp", F.VALUE, None), False),
               "b": (("comp", F.STORAGE_OPT, None), True)}),
])
def test_each_statement_binding_is_the_kind_its_site_declares(
        func_name, expected) -> None:
    func, table = _plan(_STATEMENT_BINDS, func_name)
    got = {r.name: (_shape(r), r.const) for r in table.records()
           if r.name in expected and r.site is not None}
    assert got == expected
    if func_name == "heads":
        # `s` is the function's local the second head assigns.
        assert [_shape(r) for r in _sites(table, "s")] == [
            ("decl", F.NONE, None)]


_KIND_BINDS = '''
from typing import Iterator, Optional

from tpy import int32

type Tree[T] = T | list[Tree[T]]


def mkp(n: int) -> Own[P]:
    return P(n)


def opt(o: Outer, c: bool) -> P | None:
    return o.p if c else None


def oint(n: int) -> Optional[int]:
    return n if n > 0 else None


def make_pair(o: Outer) -> tuple[P, P]:
    return (o.p, o.p)


def tree_pair(seed: Tree[int32]) -> tuple[Tree[int32], int32]:
    return (seed, 3)


def walruses(o: Outer, h: Holder, c: bool, ps: list[tuple[P, P]],
             xs: list[int]) -> int:
    s = 0
    if (a := o.p).v > 0:
        s += a.v
    if (b := opt(o, c)) is not None:
        s += b.v
    if (t := ps[0])[0].v > 0:
        s += t[1].v
    if (f := mkp(1)).v > 0:
        s += f.v
    if (v := oint(2)) is not None:
        s += v
    if (k := len(xs)) > 0:
        s += k
    if (u := make_pair(o))[0].v > 0:
        s += u[0].v
    u = h.pair
    return s


def gen_walrus(o: Outer, c: bool) -> Iterator[int]:
    if (g := opt(o, c)) is not None:
        yield g.v


class Mgr:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v

    def __enter__(self) -> int:
        return self.v

    def __exit__(self, et, ev, tb) -> None:
        pass


def binds(t: tuple[Own[P], int], u: tuple[P, int], w: tuple[Own[P | Q], int],
          m: Mgr, o: P | None, h: Outer, n: int, s: Tree[int32]) -> int:
    a, i = t
    b, k = u
    b = h.p
    c, j = w
    d, r = divmod(n, 3)
    d += 1
    e, l = tree_pair(s)
    with m as x:
        pass
    return i + k + j + x + d + r + l


def take(p: P | None) -> int:
    return p.v if p is not None else 0


def captures(o: P | None, n: int32) -> int:
    match o:
        case P(v=1):
            return 1
        case z:
            return take(z)
    match n:
        case y if y > 0:
            return y
    return 0
'''


@pytest.mark.parametrize("func_name, expected", [
    # A walrus's first binding: the borrow-only record's pointer, the
    # pointer-repr Optional's, the borrow tuple (const: its one source is
    # an element of the unwritten `ps`),
    # the reassigned borrow tuple over its owning slot, the owned record's
    # deferred-init slot, the value-repr Optional, the plain value.
    ("walruses", {"a": (("walrus", F.POINTER, C.POINTER), False),
                  "b": (("walrus", F.POINTER, C.OPT_PTR), False),
                  "t": (("walrus", F.BORROW_TUPLE, None), True),
                  "u": (("walrus", F.BORROW_TUPLE, C.BTUPLE_REBOUND), False),
                  "f": (("walrus", F.OWNED_SLOT, None), False),
                  "v": (("walrus", F.VALUE_OPT, None), False),
                  "k": (("walrus", F.VALUE, None), False)}),
    # A resumable body's walrus of a non-scalar has no form.
    ("gen_walrus", {"g": (("walrus", F.NONE, None), False)}),
    # Unpack targets: an owned element moved out, a rebound record's
    # pointer, an `Own[A | B]` element's pointer variant, a written value
    # copied, a recursive wrapper's live reference; a `with` over a value
    # enter result copies.
    ("binds", {"a": (("unpack", F.VALUE, C.MOVE), False),
               "b": (("unpack", F.POINTER, C.PTR), False),
               "c": (("unpack", F.POINTER_UNION, C.PTR_VARIANT), False),
               "d": (("unpack", F.VALUE, C.VALUE), False),
               "e": (("unpack", F.REFERENCE, C.UNWRAP_REF), False),
               "x": (("with", F.VALUE, None), False)}),
    # A match capture of a whole pointer subject copies the pointer (const
    # as the subject), a guarded scalar capture its value.
    ("captures", {"z": (("match", F.POINTER, None), True),
                  "y": (("match", F.VALUE, None), False)}),
])
def test_each_binding_kind_is_the_form_its_site_builds(func_name,
                                                       expected) -> None:
    func, table = _plan(_REJECT_KINDS + _KIND_BINDS, func_name)
    got = {r.name: (_shape(r), r.const) for r in table.records()
           if r.name in expected and r.site is not None}
    assert got == expected


def test_a_frame_nested_def_is_bound_at_the_frame_root() -> None:
    # A resumable frame's nested def is a member of the frame, callable from
    # every resume case: its name is bound at the body's root, not at its
    # statement.
    _bodies, tables = lower_bodies(_PRELUDE + '''
from typing import Iterator


def gen(n: int) -> Iterator[int]:
    yield 0

    def helper(k: int) -> int:
        return k + n
    yield helper(1)
''')
    rec = tables["gen"].root.bindings["helper"]
    assert _shape(rec) == ("def", F.VALUE, None) and rec.nested_def


def _module_tables(source: str, extra_lib_dirs=None
                   ) -> dict[str, BindingTable]:
    """Every body's table as the whole module's C++ generation plans it,
    the module-init body (`__tpy_init`) included, keyed by the body's
    function name (the first planning of each)."""
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    tables: dict[str, BindingTable] = {}
    install = _bindings.install

    def capture(lc: _LowerCtx, table: BindingTable) -> None:
        tables.setdefault(lc.func.name, table)
        install(lc, table)
    _bindings.install = capture
    try:
        with activate_compiler(compiler):
            compiler.generate_inl_and_code_to_strings(entry)
    finally:
        _bindings.install = install
    return tables


_SITE_KINDS = _PRELUDE + '''
from typing import Callable, Iterator

from tpy import int32

G = P(1)


class C:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def get(self, k: int32) -> int32:
        return self.n + k


class Num:
    def __enter__(self) -> int32:
        return 3

    def __exit__(self, et, ev, tb) -> None:
        pass


def reads(p: P, xs: list[int32], pairs: list[tuple[int32, int32]], c: bool,
          o: Outer | P) -> int32:
    d: int32 = 1
    if c:
        h = d
    else:
        h = d + 1
    for x in xs:
        d += x
    for a, b in pairs:
        d += a + b
    with Num() as w:
        d += w
    if (k := d + 1) > 0:
        d += k
    ys = [y + 1 for y in xs]
    match o:
        case Outer(p=q):
            d += 1
        case _:
            pass
    try:
        d += 1
    except ValueError as e:
        d += 2

    return d + h + len(ys) + int32(p.v + G.v)


def closures(d: int32) -> int32:
    def inner(m: int32) -> int32:
        return m + d
    f: Callable[[int32], int32] = lambda z: z + d
    return inner(1) + f(2)


def gen(n: int32) -> Iterator[int32]:
    t = n + 1
    yield t
'''


def test_every_record_names_the_kind_of_site_that_declared_it() -> None:
    tables = _module_tables(_SITE_KINDS)

    def kinds(table: BindingTable, *names: str) -> dict:
        got: dict = {}
        for rec in [*table.root.bindings.values(), *table.records()]:
            if rec.name in names:
                got.setdefault(rec.name, set()).add(rec.site_kind)
        for node in table.scope_at.values():
            for rec in node.bindings.values():
                if rec.name in names:
                    got.setdefault(rec.name, set()).add(rec.site_kind)
        return got

    assert kinds(tables["reads"], "p", "G", "d", "h", "x", "a", "w", "k",
                 "y", "q", "e") == {
        "p": {"param"}, "G": {"global"}, "d": {"decl"}, "h": {"hoist"},
        "x": {"loop"}, "a": {"unpack"}, "w": {"with"}, "k": {"walrus"},
        "y": {"comp"}, "q": {"match"}, "e": {"except"}}
    assert kinds(tables["closures"], "inner", "m", "z") == {
        "inner": {"def"}, "m": {"nested_param"}, "z": {"lambda"}}
    assert kinds(tables["get"], "self") == {"self": {"receiver"}}
    assert kinds(tables["gen"], "n", "t") == {"n": {"param"},
                                               "t": {"frame"}}
    assert kinds(tables["__tpy_init"], "G") == {"G": {"module_global"}}


_LOOP_BINDS = _PRELUDE + '''
from enum import Enum

from tpy import int32, readonly


class Color(Enum):
    RED = 1
    GREEN = 2


def loops(ps: list[P], xs: list[int32], ro: readonly[list[int32]],
          pairs: list[tuple[int32, int32]]) -> int32:
    n: int32 = 0
    for p in ps:
        p.v += 1
    for q in ps:
        n += int32(q.v)
    for x in xs:
        n += x
    for r in ro:
        n += r
    for c in Color:
        n += 1
    h: int32 = 0
    for h in xs:
        pass
    zs = [z + 1 for z in xs]
    ws = [a + b for a, b in pairs]
    return n + h + len(zs) + len(ws)
'''


def test_a_loop_record_carries_the_binding_its_loop_emits() -> None:
    func, table = _plan(_LOOP_BINDS, "loops")
    LB = LoopBinding

    def loop_rec(name: str) -> BindingRepr:
        recs = [r for r in _sites(table, name) if r.loop_binding is not None]
        assert len(recs) == 1, (name, recs)
        return recs[0]
    got = {n: (loop_rec(n).loop_binding, loop_rec(n).const_loop_var)
           for n in ("p", "q", "x", "r", "c", "h", "z")}
    assert got == {
        # A record element a body writes through is the mutable reference,
        # one it only reads the const one.
        "p": (LB.REFERENCE, False), "q": (LB.CONST_REFERENCE, True),
        # A value element is the loop's own copy, never const, whatever
        # its source (a readonly container included).
        "x": (LB.VALUE, False), "r": (LB.VALUE, False),
        # An enum loop binds the member by value.
        "c": (LB.VALUE, False),
        # A loop variable read after the loop assigns the hoisted slot.
        "h": (LB.ASSIGN, False),
        # A comprehension clause binds its variable as the for statement
        # binds an element of its type.
        "z": (LB.VALUE, False)}
    # An unpack clause's holder carries the clause's binding; its targets
    # are no loop variables.
    holder = [r for r in table.records()
              if r.site_kind == "comp" and r.name not in ("z", "a", "b")]
    assert [r.loop_binding for r in holder] == [LB.REFERENCE]
    assert all(r.loop_binding is None for n in ("a", "b")
               for r in _sites(table, n))


_ARRAY_COMP = _PRELUDE + '''
from tpy import Array


def bump(a: Array[int, 3]) -> int:
    a[0] = 100
    return 0


def over(a: Array[int, 3], xs: list[int]) -> int:
    m: Array[int, 3] = [y * 2 for y in a]
    ws = [w * 2 for w in xs]
    vs: Array[int, 3] = [bump(a) + v for v in a]
    return m[0] + ws[0] + vs[0]
'''


def test_a_comprehension_variable_copies_only_under_a_source_write() -> None:
    # A read-only clause's BigInt variable is a const reference into the
    # source, whatever the result; one whose element may write the source
    # (`copies_var`) is its own copy.
    _func, table = _plan(_ARRAY_COMP, "over")
    kind = {n: next(r for r in _sites(table, n)
                    if r.loop_binding is not None).loop_binding
            for n in ("y", "w", "v")}
    assert kind == {"y": LoopBinding.CONST_TYPED_REFERENCE,
                    "w": LoopBinding.CONST_TYPED_REFERENCE,
                    "v": LoopBinding.VALUE}


_PARAM_CONST = _PRELUDE + '''
from typing import Callable

from tpy import Ptr, readonly


class Q:
    w: int

    def __init__(self, w: int) -> None:
        self.w = w


class R:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def get(self) -> int:
        return self.n

    def bump(self) -> None:
        self.n += 1

    def total(self) -> int:
        def inner() -> int:
            return self.n
        return inner()


def params(p: P, m: P, u: P | Q, rp: Ptr[readonly[P]],
           *rest: P) -> int:
    m.v += 1

    def inner() -> int:
        return p.v
    f: Callable[[], int] = lambda: p.v
    vs = [p.v + q.v for q in rest]
    return (inner() + rp.v + (1 if isinstance(u, P) else 0) + len(rest)
            + f() + len(vs))
'''


def test_a_parameter_record_carries_its_function_const_verdicts() -> None:
    _bodies, tables = lower_bodies(_PARAM_CONST)
    table = tables["params"]
    root = table.root.bindings
    got = {n: (root[n].const, root[n].deep_const)
           for n in ("p", "m", "u", "rp", "rest")}
    assert got == {
        # Read only: const; written through: mutable.
        "p": (True, True), "m": (False, False),
        # A union read only for its discriminant deep-consts its pointees.
        "u": (True, True),
        # A `Ptr[readonly[T]]` carries its const on the pointee, an
        # unmutated pack on its elements.
        "rp": (False, True), "rest": (False, True)}
    # A nested def reads the enclosing parameter's own record, so the
    # verdict of the function that declares it.
    nested = next(s for s in table.scope_at
                  if isinstance(s, TpyFunction) and s.name == "inner")
    assert table.scope_at[nested].lookup("p") is root["p"]
    # So do a lambda and a comprehension.
    lam = next(s for s in table.scope_at if isinstance(s, TpyLambda))
    assert table.scope_at[lam].lookup("p") is root["p"]
    comp = next(s for s in table.scope_at
                if isinstance(s, TpyListComprehension))
    assert table.scope_at[comp].lookup("p") is root["p"]
    # The receiver of a read-only method is const, a mutating one's not.
    assert tables["R.get"].root.bindings["self"].const is True
    assert tables["R.bump"].root.bindings["self"].const is False
    # A nested def inside a read-only method reads the method's receiver
    # record, const.
    total = tables["R.total"]
    nested_self = next(
        total.scope_at[s] for s in total.scope_at
        if isinstance(s, TpyFunction) and s.name == "inner").lookup("self")
    assert nested_self is total.root.bindings["self"]
    assert nested_self.const is True


_GLOBAL_SLOTS = _PRELUDE + '''
G = P(1)
K = 3


def reads() -> int:
    def inner() -> int:
        return G.v
    return G.v + K + inner()
'''


def test_a_pointer_slot_global_record_is_the_global_slot() -> None:
    tables = _module_tables(_GLOBAL_SLOTS)
    # Module init and a function body both read a non-value global through
    # its pointer slot; a value global is no slot.
    init = tables["__tpy_init"].root.bindings
    assert (init["G"].site_kind, init["G"].global_slot,
            init["G"].pointer) == ("module_global", True, True)
    body = tables["reads"]
    g = body.root.bindings["G"]
    assert (g.site_kind, g.global_slot, g.pointer) == ("global", True, True)
    assert not body.root.bindings["K"].global_slot
    # A nested def reads the enclosing body's slot as the pointer it is.
    nested = next(s for s in body.scope_at
                  if isinstance(s, TpyFunction) and s.name == "inner")
    inner_g = body.scope_at[nested].lookup("G")
    assert inner_g.pointer and not inner_g.global_slot
    # Only module init's own slot global is its module slot.
    assert init["G"].module_slot
    assert not g.module_slot and not inner_g.module_slot


_IMPORTED_SLOT = '''
from gslots import H

print(H.w)
'''


def test_an_imported_slot_global_is_no_module_slot(tmp_path) -> None:
    (tmp_path / "gslots.py").write_text(
        "class Q:\n"
        "    w: int\n\n"
        "    def __init__(self, w: int) -> None:\n"
        "        self.w = w\n\n\n"
        "H = Q(1)\n")
    init = _module_tables(_IMPORTED_SLOT, extra_lib_dirs=[tmp_path])[
        "__tpy_init"].root.bindings
    # The importing module's init reads the slot as a pointer through its
    # qualified spelling and never allocates it.
    h = init["H"]
    assert (h.site_kind, h.global_slot, h.pointer, h.module_slot) == (
        "module_global", True, True, False)


_FRAME_FACTS = _PRELUDE + '''
from typing import Iterator


class Bag:
    items: list[P]

    def __init__(self) -> None:
        self.items = [P(1)]

    def walk(self, k: int) -> Iterator[int]:
        for it in self.items:
            yield it.v + k


def gen(items: list[P], n: int) -> Iterator[int]:
    for it in items:
        it.v += 1
        yield it.v
    t = n + 1
    yield t


def steps(ps: list[P], os: list[P | None]) -> Iterator[int]:
    for st in iter(ps):
        yield st.v + 0
    for o in os:
        if o is not None:
            yield o.v + 0


def shadow(rowss: list[list[int]]) -> Iterator[int]:
    ys = rowss[0]
    yield len(ys)
    t = [len(ys) for ys in rowss]
    yield sum(t)


def plain(n: int) -> int:
    return n + 1
'''


def test_a_frame_member_record_carries_its_frame_class() -> None:
    _bodies, tables = lower_bodies(_FRAME_FACTS)
    C = FrameLocalClass
    root = tables["gen"].root.bindings
    # A frame's parameters are its plain members; a pointer-form loop
    # variable is the loop's `T*` member, bound by its loop's strategy;
    # a value local is a plain member.
    assert root["items"].frame_class is C.PLAIN
    assert root["n"].frame_class is C.PLAIN
    assert root["it"].frame_class is C.LOOP_PTR
    assert root["t"].frame_class is C.PLAIN
    # A pointer-form loop variable over an ITERATOR source points into the
    # step result; over a container, into its storage. A pointer-repr
    # Optional loop variable is the loop's.
    steps = tables["steps"].root.bindings
    assert steps["st"].frame_class is C.LOOP_PTR
    assert steps["st"].source_typed_holder
    assert not root["it"].source_typed_holder
    assert steps["o"].frame_class is C.OPT_PTR and steps["o"].opt_ptr_loop_var
    assert not any(r.opt_ptr_loop_var
                   for r in (root["it"], root["t"], steps["st"]))
    # A read-only method's frame holds its receiver and the loop variable
    # over its field const.
    walk = tables["Bag.walk"].root.bindings
    assert walk["self"].frame_const
    assert walk["it"].frame_const and walk["it"].frame_class is C.LOOP_PTR
    assert not root["it"].frame_const
    # A sync body has no frame members.
    assert all(r.frame_class is None
               for r in tables["plain"].root.bindings.values())


def test_a_nested_scope_rebinding_a_frame_member_hides_it(
        monkeypatch) -> None:
    lcs: list = []
    install = _bindings.install

    def capture(lc: _LowerCtx, table: BindingTable) -> None:
        lcs.append(lc)
        install(lc, table)
    monkeypatch.setattr(_bindings, "install", capture)
    lower_bodies(_FRAME_FACTS)
    lc = next(lc for lc in lcs
              if lc.func.name == "shadow" and lc.table.frame_members)
    table = lc.table
    lc.cursor = (None, table.root)
    assert {"ys", "t"} <= lc.frame_field_names()
    # Inside the comprehension `ys` is the clause's variable, no member.
    comp = next(s for s in table.scope_at
                if isinstance(s, TpyListComprehension))
    lc.cursor = (None, table.scope_at[comp])
    names = lc.frame_field_names()
    assert "ys" not in names and "t" in names


def test_a_seeded_record_takes_its_form_from_its_facts() -> None:
    # A parameter's and a seeded global's row decides facts, not one
    # representation: the form reads them, the owned storage optional of
    # an `Own[P | None]` parameter (also a pointer) first.
    seeded = _bindings._seeded_form
    assert seeded({"optional_storage": True, "pointer": True}) is F.OWNED_SLOT
    assert seeded({"pointer": True}) is F.POINTER
    assert seeded({"ptr_variant": True}) is F.POINTER_UNION
    assert seeded({"value_opt": ValueOptKind.SCALAR}) is F.VALUE_OPT
    assert seeded({"storage_tuple": True, "movable": True}) is F.STORAGE_TUPLE
    assert seeded({"borrow_tuple_mixed": True}) is F.BORROW_TUPLE
    assert seeded({"tuple_layout": object()}) is F.BORROW_TUPLE
    assert seeded({"const": True, "movable": True}) is F.NONE


def test_a_parameter_record_has_the_form_its_signature_passes() -> None:
    func, table = _plan('''
def take(o: P | None, p: P, n: int) -> int:
    return n
''', "take")
    root = table.root.bindings
    assert _shape(root["o"]) == ("param", F.POINTER, None)
    assert _shape(root["p"]) == ("param", F.NONE, None)
    assert _shape(root["n"]) == ("param", F.NONE, None)


def test_a_frame_field_takes_its_form_from_its_class() -> None:
    form = _bindings._frame_form
    pointers = {FrameLocalClass.LOOP_PTR, FrameLocalClass.UNPACK_PTR,
                FrameLocalClass.ALIAS_PTR, FrameLocalClass.OPT_PTR,
                FrameLocalClass.REBIND_PTR}
    for cls in FrameLocalClass:
        want = (F.POINTER if cls in pointers
                else F.FRAME_SLOT if cls.is_frame_slot
                else F.VALUE_OPT if cls is FrameLocalClass.VALUE_OPT_RECORD
                else F.BORROW_TUPLE if cls is FrameLocalClass.BORROW_TUPLE
                else F.DYN if cls is FrameLocalClass.ERASED_HANDLE
                else F.VALUE)
        assert form(cls) is want, cls
    assert form(None) is F.VALUE


def test_a_loop_record_takes_its_form_from_its_loop_binding() -> None:
    LB = LoopBinding
    form = _bindings._loop_form
    for lb in (LB.REFERENCE, LB.CONST_REFERENCE, LB.CONST_TYPED_REFERENCE,
               LB.OWNED):
        assert form(lb) is F.REFERENCE, lb
    for lb in (LB.VALUE, LB.ASSIGN, LB.OWNED_STORAGE):
        assert form(lb) is F.VALUE, lb
    assert form(None) is F.NONE
    func, table = _plan(_LOOP_BINDS, "loops")
    forms = {r.name: r.form for r in table.records()
             if r.site_kind == "loop"}
    assert forms["p"] is F.REFERENCE and forms["c"] is F.VALUE
    assert forms["h"] is F.VALUE
    # An unpack clause's holder, as a for statement's.
    holder, = (r for r in table.records()
               if r.site_kind == "comp" and r.name not in ("z", "a", "b"))
    assert holder.form is F.REFERENCE


def test_a_site_with_no_form_gets_none_whatever_the_site() -> None:
    func, table = _plan('''
def pick(c: bool, xs: list[int]) -> int:
    n = 0
    s = 1
    try:
        n = xs[0]
    except IndexError as e:
        n = -1
    return n + s
''', "pick")
    for r in table.records():
        if r.name in ("s", "e"):
            assert r.form is F.NONE and r.construct is None, r


def test_a_for_head_null_pointer_hoist_is_a_pointer_the_head_repoints(
        ) -> None:
    func, table = _plan('''
def scan() -> int:
    pairs: list[tuple[int, list[int]]] = [(1, [10]), (2, [20])]
    for n, xs in pairs:
        pass
    xs.append(30)
    return n
''', "scan")
    loop = func.body[1]
    xs, = (r for r in table.sites[loop]
           if r.name == "xs" and r.site_kind == "hoist")
    # The hoist the head re-points is a pointer like any pointer hoist; a
    # bit says the head writes it.
    assert _shape(xs) == ("hoist", F.POINTER, None)
    assert xs.head_target and xs.pointer and xs.lazy_rebind_slot
    assert all(not r.head_target for r in table.records() if r is not xs)


_ER_BIND = '''
from tpy import error_return, ReturnException


class Fail(Exception, ReturnException):
    pass


@error_return(Fail)
def make(v: int) -> Own[P]:
    if v < 0:
        raise Fail
    return P(v)


@error_return(Fail)
def count(v: int) -> int:
    if v < 0:
        raise Fail
    return v


@error_return(Fail)
def body() -> int:
    p = make(15)
    alias = p
    p = make(50)
    n = count(2)
    return alias.v + p.v + n
'''


def test_an_error_return_bind_is_a_pointer_or_a_value_by_its_facts(
        ) -> None:
    # The one bind row gives a rebound record result a pointer and any
    # other result the value it unwraps.
    func, table = _plan(_ER_BIND, "body")
    p = table.declared_by(func.body[0], "p")
    n = table.declared_by(func.body[3], "n")
    assert _shape(p) == ("decl", F.POINTER, C.ER_BIND) and p.pointer
    assert _shape(n) == ("decl", F.VALUE, C.ER_BIND) and not n.pointer


def test_a_widened_tuple_literal_is_storage_or_value_by_its_elements(
        ) -> None:
    func, table = _plan('''
def pick() -> int:
    t: tuple[int | str, int | str] = (1, "hi")
    return 0
''', "pick")
    t = table.declared_by(func.body[0], "t")
    assert _shape(t) == ("decl", F.VALUE, C.TUPLE_LITERAL_WIDE)
    assert not t.storage_tuple


# -- movable: the binding's ownership less the scoped rows -----------------------

def _rec(table, name: str, kind: str):
    """The one record of `name` a site of `kind` declares."""
    found = [r for r in table.records()
             if r.name == name and r.site_kind == kind]
    assert len(found) == 1, (name, kind, found)
    return found[0]


_SCOPED = '''
from typing import Iterable
def pair() -> tuple[str, int]:
    return ("second", 1)
def value_param(p: tuple[Own[int], int]) -> int:
    return p[0]
def copied_head(a: list[tuple[int, str]]) -> None:
    for x, s in a:
        a[0] = (50, "changed")
        print(x, s)
def via_closure(source: Iterable[Own[list[int]]]) -> int:
    out: list[list[int]] = []
    def inner() -> None:
        for x in source:
            out.append(x)
    inner()
    return len(out[0])
def nested_unpack() -> str:
    def inner() -> str:
        s = "first"
        s, n = pair()
        return s
    return inner()
def hoisted_unpack(c: bool) -> str:
    try:
        s = "first"
    except ValueError:
        s = "fallback"
    s, n = pair()
    return s
def promoted() -> str:
    s = "first"
    s, n = pair()
    return s
def mixed_name(o: Outer, borrowed: P) -> None:
    xs: list[P] = [P(1)]
    ys: list[P] = []
    for x in xs:
        if x.v > 0:
            ys.append(x)
        else:
            o.p = x
    x = borrowed
    print(x.v)
'''


def test_a_value_tuple_ownership_transfer_parameter_moves() -> None:
    # No value exception on a parameter row: the transfer moves.
    func, table = _plan(_SCOPED, "value_param")
    assert table.root.lookup("p").movable


def test_copied_loop_head_targets_move() -> None:
    func, table = _plan(_SCOPED, "copied_head")
    for n in ("x", "s"):
        rec = _rec(table, n, "unpack")
        # A `str` copy allocates: its last use in the pass moves it; the
        # `int` copy moves too (a BigInt).
        assert rec.movable, n


def test_a_nested_defs_owned_loop_variable_moves() -> None:
    # The nested-def exception covers the rows its body's locals take,
    # never a loop variable, whose ownership is the loop's own.
    func, table = _plan(_SCOPED, "via_closure")
    rec = _rec(table, "x", "loop")
    assert rec.movable and rec.move_exception is None


def test_an_unpack_promotes_only_an_eligible_local() -> None:
    func, table = _plan(_SCOPED, "promoted")
    promoted = [r for r in table.records()
                if R.UNPACK_PROMOTE in r.refinements]
    assert [(r.name, r.movable, r.move_forced) for r in promoted] == [
        ("s", True, M.UNPACK_PROMOTE)]
    # A nested def's local and a hoisted one are never promoted.
    for name in ("nested_unpack", "hoisted_unpack"):
        func, table = _plan(_SCOPED, name)
        recs = [r for r in table.records() if r.name == "s"]
        recs += [r for node in table.scope_at.values()
                 for r in node.bindings.values() if r.name == "s"]
        assert not any(R.UNPACK_PROMOTE in r.refinements for r in recs), name
        assert not any(r.movable for r in recs), name


def test_a_str_dict_key_loop_variable_moves() -> None:
    # The case's first `for k in d:` binds `k` as an owned `str`: its
    # record moves.
    source = (Path(__file__).resolve().parents[2] / "tests/cases/calls"
              / "ref_generic_tuple_return/src/main.py").read_text()
    func, table = _plan(source, "main", prelude="")
    loops = {r.site.loc.line: r for r in table.records()
             if r.name == "k" and r.site_kind == "loop"}
    assert str(loops[24].type) == "str" and loops[24].movable


def test_a_loop_site_moves_although_a_later_local_borrows() -> None:
    func, table = _plan(_SCOPED, "mixed_name")
    assert _rec(table, "x", "loop").movable
    later = _rec(table, "x", "decl")
    assert not later.movable


_ROWS_PROGRAM = '''
from typing import Callable, Iterator
from tpy import error_return, ReturnException
class E(Exception, ReturnException):
    pass
class S:
    p: P
    o: P | None
    u: P | Outer
    def __init__(self) -> None:
        self.p = P(0)
        self.o = None
        self.u = P(0)
def make_opt(k: int) -> Own[P] | None:
    if k > 0:
        return P(k)
    return None
@error_return(E)
def make_p(k: int) -> Own[P]:
    if k < 0:
        raise E()
    return P(k)
def nums(n: int) -> Iterator[int]:
    for i in range(n):
        yield i
def rows(s: S, k: int, n: int | None, t: str | None) -> int:
    if (w := P(k)).v > 0:
        s.p = w
    r = make_opt(k)
    s.o = r
    u: P | Outer = P(k)
    if k > 2:
        u = Outer()
    s.u = u
    g = nums(3)
    c = 1
    flag = k > 0
    if not flag:
        return 0
    f: Callable[[Own[P]], int] = lambda q: q.v
    def inner(q: Own[P]) -> int:
        b = P(2)
        return q.v + b.v
    try:
        raise ValueError("x")
    except ValueError as e:
        print(e)
    for v in g:
        c += v
    return c + f(P(1)) + inner(P(2))
@error_return(E)
def er(s: S, k: int) -> None:
    p = make_p(k)
    s.p = p
'''


def test_every_exception_and_forced_row_names_its_reason() -> None:
    func, table = _plan(_ROWS_PROGRAM, "rows")
    want = {
        # A walrus target, a local off an `Own[R] | None` call and a
        # pointer-union local move what they own, as fact 1 says.
        ("w", "walrus"): (None, None, True),
        ("r", "decl"): (None, None, True),
        ("u", "decl"): (None, None, True),
        # A generator object owns its frame, which never moves once
        # started: the concrete-generator decl row's exception.
        ("g", "decl"): (M.GENERATOR_OBJECT, None, False),
        ("c", "decl"): (M.OWNED_VALUE, None, False),
        ("flag", "decl"): (M.TRIVIAL_VALUE, None, False),
        ("n", "param"): (None, M.BY_VALUE_OPTIONAL, True),
        ("t", "param"): (None, M.BY_VALUE_OPTIONAL, True),
    }
    for (name, kind), (exc, forced, movable) in want.items():
        rec = _rec(table, name, kind)
        assert (rec.move_exception, rec.move_forced, rec.movable) == (
            exc, forced, movable), (name, kind)
    lam = [r for node in table.scope_at.values()
           for r in node.bindings.values() if r.site_kind == "lambda"]
    assert {(r.name, r.move_exception, r.movable) for r in lam} == {
        ("q", M.LAMBDA_PARAM, False)}
    # A nested def's parameters and locals move what they own.
    nested = [r for node in table.scope_at.values()
              for r in node.bindings.values()
              if r.site_kind == "nested_param"]
    assert {(r.name, r.move_exception, r.movable) for r in nested} == {
        ("q", None, True)}
    b = [r for r in table.records() if r.name == "b"]
    assert [(r.move_exception, r.movable) for r in b] == [(None, True)]
    # So does an `@error_return` call's unwrap.
    func, table = _plan(_ROWS_PROGRAM, "er")
    p = _rec(table, "p", "decl")
    assert (p.move_exception, p.movable) == (None, True)
    # The rows that are defects name the entry tracking them.
    assert M.DYN.slug == "dyn-local-own-arg-ill-formed"
    assert M.TRIVIAL_VALUE.slug is None and M.BY_VALUE_OPTIONAL.forced


_ROWS_PROGRAM_3 = _PRELUDE + '''
from typing import Iterator
from tpy import int32, readonly
def mk(k: int) -> Own[P]:
    if k > 100:
        raise ValueError("x")
    return P(k)
def nums(n: int) -> Iterator[int]:
    for i in range(n):
        yield i
def own_loop(xs: Iterator[Own[P]]) -> Iterator[int]:
    for a in xs:
        yield a.v
def consuming_loop(out: list[P]) -> Iterator[int]:
    # Annotated: unannotated, the frame field takes another row.
    xs: list[P] = [P(1), P(2)]
    for a in xs:
        yield a.v
        out.append(a)
def borrowed_field(o: Outer) -> Iterator[int]:
    alias = o.p
    yield alias.v
def ptr_hoist(out: list[P], k: int) -> None:
    try:
        y = mk(k)
    except ValueError:
        y = P(7)
    out.append(y)
def loop_fresh(xs: Iterator[Own[P]], b: P, out: list[P]) -> None:
    for a in xs:
        a = P(4)
        out.append(a)
    a = b
    print(a.v)
def not_owned(o: Outer) -> int:
    alias = o.p
    def inner() -> int:
        return 1
    return alias.v + inner()
def values(p: tuple[Own[int32], int32]) -> int32:
    return p[0]
def gen_frame() -> Iterator[int]:
    g = nums(2)
    for v in g:
        yield v
def loop_fresh_alone(xs: Iterator[Own[P]], out: list[P]) -> None:
    for a in xs:
        a = P(4)
        out.append(a)
def nested_alias() -> int:
    def own_local() -> int:
        inner = P(5)
        target = inner
        target.v += 1
        return target.v
    return own_local()
def const_hoist(o: readonly[Outer], c: bool) -> int:
    if c:
        q = o.p
    else:
        q = o.p
    return q.v
'''


def test_frame_hoist_and_loop_rows_pin_their_move() -> None:
    func, table = _plan(_ROWS_PROGRAM_3 + '''
def closed() -> int:
    g = nums(2)
    t = 0
    for v in g:
        t += v
    del g
    return t
''', "closed", prelude="")
    # A plain body's generator local a `del` closes: an optional slot that
    # moves whatever it owns.
    g = _rec(table, "g", "decl")
    assert (g.move_exception, g.move_forced, g.movable) == (
        None, M.CLOSED_FRAME, True)
    _bodies, tables = lower_bodies(_ROWS_PROGRAM_3)

    def field(fn: str, name: str) -> BindingRepr:
        return tables[fn].root.bindings[name]
    want = {
        # The frame moves a step an `Own` element loop or a consuming loop
        # takes out of its element pointer.
        ("own_loop", "a"): (None, None, True),
        ("consuming_loop", "a"): (None, None, True),
        # A borrowed frame field: the frame layout holds a pointer.
        ("borrowed_field", "alias"): (M.FRAME_LAYOUT, None, False),
        # A generator object owns its frame, which never moves.
        ("gen_frame", "g"): (M.GENERATOR_OBJECT, None, False),
    }
    got = {}
    for (fn, name), _want in want.items():
        rec = field(fn, name)
        got[(fn, name)] = (rec.move_exception, rec.move_forced, rec.movable)
    assert got == want
    func, table = _plan(_ROWS_PROGRAM_3, "ptr_hoist", prelude="")
    # A pointer hoist re-pointed by its binds moves out of the object it
    # points at where it owns it.
    y = _rec(table, "y", "hoist")
    assert (y.move_exception, y.movable) == (None, True)
    func, table = _plan(_ROWS_PROGRAM_3, "loop_fresh", prelude="")
    # The loop site owns its step and its body's rebind: the rebound slot
    # moves whatever the `a = b` after the loop borrows.
    a = _rec(table, "a", "loop")
    assert (a.move_exception, a.movable) == (None, True)
    # So it does without the borrowing binding after the loop.
    func, table = _plan(_ROWS_PROGRAM_3, "loop_fresh_alone", prelude="")
    a = _rec(table, "a", "loop")
    assert (a.move_exception, a.movable) == (None, True)
    func, table = _plan(_ROWS_PROGRAM_3, "nested_alias", prelude="")
    # A nested def's local bound at another's last use is a reference to
    # the owner it takes over: owned, and the reference alias's exception
    # (BUGS.md#nested-def-owned-binding-copies).
    target = [r for node in table.scope_at.values()
              for r in node.bindings.values() if r.name == "target"]
    assert {(r.move_exception, r.movable) for r in target} == {
        (M.ALIAS, False)}
    func, table = _plan(_ROWS_PROGRAM_3, "values", prelude="")
    # An ownership-transfer tuple parameter moves, value leaves and all.
    p = table.root.lookup("p")
    assert (p.move_exception, p.move_forced, p.movable) == (None, None, True)


def test_not_owned_representations_are_fact_one_assertions() -> None:
    # An alias and a nested def's name own nothing in fact 1: their rows
    # carry no exception of their own.
    func, table = _plan(_ROWS_PROGRAM_3, "not_owned", prelude="")
    compiler, analyzer = _ANALYZERS[id(func)]
    ctx = analyzer.ctx
    for name, kind in (("alias", "decl"), ("inner", "def")):
        rec = _rec(table, name, kind)
        assert not local_ownership(ctx, func, name), name
        assert (rec.move_exception, rec.movable) == (None, False), name
    # A const borrow slot, the same.
    func, table = _plan(_ROWS_PROGRAM_3, "const_hoist", prelude="")
    compiler, analyzer = _ANALYZERS[id(func)]
    rec = _rec(table, "q", "hoist")
    assert rec.const and not local_ownership(analyzer.ctx, func, "q")
    assert (rec.move_exception, rec.movable) == (None, False)


def test_aliases_except_targets_and_globals_own_nothing() -> None:
    func, table = _plan('''
G = P(9)
def own_nothing(o: Outer) -> int:
    alias = o.p
    try:
        raise ValueError("x")
    except ValueError as e:
        print(e)
    return alias.v + G.v
''', "own_nothing")
    compiler, analyzer = _ANALYZERS[id(func)]
    ctx = analyzer.ctx
    assert not local_ownership(ctx, func, "alias")
    assert not local_ownership(ctx, func, "e")
    reads = []
    walk_body_stmts(func.body, lambda e: walk_expr_tree(
        e, lambda x: reads.append(x) or True), lambda s: None)
    g = [x for x in reads if isinstance(x, TpyName) and x.name == "G"][0]
    assert ctx.read_bindings[g].kind is ReadKind.MODULE
    for name in ("alias", "e"):
        assert not any(r.movable for r in table.records()
                       if r.name == name), name


_ROWS_PROGRAM_2 = '''
from typing import Iterator, Protocol
from tpy import dynamic
@dynamic
class Shape(Protocol):
    def area(self) -> int: ...
class Sq:
    def __init__(self, n: int) -> None:
        self.n = n
    def area(self) -> int:
        return self.n
def keep(s: Own[Shape]) -> int:
    return s.area()
def pairs(n: int) -> Iterator[tuple[int, Own[P]]]:
    for i in range(n):
        yield (i, P(i))
def nums(n: int) -> Iterator[int]:
    for i in range(n):
        yield i
def rows2(o: Outer, k: int, ps: list[P]) -> int:
    s: Shape = Sq(k)
    t = 0
    for i, b in pairs(2):
        o.p = b
    try:
        x = P(k)
    finally:
        print("f")
    o.p = x
    name = str(k) + "x"
    pair = (o.p, 1)
    for q in [P(1), P(2)]:
        pass
    print(q.v, name, pair[1])
    if k > 0:
        g = nums(2)
    else:
        g = nums(3)
    for v in g:
        t += v
    return keep(s) + t
def comps(xs: list[P]) -> int:
    w = P(1)
    ys = [w for w in xs]
    return len(ys) + w.v
'''


def test_more_rows_name_their_reason() -> None:
    func, table = _plan(_ROWS_PROGRAM_2, "rows2")
    want = {
        ("s", "decl"): (M.DYN, None, False),
        ("b", "unpack"): (None, None, True),
        ("x", "hoist"): (None, M.OWNED_SLOT, True),
        ("name", "decl"): (M.VIEWFAM_VALUE, None, False),
        # A borrow tuple moves where its reads' element verdicts do.
        ("pair", "decl"): (None, None, True),
        # A branch-first generator object's hoisted pointer owns the frame,
        # which never moves.
        ("g", "hoist"): (M.GENERATOR_OBJECT, None, False),
        # A loop variable read after its loop writes the hoisted slot, whose
        # owned-slot move it inherits.
        ("q", "loop"): (M.PASS_SLOT, M.INHERITED_OUTER, True),
    }
    for (name, kind), (exc, forced, movable) in want.items():
        rec = _rec(table, name, kind)
        assert (rec.move_exception, rec.move_forced, rec.movable) == (
            exc, forced, movable), (name, kind, rec.move_exception,
                                    rec.move_forced, rec.movable)
    func, table = _plan(_ROWS_PROGRAM_2, "comps")
    w = _rec(table, "w", "comp")
    # The clause variable moves only at its sinks; it inherits the move of
    # the owned local it shadows (inert: the region decides there).
    assert (w.move_exception, w.move_forced, w.movable) == (
        M.COMP_CLAUSE, M.INHERITED_OUTER, True)


_ROWS_PROGRAM_4 = _PRELUDE + '''
from typing import Iterator
from tpy import int32, readonly
def ident[T](x: T) -> T:
    return x
def mk(k: int) -> Own[P]:
    if k > 100:
        raise ValueError("x")
    return P(k)
def make() -> Own[readonly[P]]:
    return P(1)
def gen_slot[T](x: T, out: list[T]) -> Iterator[int32]:
    y = ident(x)
    out.append(y)
    yield 1
async def async_slot[T](x: T, out: list[T]) -> int32:
    y = ident(x)
    out.append(y)
    return 1
def gen_hoist(k: int) -> Iterator[int]:
    try:
        y = mk(k)
    except ValueError:
        y = P(7)
    yield y.v
def const_if(c: bool) -> int:
    if c:
        a = make()
    else:
        return 0
    return a.v
def const_try() -> int:
    try:
        a = make()
    finally:
        print("done")
    return a.v
g = "old"
def write_global(c: bool) -> str:
    global g
    if c:
        g = "new"
    return g
'''


def test_frame_slots_const_hoists_and_globals_pin_their_move() -> None:
    _bodies, tables = lower_bodies(_ROWS_PROGRAM_4)

    def field(fn: str, name: str) -> tuple:
        rec = tables[fn].root.bindings[name]
        return (rec.move_exception, rec.move_forced, rec.movable)
    # A generic resumable body's `y = ident(x)` holds its own frame slot,
    # which moves: generator and coroutine alike.
    assert field("gen_slot", "y") == (None, None, True)
    assert field("async_slot", "y") == (None, None, True)
    # A frame's try-hoisted local is a slot its binds re-point, which moves
    # out of the object it owns.
    assert field("gen_hoist", "y") == (None, None, True)
    # An owned readonly result hoisted to a const pointer over its owning
    # slot: owned, and the const slot does not move.
    for fn in ("const_if", "const_try"):
        func, table = _plan(_ROWS_PROGRAM_4, fn, prelude="")
        compiler, analyzer = _ANALYZERS[id(func)]
        a = _rec(table, "a", "hoist")
        assert local_ownership(analyzer.ctx, func, "a"), fn
        assert (a.move_exception, a.movable) == (M.CONST_SLOT, False), fn
    # A declared global is module storage: never a LOCAL, never moved.
    func, table = _plan(_ROWS_PROGRAM_4, "write_global", prelude="")
    compiler, analyzer = _ANALYZERS[id(func)]
    assert not local_ownership(analyzer.ctx, func, "g")
    assert not any(r.movable for r in table.records() if r.name == "g")


def test_a_module_level_rebound_loop_slot_moves_its_step(
        monkeypatch: pytest.MonkeyPatch) -> None:
    tables: list = []
    install = _bindings.plan_and_install

    def capture(lc, body, seeds, **kwargs):
        out = install(lc, body, seeds, **kwargs)
        if kwargs.get("top_level"):
            tables.append(lc.table)
        return out
    monkeypatch.setattr(_bindings, "plan_and_install", capture)
    _assert_byte_identical(_PRELUDE + '''
from typing import Iterator
def mk() -> Iterator[Own[P]]:
    yield P(1)
out: list[P] = []
for a in mk():
    a = P(4)
    out.append(a)
print(len(out))
''')
    a, = (r for t in tables for r in t.records()
          if r.name == "a" and r.site_kind == "loop")
    # The loop site owns its step and its body's rebind, at module level
    # too: the rebound slot moves out of it.
    assert (a.move_exception, a.movable) == (None, True)


def test_an_owned_optional_select_planned_as_a_passthrough_is_an_alias() -> None:
    # A select of fresh arms into a pointer-repr Optional is owned, and the
    # planner plans it as a call passthrough: the scoped ALIAS row, which
    # the lowering's `expr.ifexpr` refusal keeps out of the output.
    func, table = _plan('''
def pick(c: bool) -> int:
    t = P(1) if c else None
    if t is not None:
        return t.v
    return -1
''', "pick")
    compiler, analyzer = _ANALYZERS[id(func)]
    t = _rec(table, "t", "decl")
    assert local_ownership(analyzer.ctx, func, "t")
    assert (t.move_exception, t.movable) == (M.ALIAS, False)


def test_module_storage_that_fact_one_owns_stays_unmoved() -> None:
    # Module storage is a row where fact 1 says the binding owns (the
    # planner's module-global test and sema's declared globals disagree):
    # the row stands and the name is not moved; owning nothing, it is no
    # row.
    rec = BindingRepr(name="g", site=None, type=None, form=F.NONE,
                      site_kind="hoist", move_exception=M.MODULE_STORAGE)
    owned = _bindings.with_movable(rec, BindingOwnership(Acquired.OWNED_LOCAL))
    assert (owned.move_exception, owned.movable) == (M.MODULE_STORAGE, False)
    plain = _bindings.with_movable(rec, NOT_OWNED)
    assert (plain.move_exception, plain.movable) == (None, False)
