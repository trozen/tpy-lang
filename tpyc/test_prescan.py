"""Tests for prescan fact-kill collection (collect_fact_kills), the
may-hold relation fact kills consult (collect_in_place_writes) and the
loop bindings (loop_bindings_of) -- the three views over one write
summary."""

from types import SimpleNamespace

import pytest

from .identity_map import IdentityMap
from .parse import (Parser, TpyCall, TpyCoerce, TpyFieldAccess, TpyForEach,
                    TpyIf, TpyMethodCall, TpyNestedDef, TpyStmt, TpyWhile,
                    TpyWith)
from .parse.nodes import become_method_call
from .prescan import (_HOLD_BUDGET, _KEY_DEPTH, _NAME_BUDGET, InPlaceWrites,
                      closure_exports, collect_fact_kills,
                      collect_in_place_writes, loop_bindings_of)
from .sema.narrowing import NarrowingTracker
from .sema.value_range import ValueRange
from .typesys import INT32, STR


def _body_of(source: str, func: str = "f"):
    module = Parser().parse(source)
    for fn in module.functions:
        if fn.name == func:
            return fn.body
    raise AssertionError(f"function {func} not found")


def _kills(source: str):
    return collect_fact_kills(_body_of(source))


class TestCollectFactKills:
    def test_plain_assign_and_decl(self):
        k = _kills("def f():\n    x = 1\n    x = 2\n    y: int32 = 3\n")
        assert {"x", "y"} <= k.names

    def test_aug_assign(self):
        k = _kills("def f(i: int32):\n    i += 1\n")
        assert "i" in k.names

    def test_calls_bit(self):
        assert _kills("def f():\n    g()\n").calls
        assert _kills("def f(o: int32):\n    x = o.m()\n").calls
        assert _kills("def f(c: bool):\n    if c:\n        x = h(1)\n").calls
        assert not _kills("def f(i: int32):\n    i += 1\n    x = i\n").calls
        # A nested def's body does not run where it is defined.
        assert not _kills("def f():\n    def k():\n        g()\n").calls

    def test_field_write_is_path_not_name(self):
        k = _kills("from tpy import *\ndef f(h: int32):\n    h.opt = None\n")
        assert "h.opt" in k.paths
        assert "h" not in k.names

    def test_subscript_write_is_receiver(self):
        k = _kills("from tpy import *\ndef f(xs: list[int32]):\n    xs[0] = 1\n")
        assert "xs" in k.receivers
        assert "xs" not in k.names

    def test_walrus_in_expr(self):
        k = _kills("from tpy import *\ndef f(g: int32):\n    print((p := g))\n")
        assert "p" in k.names

    def test_for_with_unpack_del_targets(self):
        src = (
            "from tpy import *\ndef f(items: list[int32], cm: int32):\n"
            "    for v in items:\n"
            "        pass\n"
            "    a, b = items[0], items[1]\n"
            "    with cm as m:\n"
            "        pass\n"
            "    del a\n"
        )
        k = _kills(src)
        assert {"v", "a", "b", "m"} <= k.names

    def test_method_call_receiver_and_args(self):
        k = _kills("from tpy import *\ndef f(xs: list[int32], ys: int32):\n    xs.append(ys)\n")
        assert "xs" in k.receivers
        assert "ys" in k.receivers
        assert "xs" not in k.names

    def test_nested_def_nonlocal_targets(self):
        src = (
            "def f():\n"
            "    p = 1\n"
            "    def inner():\n"
            "        nonlocal p\n"
            "        p = 2\n"
            "        q = 3\n"
        )
        k = _kills(src)
        assert "p" in k.names
        # Nested-def locals stay out of the enclosing kill-set.
        assert "q" not in k.names

    def test_recurses_into_sub_bodies(self):
        src = (
            "def f(c: bool):\n"
            "    if c:\n"
            "        while c:\n"
            "            x = 1\n"
        )
        k = _kills(src)
        assert "x" in k.names

    def test_call_operands_expand_to_their_objects(self):
        k = _kills("def f(a: N, b: N, t: N, c: bool):\n"
                   "    clear_m(a.inner)\n"
                   "    (a if c else b).reset()\n"
                   "    clear_m(m=t.inner)\n")
        assert {"a.inner", "a", "b", "t.inner"} <= k.receivers
        assert not k.paths

    def test_field_store_records_its_store(self):
        k = _kills("def f(a: N):\n    a.v = 1\n    a.v += 1\n    del a.v\n")
        stores = k.paths["a.v"]
        assert len(stores) == 3 and stores[0] is not None
        assert stores[1:] == [None, None]

    def test_reads_do_not_kill(self):
        k = _kills("from tpy import *\ndef f(p: int32, q: int32):\n    print(p.x + q.y)\n")
        assert not k.names
        assert not k.paths
        # print(p...) passes no bare names mutably here; field reads only.
        assert "p" not in k.receivers


