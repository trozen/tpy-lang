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

import pytest

from ..compilation_context import activate_compiler
from ..parse.nodes import (TpyExprStmt, TpyForEach, TpyFunction, TpyIf,
                           TpyLambda, TpyMatch, TpyName, TpyNestedDef,
                           TpyReturn, TpyVarDecl, TpyWith, walk_expr_tree)
from . import nodes as th
from .lower import bindings as _bindings
from .lower import iter_module_callables, lower_function
from .lower import match as _match
from .lower.bindings import (BindingTable, PlanInputs, ScopeNode,
                             plan_bindings, project_held)
from .lower.comprehensions import _clause_bindings, _clause_route
from .lower.context import (BindingTableError, _LowerCtx, _LowerScope,
                            _Prescan)
from .lower.statements import _admit_hoist, _Hoist, _lower_first_decl
from .reject import ThirUnsupported, begin_stmt
from .source import BindingKind, BindingRepr, Held
from .testutil import _compile, _entry, lower_bodies
from ..typesys import PendingListType, unwrap_readonly, unwrap_ref_type
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


def _plan(source: str, name: str):
    compiler, modules = _compile(_PRELUDE + source)
    entry = _entry(modules)
    func = next(f for f, _st in iter_module_callables(entry.ast,
                                                      entry.analyzer)
                if f.name == name)
    with activate_compiler(compiler):
        lc = _LowerCtx(func, entry.analyzer, None)
        table = plan_bindings(PlanInputs(
            func=func, analyzer=entry.analyzer, prescan=lc.prescan,
            params=lc.params, seeds={n: t for n, t in func.params},
            sema_movable=frozenset(lc.sema_movable_locals)), func.body)
    _ANALYZERS[id(func)] = (compiler, entry.analyzer)
    return func, table


def _sites(table, name: str) -> list:
    return [r for r in table.records() if r.name == name]


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
    assert q.kind not in (BindingKind.MATCH_REF, BindingKind.MATCH_COPY,
                          BindingKind.MATCH_SUBJECT_PTR)


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
    assert loop.binding.name == "x" and loop.binding.kind is BindingKind.LOOP_VAR
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
    for x in items:
        x.v = 0
        print(x.v)
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
    wrong = replace(write, binding=replace(write.binding, frame_slot=False,
                                           coro_frame=False))
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
    table = BindingTable(ScopeNode(None))
    one = BindingRepr(name="x", site=site, type=None, kind=BindingKind.DECL_PLAIN)
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


def test_only_a_module_global_park_may_declare_a_pointer_slot_tuple() -> None:
    # A tuple global whose reference elements are pointer slots is written
    # by parking the value in a static slot; that node may declare it, but
    # neither a local record with the same layout nor another pointer-slot
    # kind may borrow the exemption.
    func, table = _plan('''
def pair(t: tuple[bool, P]) -> int:
    return t[1].v
''', "pair")
    compiler, an = _compile_analyzer(func)
    tt = unwrap_readonly(unwrap_ref_type(table.root.lookup("t").type))
    with activate_compiler(compiler):
        layout = tuple_layout(tt, an, borrow=True)
    assert layout is not None
    glob = BindingRepr(name="M", site=None, type=tt, kind=BindingKind.GLOBAL_MODULE,
                       tuple_layout=layout)
    local = replace(glob, kind=BindingKind.DECL_BTUPLE_LITERAL)
    decl = th.THIRPtrLocalDecl(name="M", resolved_type=tt,
                               kind=th.PtrSlotKind.GLOBAL_RVALUE,
                               cpp_type="std::tuple<bool, P>",
                               val_cpp="std::tuple<bool, P*>")
    rebind = th.THIRPtrLocalRebind(name="M",
                                   kind=th.PtrSlotKind.GLOBAL_HOIST_RVALUE,
                                   val_cpp="std::tuple<bool, P>",
                                   tuple_cpp="std::tuple<bool, P*>")
    assert _contract_problem(decl, glob) is None
    assert _contract_problem(rebind, glob) is None
    assert "non-pointer" in _contract_problem(decl, local)
    assert "non-pointer" in _contract_problem(rebind, local)
    other = replace(decl, kind=th.PtrSlotKind.BRANCH_RVALUE)
    assert "non-pointer" in _contract_problem(other, glob)
    assert "non-pointer" in _contract_problem(
        replace(rebind, tuple_cpp=None), glob)


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
    assert rec.kind is BindingKind.DECL_BTUPLE_REBOUND
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
    assert rec.kind is BindingKind.DECL_NO_INIT_VALUE


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
    assert rec.kind is BindingKind.DECL_PTR_VARIANT_SLOT
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
    assert rec.kind is BindingKind.DECL_BTUPLE_INLINE_ARMS


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
            params=lc.params, seeds={n: t for n, t in func.params},
            sema_movable=frozenset(lc.sema_movable_locals))
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
    assert table.declared_by(func.body[0], "d").kind is BindingKind.DECL_DYN


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
            _bindings._Site("decl", node=stmt, init=stmt.init), "xs",
            pending, inp, lambda n: None)
    assert rec.kind is BindingKind.DECL_CONTAINER_SLOT


