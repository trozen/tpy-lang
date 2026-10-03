"""Tests for prescan fact-kill collection (collect_fact_kills) and the
may-hold relation fact kills consult (collect_in_place_writes)."""

import pytest

from .parse import Parser, TpyStmt
from .prescan import (_HOLD_BUDGET, _KEY_DEPTH, _NAME_BUDGET, InPlaceWrites,
                      collect_fact_kills,
                      collect_in_place_writes)


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