class TestSuspendsFlag:
    def test_yield_sets_suspends(self):
        k = _kills("def f():\n    yield 1\n")
        assert k.suspends

    def test_nested_yield_in_loop_sets_suspends(self):
        k = _kills("def f(n: int32):\n    for i in range(n):\n        yield i\n")
        assert k.suspends

    def test_await_in_decl_init_sets_suspends(self):
        k = _kills("def f():\n    x = await g()\n")
        assert k.suspends

    def test_async_with_sets_suspends_without_body_suspension(self):
        k = _kills("def f(m: int32):\n    async with m:\n        pass\n")
        assert k.suspends

    def test_nested_def_does_not_count(self):
        # Suspension-carrying nested defs are parse-rejected outright, so a
        # plain one is the only representable shape; it must not set the flag.
        k = _kills("def f():\n    def inner():\n        return 1\n    x = 1\n")
        assert not k.suspends

    def test_plain_body_does_not_suspend(self):
        k = _kills("def f():\n    x = 1\n")
        assert not k.suspends


def _parse(body: str, params: str = "a: N, b: N, c: bool") -> list[TpyStmt]:
    src = ("class N:\n    v: int | None\n    inner: N\n    next: N\n"
           "    m: N\n\n"
           f"def f({params}) -> None:\n" + body)
    module = Parser().parse(src, "m")
    return next(fn for fn in module.functions if fn.name == "f").body


def _rel(body: str, params: str = "a: N, b: N, c: bool") -> InPlaceWrites:
    return collect_in_place_writes(_parse(body, params))


def _bound(bound: dict[str, set[str]]) -> InPlaceWrites:
    return InPlaceWrites.from_bindings({}, bound)


class TestMayHold:
    def test_ternary_is_directed(self):
        r = _rel("    t = a if c else N()\n")
        assert r.holds("t") == {"t", "a"}
        assert r.holds("a") == {"a"}

    def test_ternary_holds_both_arms(self):
        r = _rel("    t = a if c else b\n")
        assert r.holds("t") == {"t", "a", "b"}

    def test_or_holds_both_arms(self):
        r = _rel("    t = b or a\n")
        assert r.holds("t") == {"t", "b", "a"}

    def test_field_chain_through_alias(self):
        r = _rel("    b = a\n    t = b.inner\n")
        assert r.holds("t") == {"t", "b.inner", "a.inner"}
        assert r.holds("b") == {"b", "a"}

    def test_deep_chain_is_exact(self):
        r = _rel("    t = a.b.c.d.e\n")
        assert r.holds("t") == {"t", "a.b.c.d.e"}

    def test_walrus(self):
        r = _rel("    print((t := a).v)\n")
        assert r.holds("t") == {"t", "a"}

    def test_nested_def_binding_counts_in_outer_body(self):
        r = _rel("    def k() -> None:\n        u = a\n    k()\n")
        assert r.holds("u") == {"u", "a"}

    def test_reassigned_alias_holds_every_binding(self):
        r = _rel("    h2 = h\n    h2 = other\n", "h: N, other: N")
        assert r.holds("h2") == {"h2", "h", "other"}

    def test_copy_cycle_stays_exact(self):
        r = _rel("    x = a\n    y = x\n    x = y\n")
        assert r.holds("x") == {"x", "y", "a"}
        assert r.holds("y") == {"x", "y", "a"}


class TestCycleCut:
    # A projection between names that bind each other is not composed.
    # BUGS.md#pointer-structure-aliases-unmodelled
    def test_walk_holds_its_start_only(self):
        r = _rel("    node = head\n    node = node.next\n", "head: N")
        assert r.holds("node") == {"node", "head"}

    def test_mutual_projections_hold_only_themselves(self):
        r = _rel("    x = y.next\n    y = x.next\n", "c: bool")
        assert r.holds("x") == {"x"}
        assert r.holds("y") == {"y"}
        # A copy edge still hands over what it copies.
        r = _rel("    x = y.next\n    y = x.next\n    y = a\n")
        assert r.holds("x") == {"x"}
        assert r.holds("y") == {"y", "a"}

    def test_projection_into_the_component_applies_once(self):
        r = _rel("    node = a.inner\n    node = node.next\n")
        assert r.holds("node") == {"node", "a.inner"}

    def test_copies_inside_the_component_close(self):
        r = _rel("    x = y\n    y = x\n    x = y.next\n    x = a.inner\n")
        assert r.holds("x") == {"x", "y", "a.inner"}
        assert r.holds("y") == {"x", "y", "a.inner"}
        r = _rel("    x = a.inner\n    y = x\n    x = y.next\n")
        assert r.holds("x") == {"x", "a.inner"}
        assert r.holds("y") == {"y", "x", "a.inner"}

    # BUGS.md#pointer-structure-aliases-unmodelled
    def test_walk_store_kills_through_its_start_only(self):
        r = _rel("    node = head\n    node = node.next\n", "head: N")
        facts = {"head.v", "head.next.v", "other.v"}
        assert r.affected_facts("node.v", facts, own=True) == {"head.v"}


def _store_kills(r: InPlaceWrites, store: str, fact: str = "t.v") -> bool:
    return r.affected_facts(store, {fact}, own=True) == {fact}