def test_a_frame_slot_reaching_the_first_declaration_is_an_error(
        monkeypatch) -> None:
    # A frame slot is written by the frame's own arms and the planner never
    # gives a local declaration one, so the record is forged.
    decl_row = _bindings._decl_row

    def frame_row(site, name, typ, inp, visible):
        rec = decl_row(site, name, typ, inp, visible)
        return replace(rec, frame_slot=True) if name == "x" else rec
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
    kinds = {r.name: r.kind for r in table.records()
             if isinstance(r.site, TpyVarDecl)}
    assert kinds["u"] is BindingKind.DECL_BTUPLE_REF_ALIAS
    assert kinds["v"] is BindingKind.DECL_BTUPLE_REF_ALIAS
    assert kinds["w"] is BindingKind.DECL_BTUPLE_ALIAS


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
    assert rec.kind is BindingKind.DECL_TPARAM_CALL
    assert rec.const
    rec = decl("pick", "b")
    assert rec.kind is BindingKind.DECL_TPARAM_CALL
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
    assert rec.kind is BindingKind.DECL_STORAGE_TUPLE_CHAIN
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
    assert rec.kind is BindingKind.DECL_STORAGE_TUPLE_CHAIN
    assert rec.storage_tuple
    assert rec.tuple_layout is layout and rec.pointer


