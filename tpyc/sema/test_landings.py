"""The union member a committing check lands a value in, recorded by the
boundary that receives it (`landings`): one entry per boundary, the last
committed check's, a rejected trial leaving none, and a read-modify-write
subscript's getter and setter keys apart."""

from types import SimpleNamespace

import pytest

from ..parse import TpyAugAssign, TpyBinOp, TpyCall, TpyReturn, TpyVarDecl
from ..parse.nodes import walk_body_stmts, walk_expr_tree
from ..identity_map import IdentityMap
from ..thir.testutil import _compile, _entry
from ..typesys import (TypeRegistry, BOOL, FLOAT, FLOAT32, INT16, INT32, INT64, STR, LiteralType,
                       make_union)
from .pending_num import DeferredIntOp, PendingNums
from ..codegen_cpp.context import ThirRejectError
from ..codegen_cpp.generator import _check_landings_read
from ..typesys import NONE
from .landings import (Boundary, PublishedLandings, UnionLanding, boundary_of,
                       landing_exempt,
                       literal_token_type, lookup, member_at, move_landing,
                       param_boundary, peek_member, publish, register,
                       renders_as, subscript_boundary)


def _analyzed(source: str):
    _compiler, modules = _compile(source)
    entry = _entry(modules)
    return entry, entry.analyzer.ctx.union_landings


def _stmts(entry, fn: str, kind) -> list:
    func = next(f for f in entry.ast.functions if f.name == fn)
    found: list = []
    walk_body_stmts(func.body, lambda e: None,
                    lambda s: found.append(s) if isinstance(s, kind) else None)
    return found


def walk_binops(entry, fn: str) -> list:
    func = next(f for f in entry.ast.functions if f.name == fn)
    found: list = []

    def visit(e) -> bool:
        if isinstance(e, TpyBinOp):
            found.append(e)
        return True

    walk_body_stmts(func.body, lambda e: walk_expr_tree(e, visit),
                    lambda s: None)
    return found


def test_register_replaces_and_clears() -> None:
    table = IdentityMap()
    holder = object()
    b = Boundary(holder, ("value", None))
    register(table, b, {(): INT32})
    register(table, b, {(): INT64})
    assert lookup(table, b) == {(): INT64}
    register(table, b, {})
    assert lookup(table, b) is None


def test_landing_paths() -> None:
    landing = UnionLanding()
    landing.at(1).record(INT64)
    landing.record(INT32)
    assert landing.members == {(1,): INT64, (): INT32}


def test_declaration_records_its_member() -> None:
    entry, table = _analyzed(
        "from tpy import int64, uint32, uint64\n"
        "def f(c: uint32) -> None:\n"
        "    v: int64 | uint64 = c\n"
        "    print(v)\n")
    (decl,) = _stmts(entry, "f", TpyVarDecl)
    assert member_at(table, boundary_of(decl, "init")) == INT64


def test_rejected_overload_trial_leaves_no_entry() -> None:
    # The `int32 | str` variant is tried and loses to the exact `int64` one:
    # its argument landing is rolled back with the trial.
    entry, table = _analyzed(
        "from tpy import int32, int64\n"
        "from typing import overload\n"
        "@overload\n"
        "def g(x: int64) -> int32: ...\n"
        "@overload\n"
        "def g(x: int32 | str) -> int32: ...\n"
        "def g(x: int64 | int32 | str) -> int32:\n"
        "    return 1\n"
        "def f(n: int64) -> int32:\n"
        "    return g(n)\n")
    (ret,) = _stmts(entry, "f", TpyReturn)
    call = ret.value
    while not isinstance(call, TpyCall):
        call = call.expr
    assert member_at(table, param_boundary(call, 0)) is None


def test_aug_subscript_getter_and_setter_keys_are_two_entries() -> None:
    entry, table = _analyzed(
        "from tpy import int32, int64\n"
        "class M:\n"
        "    v: int32\n"
        "    def __init__(self) -> None:\n"
        "        self.v = 0\n"
        "    def __getitem__(self, key: int32 | str) -> int32:\n"
        "        return self.v\n"
        "    def __setitem__(self, key: int64 | str, value: int32) -> None:\n"
        "        self.v = value\n"
        "def key() -> int32:\n"
        "    return 1\n"
        "def f() -> None:\n"
        "    m = M()\n"
        "    m[key()] += 1\n"
        "    print(m.v)\n")
    (aug,) = _stmts(entry, "f", TpyAugAssign)
    sub = aug.target
    assert member_at(table, subscript_boundary(sub, "__getitem__", 0)) == INT32
    assert member_at(table, subscript_boundary(sub, "__setitem__", 0)) == INT64


def test_cancelled_placeholder_registers_nothing() -> None:
    # A placeholder its step peeled is resolved no more: its check (and the
    # landing it would record) never runs; a live one runs.
    ran: list[str] = []
    live = DeferredIntOp(None, (INT32,), lambda _ts: ran.append("live"))
    void = DeferredIntOp(None, (INT32,), lambda _ts: ran.append("void"),
                         cancelled=True)
    fake = SimpleNamespace(
        ctx=SimpleNamespace(trial_depth=0, func=SimpleNamespace(
            pending_num_deferred=[live, void])),
        _ready=lambda op: True, finalize=lambda t: t)
    PendingNums.resolve_ready(fake)
    assert ran == ["live"]