class TestGuards:
    # A place a guard refuses leaves the name overflowed for its root: the
    # relation then answers "may alias" for anything under that root.
    def test_per_root_budget_overflows_the_root(self):
        fields = sorted(f"a.f{i}" for i in range(_HOLD_BUDGET + 1))
        r = _bound({"t": set(fields[:_HOLD_BUDGET])})
        assert r.holds("t") == {"t", *fields[:_HOLD_BUDGET]}
        assert not r.overflowed("t")
        assert _store_kills(r,fields[0] + ".v")
        assert not _store_kills(r,fields[-1] + ".v")
        r = _bound({"t": set(fields)})
        assert r.holds("t") == {"t", *fields[:_HOLD_BUDGET]}
        assert r.overflowed("t") == {"a"}
        # The refused place: the truncating relation kept `t.v` here.
        assert _store_kills(r,fields[-1] + ".v")
        assert not _store_kills(r,"b.f0.v")

    @pytest.mark.parametrize("per", [9, _HOLD_BUDGET])
    def test_two_roots_are_budgeted_apart(self, per):
        places = {f"{root}.f{i}" for root in "ab" for i in range(per)}
        r = _bound({"t": places})
        assert r.holds("t") == {"t", *places}
        assert not r.overflowed("t")
        assert not _store_kills(r,"a.g.v") and not _store_kills(r,"b.g.v")

    def test_depth_guard_overflows_the_root(self):
        deep = "a" + ".l" * _KEY_DEPTH
        r = _bound({"t": {deep}})
        assert r.holds("t") == {"t", deep}
        assert not r.overflowed("t")
        assert _store_kills(r,deep + ".v") and not _store_kills(r,"a.m.v")
        r = _bound({"t": {deep + ".l"}})
        assert r.holds("t") == {"t"}
        assert r.overflowed("t") == {"a"}
        assert _store_kills(r,deep + ".l.v")
        assert not _store_kills(r,"b.l.v")
        # A projection composing past the depth overflows too.
        r = _bound({"u": {deep}, "t": {"u.l"}})
        assert r.holds("t") == {"t", "u.l"}
        assert r.overflowed("t") == {"a"}
        assert _store_kills(r,deep + ".l.v")

    def test_name_budget_overflows_the_refused_root(self):
        per = 13
        places = sorted(f"r{j}.f{i}" for j in range(_NAME_BUDGET // per + 1)
                        for i in range(per))
        assert len(places) == _NAME_BUDGET + 1
        r = _bound({"t": set(places[:_NAME_BUDGET])})
        assert r.holds("t") == {"t", *places[:_NAME_BUDGET]}
        assert not r.overflowed("t")
        r = _bound({"t": set(places)})
        assert r.holds("t") == {"t", *places[:_NAME_BUDGET]}
        refused = places[-1]
        assert r.overflowed("t") == {refused.split(".")[0]}
        assert _store_kills(r,refused + ".v")
        # Roots held in full stay exact.
        assert not _store_kills(r,"r0.g.v")

    def test_overflow_propagates_over_copies(self):
        fields = {f"a.f{i}" for i in range(_HOLD_BUDGET + 1)}
        r = _bound({"t": fields, "u": {"t"}, "w": {"t.inner"}})
        assert r.overflowed("u") == r.overflowed("w") == {"a"}
        assert _store_kills(r,"a.zz.v", "u.v")
        assert _store_kills(r,"a.zz.v", "w.v")
        assert r.may_share("u", "a.zz") and r.may_share("a.zz", "w")
        assert not r.may_share("u", "b")

    def test_writes_through_an_overflowed_name(self):
        fields = sorted(f"a.f{i}" for i in range(_HOLD_BUDGET + 1))
        writes: dict[str, set[str | None]] = {
            fields[-1]: {"reset"}, "b": {"append"}}
        r = InPlaceWrites.from_bindings(writes, {"t": set(fields)})
        assert r.writes_through("t") == {"reset"}
        r = InPlaceWrites.from_bindings(writes, {"t": set(fields[:-1])})
        assert r.writes_through("t") == set()


class TestClosureCost:
    def test_alias_chain_closes_in_linear_steps(self):
        n = 3000
        r = _closed(_alias_chain(n))
        assert "a0" in r.holds(f"a{n - 1}")
        assert r.close_steps <= 2 * n

    def test_field_chain_closes_in_linear_steps(self):
        n = 2000
        r = _bound({f"a{i}": {f"a{i - 1}.next"} for i in range(1, n)})
        assert len(r.holds(f"a{n - 1}")) == _KEY_DEPTH + 1
        assert "a0" in r.overflowed(f"a{n - 1}")
        assert r.close_steps <= (_KEY_DEPTH + 4) * n

    def test_alias_field_chain_steps_follow_the_result(self):
        n = _NAME_BUDGET
        r = _closed(_alias_field_chain(n))
        assert {"b0.inner", f"b{n - 1}.inner"} <= r.holds(f"p{n - 1}")
        size = sum(len(r.holds(f"{x}{i}")) for x in "bp" for i in range(n))
        assert r.close_steps <= size
        # Past the name budget the projections stop composing and overflow.
        n = 300
        r = _closed(_alias_field_chain(n))
        assert len(r.holds(f"p{n - 1}")) == _NAME_BUDGET + 1
        assert "b0" in r.overflowed(f"p{n - 1}")
        assert r.close_steps <= n * (_NAME_BUDGET + 8)

    def test_strongly_connected_group_closes_in_linear_steps(self):
        n = 200
        bound = {f"x{i}": {f"x{(i - 1) % n}.f{i % 8}",
                           f"x{(i * 7 + 3) % n}.f{(i + 3) % 8}"}
                 for i in range(n)}
        bound["x0"].add("r")
        r = _bound(bound)
        assert r.holds("x0") == {"x0", "r"}
        assert r.holds("x1") == {"x1"}
        assert r.close_steps <= 4 * n

    def test_chained_walks_close_in_linear_steps(self):
        n = 40
        r = _chained_walks(n)
        assert {"b0", f"node{n}"} <= r.holds(f"b{n}")
        assert r.close_steps <= 10 * n
        assert r.close_steps <= 2.2 * _chained_walks(n // 2).close_steps

    def test_fan_out_closes_within_the_name_budget(self):
        # `t` holds n roots and m names each project `t.f`: n * m places
        # without the name budget.
        n = m = 800
        bound: dict[str, set[str]] = {"t": {f"a{j}" for j in range(n)}}
        bound.update({f"u{i}": {"t.f"} for i in range(m)})
        r = _bound(bound)
        assert len(r.holds("u0")) == _NAME_BUDGET + 1
        assert len(r.overflowed("u0")) == n + 1
        assert r.close_steps <= 2 * n + m * (_NAME_BUDGET + 4)
        r.query_steps = 0
        assert r.affected_facts(f"a{n - 1}.f.v", ["u0.v"], own=True)
        assert r.query_steps == 1

    def test_inherited_paths_are_counted(self):
        n = 100
        bound: dict[str, set[str]] = {
            "t": {f"a.f{i}" for i in range(_HOLD_BUDGET)}}
        bound.update({f"u{i}": {"t"} for i in range(n)})
        r = _bound(bound)
        assert r.close_steps >= n * _HOLD_BUDGET

    def test_query_is_bounded_by_the_hold_budget(self):
        r = _bound({"node": {"a", "node.next"}
                    | {f"a.f{i}" for i in range(_HOLD_BUDGET)}})
        assert len(r.holds("node")) == _HOLD_BUDGET + 2
        facts = [f"a.f{i}.v" for i in range(_HOLD_BUDGET)] + [
            f"r{i}.v" for i in range(100)] + [
            f"r{i}.inner.w" for i in range(100)]
        r.query_steps = 0
        r.affected_facts("node.v", facts, own=True)
        r.affected_facts("node", facts, own=False)
        assert r.query_steps <= 2 * (len(facts) + _HOLD_BUDGET ** 2)

    def test_paths_under_one_root_cost_linear_lookups(self):
        # Fact and store both hold a full budget of paths under each shared
        # root: one lookup per path, not one per pair of paths.
        roots = "abcd"
        bound = {"x": {f"{r}.f{j}" for r in roots
                       for j in range(_HOLD_BUDGET)},
                 "y": {f"{r}.g{j}" for r in roots
                       for j in range(_HOLD_BUDGET)}}
        r = _bound(bound)
        r.query_steps = 0
        assert not r.affected_facts("y.v", ["x.v"], own=True)
        assert r.query_steps <= 1 + len(roots) * 2 * _HOLD_BUDGET
        assert r.affected_facts("y.v", ["x.f0.v"], own=True) == set()
        bound["y"].add("a.f0")
        r = _bound(bound)
        assert r.affected_facts("y", ["x.v"], own=False) == {"x.v"}

    def test_stores_through_a_copy_cycle_cost_per_fact(self):
        n = 2000
        r = _bound({f"x{i}": {f"x{(i + 1) % n}"} for i in range(n)})
        facts = ["a.v", "x0.v"] + [f"x{i}.inner.v" for i in range(8)]
        r.query_steps = 0
        for i in range(n):
            r.affected_facts(f"x{i}.v", facts, own=True)
        assert r.query_steps <= 2 * n * len(facts)

    def test_unrelated_stores_cost_one_step_per_fact(self):
        # Facts under five roots, stores to a sixth (the shape of a body
        # guarding many fields and then storing elsewhere).
        facts = [f"a{r}.m.f{j}" for r in range(5) for j in range(160)]
        r = _bound({})
        r.query_steps = 0
        for _ in range(1600):
            r.affected_facts("c.g", facts, own=True)
        assert r.query_steps <= 1600 * len(facts)


def _closed(body: str, params: str = "c: bool",
            fields: tuple[str, ...] = ("inner", "l", "r")) -> InPlaceWrites:
    src = ("class N:\n    v: int | None\n"
           + "".join(f"    {name}: N\n" for name in fields)
           + f"\ndef f({params}) -> None:\n{body}")
    stmts = next(fn for fn in Parser().parse(src, "m").functions
                 if fn.name == "f").body
    return collect_in_place_writes(stmts)


def _chained_walks(n: int) -> InPlaceWrites:
    # n walks, each over 4 fields, each starting where the previous ended.
    fields = ("a", "b", "c", "d")

    def step(i: int) -> str:
        e = f"node{i}.d"
        for name in reversed(fields[:-1]):
            e = f"(node{i}.{name} if c else {e})"
        return e
    return _closed("    b0 = N()\n" + "".join(
        f"    node{i} = b{i - 1}\n    while c:\n        node{i} = {step(i)}\n"
        f"    b{i} = node{i}\n" for i in range(1, n + 1)),
        "c: bool", fields)


def _alias_chain(n: int) -> str:
    return "    a0 = N()\n" + "".join(
        f"    a{i} = a{i - 1}\n" for i in range(1, n))


def _alias_field_chain(n: int) -> str:
    return "    b0 = N()\n    p0 = b0.inner\n" + "".join(
        f"    b{i} = b{i - 1}\n    p{i} = b{i}.inner\n" for i in range(1, n))


class TestAffectedFacts:
    def test_store_through_ternary_alias(self):
        r = _bound({"t": {"a"}})
        facts = {"a.v", "a.v.x", "a.w", "t.v"}
        assert r.affected_facts("t.v", facts, own=True) == {"a.v", "a.v.x", "t.v"}
        assert r.affected_facts("t.v", facts, own=False) == {"a.v.x"}

    def test_sibling_field_survives(self):
        r = _bound({"b": {"a"}, "t": {"b.inner"}})
        assert r.holds("t") == {"t", "b.inner", "a.inner"}
        facts = {"a.inner.v", "a.v", "b.inner.v"}
        assert (r.affected_facts("t.v", facts, own=True)
                == {"a.inner.v", "b.inner.v"})

    def test_store_into_part_keeps_the_root_field(self):
        r = _bound({"t": {"a.inner"}})
        assert r.affected_facts("t.v", {"a.v"}, own=True) == set()

    def test_store_through_root_reaches_chain_alias(self):
        r = _bound({"b": {"a"}, "t": {"b.inner"}})
        assert r.affected_facts("a.inner.v", {"t.v", "t.w"}, own=True) == {"t.v"}

    def test_call_on_root_reaches_part_of_alias(self):
        r = _bound({"t": {"a.inner"}})
        assert r.affected_facts("a", {"t.v"}, own=False) == {"t.v"}

    def test_deref_view_sits_beneath_its_receiver(self):
        r = _bound({"t": {"a"}})
        view = "a\x00deref"
        assert r.affected_facts("t", {view}, own=False) == {view}

    def test_store_kills_a_bare_name_holding_the_slot(self):
        # `u = h.payload; h.payload = B()`: `u`'s own narrowing dies.
        r = _bound({"u": {"h.payload"}})
        assert r.affected_facts("h.payload", {"u", "u.v"}, own=True) == {
            "u", "u.v"}

    def test_call_keeps_a_bare_name_fact(self):
        r = _bound({"u": {"h.payload"}})
        assert r.affected_facts("h.payload", {"u", "u.v"}, own=False) == {
            "u.v"}

    def test_unrelated_store_keeps_a_bare_name_fact(self):
        r = _bound({"u": {"h.payload"}})
        assert r.affected_facts("g.payload", {"u", "u.v"}, own=True) == set()
        assert r.affected_facts("h.other", {"u", "u.v"}, own=True) == set()

    def test_copy_keeps_its_own_fact(self):
        # `x = a.v` with `x: int | None`: a store to the slot leaves `x`.
        r = _bound({"x": {"a.v"}})
        copy = {"x"}.__contains__
        assert r.affected_facts("a.v", {"x", "a.v"}, own=True,
                                is_copy=copy) == {"a.v"}
        # Not typed yet: it may be an alias.
        assert r.affected_facts("a.v", {"x", "a.v"}, own=True,
                                is_copy=lambda n: False) == {"x", "a.v"}

    def test_store_through_a_copy_reaches_what_it_shares(self):
        # A copied pointer shares its pointee: `p = h.p; p.x = None`
        # reaches `h.p.x`, and a store to the slot reaches facts beneath it.
        r = _bound({"p": {"h.p"}})
        copy = {"p"}.__contains__
        assert r.affected_facts("p.x", {"h.p.x", "p"}, own=True,
                                is_copy=copy) == {"h.p.x"}
        assert r.affected_facts("h.p", {"p.x", "p"}, own=True,
                                is_copy=copy) == {"p.x"}

    def test_rebind_kills_through_what_the_name_held(self):
        r = _bound({"node": {"head", "node.next"}})
        facts = {"head.v", "other.v", "node.v", "node"}
        assert r.affected_facts("node", facts, own=False) == {
            "head.v", "node.v"}


class TestOverlap:
    # The fact's object, spelled through each of its prefixes' holdings,
    # meets the written place: equal, prefix, or neither.
    FACTS = {"a.x.c.d.e.v", "a.b.c.d.e.v", "a.b.c.d.e", "a.b"}

    def test_key_equal(self):
        r = _bound({"t": {"a.b.c.d.e"}})
        assert r.affected_facts("t.v", self.FACTS, own=True) == {
            "a.b.c.d.e.v"}

    def test_key_prefix(self):
        r = _bound({"t": {"a.b.c.d.e"}})
        assert r.affected_facts("t", self.FACTS, own=True) == {
            "a.b.c.d.e", "a.b.c.d.e.v"}

    def test_incomparable_is_kept(self):
        # `a.x...` is beside the written place, and `a.b` contains it
        # without being its slot.
        r = _bound({"t": {"a.b.c.d.e"}})
        for own in (True, False):
            hit = r.affected_facts("t", self.FACTS, own=own)
            assert "a.x.c.d.e.v" not in hit and "a.b" not in hit


class TestMayShare:
    def test_part_of_shares_both_ways(self):
        r = _rel("    u = a.inner\n")
        assert r.may_share("u", "a")
        assert r.may_share("a", "u")

    # BUGS.md#pointer-structure-aliases-unmodelled
    def test_walk_shares_with_its_start_only(self):
        r = _rel("    node = a\n    node = node.next\n")
        assert r.may_share("node", "a")
        assert not r.may_share("node", "b")

    def test_disjoint_names_do_not_share(self):
        r = _rel("    u = b\n")
        assert not r.may_share("u", "a")
        assert not r.may_share("a", "u")

    def test_writes_through_is_exact(self):
        r = _rel("    q = a.next\n    node = b\n"
                 "    node = node.next\n    node.reset()\n"
                 "    q.inner.reset()\n    st.append(1)\n",
                 "a: N, b: N, st: N")
        assert r.writes_through("st") == {"append"}
        assert r.writes_through("a") == {"reset"}
        assert r.writes_through("b") == {"reset"}


def _first_loop(body: list[TpyStmt]) -> TpyStmt:
    return next(s for s in body if isinstance(s, (TpyWhile, TpyForEach)))


def _loop_facts(loop: TpyStmt):
    return loop_bindings_of(IdentityMap(), loop, IdentityMap())


def _nbody(source: str) -> list[TpyStmt]:
    module = Parser().parse("class N:\n    v: int | None\n\n" + source, "m")
    return next(fn for fn in module.functions if fn.name == "f").body


class TestWriteViews:
    """The three write views agree with one another over one summary."""

    def test_stores_effects_and_deletes(self):
        body = _nbody("def f(c: bool, xs: list[int], i: int, o: N,"
                        " obj: N, y: int):\n"
                        "    while c:\n"
                        "        x = f(y)\n"
                        "        xs[i] = 1\n"
                        "        del o.f\n"
                        "        obj.m()\n")
        loop = _first_loop(body)
        k = collect_fact_kills(loop.body)
        assert k.names == {"x"}
        assert {p: list(ss) for p, ss in k.paths.items()} == {"o.f": [None]}
        assert k.receivers == {"y", "xs", "obj"}
        assert not k.suspends
        facts = _loop_facts(loop)
        assert facts.body == {"x"} and facts.target == set()
        xs_store = loop.body[1].target
        del_target = loop.body[2].targets[0]
        assert [id(n) for n in facts.stores] == [id(xs_store), id(del_target)]
        assert [id(n) for n in facts.deletes] == [id(del_target)]
        assert [id(n) for n in facts.effects] == [
            id(loop.body[0].init), id(loop.body[3].expr)]

    def test_tuple_loop_target_holds_both_and_stores_a_path(self):
        body = _nbody("def f(a: N, b: N):\n"
                        "    for t in (a, b):\n"
                        "        t.v = None\n")
        rel = collect_in_place_writes(body)
        assert {"a", "b"} <= rel.holds("t")
        loop = _first_loop(body)
        k = collect_fact_kills(loop.body)
        assert {p: [id(s) for s in ss] for p, ss in k.paths.items()} == {
            "t.v": [id(loop.body[0])]}
        assert _loop_facts(loop).target == {"t"}

    def test_nested_def_in_a_loop(self):
        body = _nbody("def f(c: bool, o: N):\n"
                        "    x: int | None = 1\n"
                        "    while c:\n"
                        "        def k():\n"
                        "            nonlocal x\n"
                        "            x = None\n"
                        "            y = 2\n"
                        "            o.f = 1\n"
                        "            o.clear()\n"
                        "        k()\n")
        loop = _first_loop(body)
        k = collect_fact_kills(loop.body)
        assert "x" in k.names
        # Defining k counts as running it: its writes through o are kills.
        assert "o.f" in k.paths and "o" in k.receivers
        facts = _loop_facts(loop)
        assert facts.body.isdisjoint({"x", "y", "k"})
        # The relation folds a closure's writes into the enclosing
        # namespace (TODO.md "Pre-scan write views: the leftovers" (a)).
        assert "clear" in collect_in_place_writes(body).writes_through("o")

    def test_lambda_body_walrus_binds_the_enclosing_scope(self):
        # BUGS.md#lambda-body-walrus-binds-enclosing-scope
        body = _nbody("def f():\n    g(lambda: (w := 1))\n")
        assert "w" in collect_fact_kills(body).names

    def test_with_target_and_effect(self):
        body = _nbody("def f(c: bool):\n"
                        "    while c:\n"
                        "        with cm() as h:\n"
                        "            pass\n")
        loop = _first_loop(body)
        assert collect_fact_kills(loop.body).names == {"h"}
        with_stmt = loop.body[0]
        assert isinstance(with_stmt, TpyWith)
        assert any(e is with_stmt for e in _loop_facts(loop).effects)

    def test_handler_and_capture_names(self):
        body = _nbody("def f(c: bool, v: int):\n"
                        "    while c:\n"
                        "        try:\n"
                        "            pass\n"
                        "        except ValueError as e:\n"
                        "            pass\n"
                        "        match v:\n"
                        "            case P(x):\n"
                        "                pass\n")
        loop = _first_loop(body)
        assert {"e", "x"} <= _loop_facts(loop).body
        assert {"e", "x"} <= collect_fact_kills(loop.body).names

    def test_property_read_reclassified_after_the_build(self):
        body = _nbody("def f(c: bool, o: N):\n"
                        "    while c:\n"
                        "        print(o.p)\n")
        loop = _first_loop(body)
        table = IdentityMap()
        assert collect_fact_kills(loop.body, table=table).receivers == {"o.p"}
        read = loop.body[0].expr.args[0]
        assert isinstance(read, TpyFieldAccess)
        become_method_call(read, method="p", args=[], fi=None)
        # The kept summary answers from the node's current class.
        assert collect_fact_kills(loop.body, table=table).receivers == {"o"}

    def test_coerced_argument(self):
        body = _nbody("def f(c: bool, a: N):\n"
                        "    while c:\n"
                        "        g(a.b)\n")
        loop = _first_loop(body)
        table = IdentityMap()
        assert collect_fact_kills(loop.body, table=table).receivers == {"a.b"}
        call = loop.body[0].expr
        call.args[0] = TpyCoerce(expr=call.args[0], actual_type=None,
                                 expected_type=None, coercion=None,
                                 context_kind="", context_msg="")
        assert collect_fact_kills(loop.body, table=table).receivers == {"a.b"}

    def test_loop_payload_is_fixed_at_first_ask(self):
        body = _nbody("def f(c: bool, a: N):\n"
                        "    while c:\n"
                        "        g(a)\n")
        loop = _first_loop(body)
        table = IdentityMap()
        first = loop_bindings_of(table, loop)
        call = loop.body[0].expr
        assert isinstance(call, TpyCall)
        call.macro_expansion = TpyCall(func=call.func, args=[])
        assert loop_bindings_of(table, loop) is first
        assert not any(e is call.macro_expansion for e in first.effects)

    def test_summary_follows_an_appended_statement(self):
        body = _nbody("def f(c: bool):\n    x = 1\n")
        table = IdentityMap()
        assert collect_fact_kills(body, table=table).names == {"x"}
        body.append(_nbody("def f():\n    y = 2\n")[0])
        assert collect_fact_kills(body, table=table).names == {"x", "y"}


def _exports(source: str):
    """The exports of the first nested def in `f`."""
    body = _nbody(source)
    nd = next(s for s in body if isinstance(s, TpyNestedDef))
    out = closure_exports(nd)
    return set(out.paths), out.receivers


class TestClosureExports:
    """What running a nested def writes, in its enclosing scope's names."""

    def test_free_root_exports_as_is(self):
        paths, recv = _exports("def f(t: N, xs: list[int]):\n"
                               "    def k():\n"
                               "        t.v = None\n"
                               "        xs.append(1)\n"
                               "        del t.w\n")
        assert paths == {"t.v", "t.w"}
        assert recv == {"xs"}

    def test_nonlocal_root_exports_as_is(self):
        paths, _ = _exports("def f(t: N):\n"
                            "    def k():\n"
                            "        nonlocal t\n"
                            "        t.v = None\n")
        assert paths == {"t.v"}

    def test_parameter_shadowing_an_outer_name_exports_nothing(self):
        paths, recv = _exports("def f(t: N):\n"
                               "    def k(t: N):\n"
                               "        t.v = None\n"
                               "        t.reset()\n")
        assert paths == set() and recv == set()

    def test_own_fresh_local_exports_nothing(self):
        paths, recv = _exports("def f(t: N):\n"
                               "    def k():\n"
                               "        t = N()\n"
                               "        t.v = None\n")
        assert paths == set() and recv == set()

    def test_local_alias_maps_to_the_captured_name(self):
        paths, recv = _exports("def f(t: N):\n"
                               "    def k():\n"
                               "        u = t\n"
                               "        u.v = None\n"
                               "        u.reset()\n")
        assert paths == {"t.v"}
        assert recv == {"t"}

    def test_projection_is_kept(self):
        paths, _ = _exports("def f(t: N):\n"
                            "    def k():\n"
                            "        u = t.inner\n"
                            "        u.v = None\n")
        assert paths == {"t.inner.v"}

    def test_nested_def_reexports_a_grandparent_capture(self):
        paths, _ = _exports("def f(t: N):\n"
                            "    def k():\n"
                            "        def j():\n"
                            "            t.v = None\n"
                            "        j()\n")
        assert paths == {"t.v"}

    def test_nested_def_writing_the_middle_local_exports_nothing(self):
        paths, recv = _exports("def f(u: N):\n"
                               "    def k():\n"
                               "        u = N()\n"
                               "        def j():\n"
                               "            u.v = None\n"
                               "        j()\n")
        assert paths == set() and recv == set()

    def test_lambda_in_the_def_counts_as_the_defs(self):
        _, recv = _exports("def f(xs: list[int]):\n"
                           "    def k():\n"
                           "        g(lambda: xs.append(1))\n")
        assert "xs" in recv

    def test_overflowed_local_exports_every_free_name(self):
        arms = " if c else ".join(f"t.a{i}" for i in range(_HOLD_BUDGET + 1))
        paths, recv = _exports("def f(t: N, o: N, c: bool):\n"
                               "    def k():\n"
                               f"        u = {arms}\n"
                               "        u.v = None\n"
                               "        print(o)\n")
        assert {"t", "o", "c"} <= recv

    def test_element_store_through_a_field_exports_the_root(self):
        # The key rules stop a subscript store's key at the chain's root
        # name: a receiver write beneath `t`, which covers `t.xs`.
        paths, recv = _exports("def f(t: N, i: int, v: int):\n"
                               "    def k():\n"
                               "        t.xs[i] = v\n")
        assert paths == set() and recv == {"t"}

    def test_bare_name_argument_is_a_receiver(self):
        paths, recv = _exports("def f(xs: list[int]):\n"
                               "    def k():\n"
                               "        g(xs)\n")
        assert paths == set() and recv == {"xs"}

    def test_method_receiver_and_arguments_are_exported(self):
        # Syntactic: whether the callee writes is not asked, since even a
        # @readonly method may write what the receiver's pointers reach.
        paths, recv = _exports("def f(o: N, xs: list[int]):\n"
                               "    def k():\n"
                               "        o.peek(xs)\n")
        assert paths == set() and recv == {"o", "xs"}


class TestHiddenCallAtAMeet:
    """A call sema attaches to a node after the summary was built sets the
    `calls` bit the next ask reads."""

    def test_property_setter_call(self):
        body = _nbody("def f(c: bool, o: N):\n"
                      "    while c:\n"
                      "        o.p = 1\n")
        loop = _first_loop(body)
        table = IdentityMap()
        assert not collect_fact_kills(loop.body, table=table).calls
        target = loop.body[0].target
        assert isinstance(target, TpyFieldAccess)
        target.property_setter_call = TpyMethodCall(
            obj=target.obj, method="p", args=[])
        assert collect_fact_kills(loop.body, table=table).calls

    def test_getattr_fallback_call(self):
        body = _nbody("def f(c: bool, o: N):\n"
                      "    while c:\n"
                      "        y = o.x\n")
        loop = _first_loop(body)
        table = IdentityMap()
        assert not collect_fact_kills(loop.body, table=table).calls
        read = loop.body[0].init
        assert isinstance(read, TpyFieldAccess)
        read.dyn_getattr_call = TpyMethodCall(
            obj=read.obj, method="__getattr__", args=[])
        assert collect_fact_kills(loop.body, table=table).calls


def _condition(source: str):
    """The condition of the first `if` in `f`."""
    return next(s for s in _nbody(source) if isinstance(s, TpyIf)).condition


def _tracker(*, receivers: set[str] = frozenset(),
             paths: dict | None = None) -> NarrowingTracker:
    """A tracker over a bare function state holding only the closure
    exports and an empty may-hold relation."""
    func = SimpleNamespace(closure_written_names=set(),
                           closure_mutated_receivers=set(receivers),
                           closure_mutated_paths=dict(paths or {}),
                           in_place_writes=_bound({}),
                           current_scope=None)
    tracker = NarrowingTracker.__new__(NarrowingTracker)
    tracker.ctx = SimpleNamespace(func=func)
    tracker._module_has_rebindable_globals = lambda: False
    return tracker


_GROW_COND = ("def f(i: int, xs: list[int]):\n"
              "    if i < len(xs) and grow():\n"
              "        pass\n")


class TestStripCallUnstable:
    """Condition facts a call in the same condition may falsify through a
    closure's writes to captured storage."""

    def test_len_range_of_an_exported_receiver_is_dropped(self):
        cond = _condition(_GROW_COND)
        facts = ({"i": ValueRange(lo=0, hi_len_of="xs")}, {})
        t, _ = _tracker(receivers={"xs"})._strip_call_unstable(facts, cond)
        assert t == {}
        t, _ = _tracker(receivers={"ys"})._strip_call_unstable(facts, cond)
        assert set(t) == {"i"}

    def test_no_call_in_the_condition_keeps_everything(self):
        cond = _condition("def f(i: int, xs: list[int]):\n"
                          "    if i < len(xs):\n"
                          "        pass\n")
        facts = ({"i": ValueRange(lo=0, hi_len_of="xs")}, {})
        t, _ = _tracker(receivers={"xs"})._strip_call_unstable(facts, cond)
        assert set(t) == {"i"}

    def test_receiver_kills_beneath_only(self):
        cond = _condition(_GROW_COND)
        facts = ({"t": INT32, "t.v": INT32, "u.v": INT32}, {"t.w": INT32})
        t, f = _tracker(receivers={"t"})._strip_call_unstable(facts, cond)
        assert set(t) == {"t", "u.v"} and f == {}

    def test_ptr_null_set_path(self):
        cond = _condition(_GROW_COND)
        facts = ({"t.p", "u.p"}, {"t.q"})
        t, f = _tracker(receivers={"t"})._strip_call_unstable(facts, cond)
        assert t == {"u.p"} and f == set()
        # A store keeping its slot non-None says nothing of a pointer.
        t, _ = _tracker(paths={"t.p": INT32})._strip_call_unstable(
            facts, cond)
        assert t == {"u.p"}

    def test_stored_path_kills_at_and_beneath(self):
        cond = _condition(_GROW_COND)
        facts = ({"o.name": STR, "o.name.x": INT32, "o": INT32}, {})
        t, _ = _tracker(paths={"o.name": None})._strip_call_unstable(
            facts, cond)
        assert set(t) == {"o"}

    def test_stored_path_whose_stores_keep_the_slot(self):
        cond = _condition(_GROW_COND)
        facts = ({"o.name": STR, "o.name.x": INT32},
                 {"o.name": INT32})
        t, f = _tracker(paths={"o.name": STR})._strip_call_unstable(
            facts, cond)
        # The fact saying what every store keeps survives; one beneath the
        # slot, or of another type, does not -- each side on its own.
        assert set(t) == {"o.name"} and f == {}
