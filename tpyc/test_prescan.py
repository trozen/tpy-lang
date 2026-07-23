"""Tests for prescan fact-kill collection (collect_fact_kills)."""

from .parse import Parser
from .prescan import alias_group, collect_fact_kills


class TestAliasGroup:
    def test_chain_and_fanout(self):
        aliases = {"b": "a", "c": "b", "d": "a", "x": "y"}
        assert alias_group(aliases, "c") == {"a", "b", "c", "d"}
        assert alias_group(aliases, "a") == {"a", "b", "c", "d"}
        assert alias_group(aliases, "x") == {"x", "y"}

    def test_no_aliases(self):
        assert alias_group({}, "a") == {"a"}


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
        k = _kills("def f():\n    x = 1\n    x = 2\n    y: Int32 = 3\n")
        assert {"x", "y"} <= k.names

    def test_aug_assign(self):
        k = _kills("def f(i: Int32):\n    i += 1\n")
        assert "i" in k.names

    def test_field_write_is_path_not_name(self):
        k = _kills("from tpy import *\ndef f(h: Int32):\n    h.opt = None\n")
        assert "h.opt" in k.paths
        assert "h" not in k.names

    def test_subscript_write_is_receiver(self):
        k = _kills("from tpy import *\ndef f(xs: list[Int32]):\n    xs[0] = 1\n")
        assert "xs" in k.receivers
        assert "xs" not in k.names

    def test_walrus_in_expr(self):
        k = _kills("from tpy import *\ndef f(g: Int32):\n    print((p := g))\n")
        assert "p" in k.names

    def test_for_with_unpack_del_targets(self):
        src = (
            "from tpy import *\ndef f(items: list[Int32], cm: Int32):\n"
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
        k = _kills("from tpy import *\ndef f(xs: list[Int32], ys: Int32):\n    xs.append(ys)\n")
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

    def test_reads_do_not_kill(self):
        k = _kills("from tpy import *\ndef f(p: Int32, q: Int32):\n    print(p.x + q.y)\n")
        assert not k.names
        assert not k.paths
        # print(p...) passes no bare names mutably here; field reads only.
        assert "p" not in k.receivers


class TestSuspendsFlag:
    def test_yield_sets_suspends(self):
        k = _kills("def f():\n    yield 1\n")
        assert k.suspends

    def test_nested_yield_in_loop_sets_suspends(self):
        k = _kills("def f(n: Int32):\n    for i in range(n):\n        yield i\n")
        assert k.suspends

    def test_await_in_decl_init_sets_suspends(self):
        k = _kills("def f():\n    x = await g()\n")
        assert k.suspends

    def test_async_with_sets_suspends_without_body_suspension(self):
        k = _kills("def f(m: Int32):\n    async with m:\n        pass\n")
        assert k.suspends

    def test_nested_def_does_not_count(self):
        # Suspension-carrying nested defs are parse-rejected outright, so a
        # plain one is the only representable shape; it must not set the flag.
        k = _kills("def f():\n    def inner():\n        return 1\n    x = 1\n")
        assert not k.suspends

    def test_plain_body_does_not_suspend(self):
        k = _kills("def f():\n    x = 1\n")
        assert not k.suspends