def test_renders_as_reads_types_not_spellings() -> None:
    # A value whose render already has the member's type needs no
    # conversion; a float literal's token is a double, never a float32.
    registry = TypeRegistry()
    assert renders_as(INT64, INT64, registry)
    assert not renders_as(INT32, INT64, registry)
    assert not renders_as(None, INT64, registry)
    assert literal_token_type(2.5) == FLOAT
    assert literal_token_type(2.5, float32=True) == FLOAT32
    assert literal_token_type(True) == BOOL
    assert literal_token_type(3) is None


def test_shared_alternative_is_a_union_fact() -> None:
    assert not make_union(INT32, INT64).shares_alternative()
    assert make_union(LiteralType(STR, ("a",)), LiteralType(STR, ("b",)),
                      INT32).shares_alternative()


def test_exemption_reads_the_landed_members_alternative() -> None:
    # The members sharing an alternative are ambiguous for whatever lands
    # in that alternative: an `int16` widened into `int32` exactly as an
    # `int32`; a member of its own alternative is not exempt.
    lits = make_union(LiteralType(INT32, (1,)), LiteralType(INT32, (2,)),
                      INT32)
    assert landing_exempt(lits, INT32, INT32)
    assert landing_exempt(lits, INT16, INT32)
    # A reader finds no member recorded for the widened source.
    assert landing_exempt(lits, INT16)
    mixed = make_union(STR, LiteralType(INT32, (1,)), INT32)
    assert not landing_exempt(mixed, STR, STR)
    assert landing_exempt(mixed, INT32, INT32)


def test_dunder_operand_and_needle_record_their_members() -> None:
    entry, table = _analyzed(
        "from tpy import int32, int64, uint32, uint64\n"
        "class B:\n"
        "    def __init__(self) -> None:\n"
        "        pass\n"
        "    def __add__(self, other: int32 | int64) -> bool:\n"
        "        return True\n"
        "def f(c: uint32) -> None:\n"
        "    b = B()\n"
        "    print(b + int64(5))\n"
        "    xs: list[int64 | uint64] = []\n"
        "    print(c in xs)\n")
    found = [h for h in table if isinstance(h, TpyBinOp)]
    add = next(e for e in found if e.op == "+")
    needle = next(e for e in walk_binops(entry, "f") if e.op == "in")
    assert member_at(table, boundary_of(add, "right")) == INT64
    # The native lookup's needle sits at its own call (`contains_call`).
    assert member_at(table, param_boundary(needle.contains_call, 0)) == INT64


def test_published_landing_read_marks_and_moves() -> None:
    # Applying a member marks its element path; a route decision's peek
    # marks nothing; a move carries the mark to the boundary the value
    # now sits at.
    table = PublishedLandings()
    a, b, c = object(), object(), object()
    register(table, boundary_of(a, "value"), {(): INT64})
    register(table, boundary_of(b, "value"), {(): INT32})
    assert [x.holder for x, _ in table.unread()] == [a, b]
    assert peek_member(table, boundary_of(a, "value")) == INT64
    assert [x.holder for x, _ in table.unread()] == [a, b]
    assert member_at(table, boundary_of(a, "value")) == INT64
    assert [x.holder for x, _ in table.unread()] == [b]
    member_at(table, boundary_of(b, "value"))
    move_landing(table, b, ("value", None), c, ("args", 0))
    assert table.unread() == []
    assert member_at(table, boundary_of(c, "args", 0)) == INT32


def test_published_landing_marks_each_element_path() -> None:
    # A tuple's elements are applied one by one: an element no conversion
    # reached stays unread although its sibling was applied.
    table = PublishedLandings()
    t = object()
    register(table, boundary_of(t, "init"), {(0,): INT64, (1,): INT32})
    members = lookup(table, boundary_of(t, "init"))
    assert members.get((0,)) == INT64
    assert [(x.holder, x.role, p) for x, p in table.unread()] == [
        (t, ("init", None), (1,))]


def test_published_absence_records_nothing() -> None:
    # None's member is the one absence every union spells alike: the body
    # keeps it for declaration narrowing, the published table drops it.
    body: IdentityMap = IdentityMap()
    holder = object()
    register(body, boundary_of(holder, "init"), {(): NONE})
    published = PublishedLandings()
    publish(body, published)
    assert member_at(body, boundary_of(holder, "init")) == NONE
    assert lookup(published, boundary_of(holder, "init")) is None


def test_unread_landing_fails_the_module() -> None:
    table = PublishedLandings()
    holder = SimpleNamespace(loc=None)
    register(table, boundary_of(holder, "value"), {(): INT64})
    with pytest.raises(ThirRejectError) as err:
        _check_landings_read(table)
    assert err.value.reason == "union.unread_landing"
    member_at(table, boundary_of(holder, "value"))
    _check_landings_read(table)
