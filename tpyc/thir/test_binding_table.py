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
from .lower.context import BindingTableError, _LowerCtx, _Prescan
from .source import BindingRepr, Held
from .testutil import _compile, _entry, lower_bodies
from ..typesys import unwrap_readonly, unwrap_ref_type
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


def test_a_match_capture_lives_in_its_arm_and_outlives_the_match() -> None:
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
    # Not visible to a sibling arm; the lowering registers a capture in
    # the enclosing block, so it outlives the statement.
    assert table.visible(other.body[0], "q") is None
    assert table.visible(other.body[0], "w").site is other
    assert table.visible(ret, "q") is rec


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
    assert loop.binding.name == "x" and loop.binding.row == "loop.var"
    validate_function(fn, lowered=True)
    # Hand-built THIR (no lowering behind it) may leave the record out.
    bare = replace(fn, body=tuple(replace(s, binding=None)
                                  if isinstance(s, th.THIRVarDecl) else s
                                  for s in fn.body))
    validate_function(bare)


def test_a_declaration_its_record_contradicts_is_rejected() -> None:
    # A `T&` declaration stamped with a record that is no ref alias, or a
    # lowered declaration with no record at all, is an internal error.
    fn = _lowered_decls()

    def with_alias(new):
        return replace(fn, body=tuple(
            new if isinstance(s, th.THIRVarDecl) and s.name == "alias" else s
            for s in fn.body))
    alias = next(s for s in fn.body
                 if isinstance(s, th.THIRVarDecl) and s.name == "alias")
    wrong = replace(alias, binding=replace(alias.binding, ref_alias=False))
    with pytest.raises(THIRValidationError, match="binding contract"):
        validate_function(with_alias(wrong))
    with pytest.raises(THIRValidationError, match="carries no record"):
        validate_function(with_alias(replace(alias, binding=None)),
                          lowered=True)


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


def rebind(o: Outer, c: bool) -> int:
    cur = P(1)
    if c:
        cur = o.p
    else:
        cur = P(2)
    return cur.v


def readonly_alias(o: Outer) -> int:
    alias = o.p
    return alias.v


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


def test_a_pointer_declaration_of_a_non_pointer_record_is_rejected(
        contracts) -> None:
    fn = contracts["rebind"]
    decl = _find(fn, th.THIRVarDecl, "cur")
    _rejects(fn, decl, replace(decl, binding=replace(
        decl.binding, pointer=False)), "of a non-pointer binding")


def test_a_rebind_slot_declaration_without_one_is_rejected(
        contracts) -> None:
    fn = contracts["rebind"]
    decl = _find(fn, th.THIRVarDecl, "cur")
    _rejects(fn, decl, replace(decl, binding=replace(
        decl.binding, rebind_slot=False)), "rebind-slot declaration")


def test_a_pointer_declaration_of_a_storage_tuple_is_rejected(
        contracts) -> None:
    fn = contracts["rebind"]
    decl = _find(fn, th.THIRVarDecl, "cur")
    _rejects(fn, decl, replace(decl, binding=replace(
        decl.binding, storage_tuple=True)), "storage tuple or value optional")


def test_a_ref_alias_declared_against_its_records_const_is_rejected(
        contracts) -> None:
    fn = contracts["readonly_alias"]
    decl = _find(fn, th.THIRVarDecl, "alias")
    assert decl.is_const and decl.binding.const
    _rejects(fn, decl, replace(decl, binding=replace(
        decl.binding, const=False)), "declared const=True against const=False")


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
    one = BindingRepr(name="x", site=site, type=None, row="test")
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
    glob = BindingRepr(name="M", site=None, type=tt, row="global.module",
                       tuple_layout=layout)
    local = replace(glob, row="decl.btuple_literal")
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