def test_a_borrow_tuple_hoist_is_the_branch_or_the_loop_kind() -> None:
    # The branch-bound borrow tuple (if / try) spells its const from the
    # fixpoint and, off an owning call, has the chain head's rebind slot (the
    # if's only); the loop variable's is the plain borrow spelling; a with
    # body's has no predecl.
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
    assert t.kind is BindingKind.HOIST_BTUPLE_BRANCH and t.rebind_slot
    # The statement keys the loop variable too.
    u, = (r for r in table.sites[for_stmt]
          if r.name == "u" and r.kind.family == "hoist")
    assert u.kind is BindingKind.HOIST_BTUPLE_LOOP and not u.rebind_slot
    w = table.declared_by(try_stmt, "w")
    assert w.kind is BindingKind.HOIST_BTUPLE_BRANCH and not w.rebind_slot
    assert table.declared_by(with_stmt, "z").kind is BindingKind.HOIST_PLAIN


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
    assert q.kind is BindingKind.HOIST_CONST_POINTER and q.const and q.pointer
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
        assert rec.kind is BindingKind.HOIST_CONST_POINTER, rec
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
    kinds = {r.name: r.kind for r in table.records()}
    assert kinds["x"] is BindingKind.HOIST_PLAIN
    assert kinds["y"] is BindingKind.HOIST_CONST_POINTER
    assert kinds["z"] is BindingKind.HOIST_PLAIN
    compiler, func, inp = _plan_inputs(src, "gen")
    with activate_compiler(compiler):
        table = plan_bindings(inp, func.body)
    kinds = {r.name: r.kind for r in table.records()}
    assert kinds["a"] is BindingKind.HOIST_PLAIN
    assert kinds["b"] is BindingKind.HOIST_OPT_STORAGE
    assert kinds["d"] is BindingKind.HOIST_PLAIN


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
    with activate_compiler(compiler):
        table = plan_bindings(inp, func.body)
    try_x, if_w, if_g, while_h, _ret = func.body
    cases = [(try_x, "try", "x", BindingKind.HOIST_PLAIN, "try.hoist"),
             (if_w, "if", "w", BindingKind.HOIST_PLAIN, "if.hoist_type"),
             (if_g, "if", "g", BindingKind.HOIST_PLAIN, "if.hoist_type"),
             (while_h, "while", "h", BindingKind.HOIST_PLAIN,
              "while.hoist_type")]
    compiler_g, func_g, inp_g = _plan_inputs(src, "gen")
    with activate_compiler(compiler_g):
        table_g = plan_bindings(inp_g, func_g.body)
    if_a, if_d, if_t, while_e, for_u, _yield = func_g.body
    cases_g = [(if_a, "if", "a", BindingKind.HOIST_PLAIN, "if.hoist_type"),
               (if_d, "if", "d", BindingKind.HOIST_PLAIN, "if.hoist_type"),
               (if_t, "if", "t", BindingKind.HOIST_PLAIN, "if.hoist_type"),
               (while_e, "while", "e", BindingKind.HOIST_PLAIN,
                "while.hoist_type")]
    for stmt, ladder, name, kind, tag in cases:
        rec = table.declared_by(stmt, name)
        assert rec.kind is kind, name
        assert _admit_tag(compiler, stmt, ladder, rec) == tag, name
    for stmt, ladder, name, kind, tag in cases_g:
        rec = table_g.declared_by(stmt, name)
        assert rec.kind is kind, name
        assert _admit_tag(compiler_g, stmt, ladder, rec) == tag, name
    u, = (r for r in table_g.sites[for_u]
          if r.name == "u" and r.kind.family == "hoist")
    assert u.kind is BindingKind.HOIST_PLAIN
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
    # and a match alike.
    ("owned_then_lvalue", {}, {
        "x": (BindingKind.HOIST_POINTER, False, False, False),
        "y": (BindingKind.HOIST_POINTER, False, False, False),
        "z": (BindingKind.HOIST_POINTER, False, False, False)}),
    # A readonly type: the const pointer at an if and a match.
    ("readonly_rebound", {}, {
        "x": (BindingKind.HOIST_CONST_POINTER, True, False, False),
        "z": (BindingKind.HOIST_CONST_POINTER, True, False, False)}),
    # Inside a branch: the rebind-slot pointer at both.
    ("in_branch", {}, {
        "x": (BindingKind.HOIST_POINTER, False, True, False),
        "z": (BindingKind.HOIST_POINTER, False, True, False)}),
    # A const borrow: the const pointer at both; the match's is also the
    # capture it assigns by address.
    ("const_capture", {}, {
        "x": (BindingKind.HOIST_CONST_POINTER, True, False, False),
        "z": (BindingKind.HOIST_CONST_POINTER, True, False, True)}),
    # Rvalue-rebound: the rebind slot at an if and a match, none at a try.
    ("owned_rebound", {}, {
        "x": (BindingKind.HOIST_POINTER, False, True, False),
        "y": (BindingKind.HOIST_POINTER, False, False, False),
        "z": (BindingKind.HOIST_POINTER, False, True, False)}),
    # Borrow-only: the pointer local at both; the match's is the capture a
    # nested match re-seats by address.
    ("borrow_only", {}, {
        "x": (BindingKind.HOIST_POINTER, False, False, False),
        "z": (BindingKind.HOIST_POINTER, False, False, True)}),
    # Borrow-only and rvalue-rebound (no program found; the fact is given):
    # the rebind slot at both.
    ("borrow_only", {"rvalue_reassigned": frozenset({"x", "z"}),
                     "reassigned": frozenset({"x", "z"})}, {
        "x": (BindingKind.HOIST_POINTER, False, True, False),
        "z": (BindingKind.HOIST_POINTER, False, True, True)}),
])
def test_the_one_hoist_ladder_answers_per_statement(func_name, facts,
                                                      expected) -> None:
    compiler, func, inp = _plan_inputs(_LADDER, func_name, **facts)
    with activate_compiler(compiler):
        table = plan_bindings(inp, func.body)
    got = {r.name: (r.kind, r.const, r.rebind_slot, r.borrowed_capture)
           for r in table.records()
           if r.kind.family == "hoist" and r.name in expected}
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
    s = 0
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
    ("unpacks", {"a": (BindingKind.UNPACK_REF, True),
                 "n": (BindingKind.UNPACK_CREF, False),
                 "b": (BindingKind.UNPACK_OPT_PTR, True),
                 "c": (BindingKind.UNPACK_CREF, False)}),
    # A for head's Optional target is const as the iteration source; a
    # head target naming a visible local writes it (no record of its own).
    ("heads", {"b": (BindingKind.UNPACK_OPT_PTR, True),
               "y": (BindingKind.UNPACK_CREF, False),
               "q": (BindingKind.UNPACK_REF, True)}),
    # A record bound once is the reference, a reassigned one the pointer
    # the second `with` re-points; an owned enter result has no form here
    # (no program in the corpus reaches it).
    ("withs", {"p": (BindingKind.WITH_REF, False),
               "r": (BindingKind.WITH_POINTER, False),
               "c": (BindingKind.WITH_PLAIN, False)}),
    # A capture aliases the field it matched, const off an unmutated
    # subject only.
    ("captures", {"q": (BindingKind.MATCH_REF, True),
                  "z": (BindingKind.MATCH_REF, False)}),
    # A record or Optional element of a comprehension's unpack is a
    # reference, const as the clause's loop variable; a value its copy.
    ("comps", {"a": (BindingKind.COMP_UNPACK_REF, True),
               "n": (BindingKind.COMP_UNPACK_VALUE, False),
               "b": (BindingKind.COMP_UNPACK_STORAGE_OPT, True)}),
])
def test_each_statement_binding_is_the_kind_its_site_declares(
        func_name, expected) -> None:
    func, table = _plan(_STATEMENT_BINDS, func_name)
    got = {r.name: (r.kind, r.const) for r in table.records()
           if r.name in expected and r.site is not None}
    assert got == expected
    if func_name == "heads":
        # `s` is the function's local the second head assigns.
        assert [r.kind for r in _sites(table, "s")] == [
            BindingKind.DECL_PLAIN]


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
    # pointer-repr Optional's, the borrow tuple,
    # the reassigned borrow tuple over its owning slot, the owned record's
    # deferred-init slot, the value-repr Optional, the plain value.
    ("walruses", {"a": (BindingKind.WALRUS_PTR_ALIAS, False),
                  "b": (BindingKind.WALRUS_OPT_PTR, False),
                  "t": (BindingKind.WALRUS_BTUPLE, False),
                  "u": (BindingKind.WALRUS_BTUPLE_SLOT, False),
                  "f": (BindingKind.WALRUS_OWNED_SLOT, False),
                  "v": (BindingKind.WALRUS_VALUE_OPT, False),
                  "k": (BindingKind.WALRUS_VALUE, False)}),
    # A resumable body's walrus of a non-scalar has no form.
    ("gen_walrus", {"g": (BindingKind.WALRUS_PLAIN, False)}),
    # Unpack targets: an owned element moved out, a rebound record's
    # pointer, an `Own[A | B]` element's pointer variant, a written value
    # copied, a recursive wrapper's live reference; a `with` over a value
    # enter result copies.
    ("binds", {"a": (BindingKind.UNPACK_MOVE, False),
               "b": (BindingKind.UNPACK_PTR, False),
               "c": (BindingKind.UNPACK_PTR_VARIANT, False),
               "d": (BindingKind.UNPACK_VALUE, False),
               "e": (BindingKind.UNPACK_UNWRAP_REF, False),
               "x": (BindingKind.WITH_VALUE, False)}),
    # A match capture of a whole pointer subject copies the pointer (const
    # as the subject), a guarded scalar capture its value.
    ("captures", {"z": (BindingKind.MATCH_SUBJECT_PTR, True),
                  "y": (BindingKind.MATCH_COPY, False)}),
])
def test_each_binding_kind_is_the_form_its_site_builds(func_name,
                                                       expected) -> None:
    func, table = _plan(_REJECT_KINDS + _KIND_BINDS, func_name)
    got = {r.name: (r.kind, r.const) for r in table.records()
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
    assert rec.kind is BindingKind.DEF_CLOSURE_LOCAL and rec.nested_def
