"""A subscript is its explicit dunder call: `t[k]` and `t.__getitem__(k)`
(and `t[k] = v` / `t.__setitem__(k, v)`) rank one overload set the same way
and stamp the same winner, with the same receiver constness, and a call no
variant takes says the same about it."""

from __future__ import annotations

import pytest

from ..compiler import CompiledModule
from ..diagnostics import SemanticError
from ..parse.nodes import (SubscriptDunder, TpyCall, TpyFunction,
                           TpyMethodCall, TpyRaise, TpySubscript,
                           walk_body_stmts)
from .overloads import CallShape, _scalar_widening_cost
from ..thir.testutil import _compile, _entry
from ..typesys import (BIGINT, INT32, INT64, NONE, STR, VOID, FunctionInfo,
                       ParamInfo, UnionType, unwrap_ref_type)

HEADER = """\
from tpy import int32, readonly, basic_slice
"""


def _sites(body: str) -> tuple[list[TpySubscript], list[TpyMethodCall]]:
    """The subscripts and the dunder method calls written in `f`, in
    source order."""
    _, modules = _compile(HEADER + body)
    ast = _entry(modules).ast
    fn = next(f for f in ast.functions
              if isinstance(f, TpyFunction) and f.name == "f")
    subs: list[TpySubscript] = []
    calls: list[TpyMethodCall] = []

    def visit(e) -> None:
        if isinstance(e, TpySubscript):
            if not any(e is s for s in subs):
                subs.append(e)
        elif isinstance(e, TpyMethodCall) and e.method.startswith("__"):
            calls.append(e)
        for c in e.children():
            visit(c)

    def on_stmt(s) -> None:
        target = getattr(s, "target", None)
        if isinstance(target, TpySubscript) and not any(
                target is t for t in subs):
            subs.append(target)

    walk_body_stmts(fn.body, visit, on_stmt)
    return subs, calls


def _same_winner(sub: TpySubscript, kind: SubscriptDunder,
                 call: TpyMethodCall) -> None:
    stamped = sub.dunder(kind)
    called = call.resolved_function_info
    assert stamped is not None and called is not None
    assert stamped.root is called.root
    assert stamped.params == called.params
    assert stamped.return_type == called.return_type
    assert (kind in sub.const_dunders) == call.const_receiver


@pytest.mark.parametrize("params, read, call", [
    # A readonly receiver: the readonly twin of the int-index getter.
    ("xs: readonly[list[int32]]", "xs[0]", "xs.__getitem__(0)"),
    # A mutable receiver: the mutable twin.
    ("xs: list[int32]", "xs[1]", "xs.__getitem__(1)"),
    # A slice key: the slice getter, on both receivers.
    ("xs: list[int32], s: basic_slice", "xs[s]", "xs.__getitem__(s)"),
    ("xs: readonly[list[int32]], s: basic_slice", "xs[s]",
     "xs.__getitem__(s)"),
    # A dict key.
    ("d: dict[str, int32]", 'd["a"]', 'd.__getitem__("a")'),
    ("d: readonly[dict[str, int32]]", 'd["a"]', 'd.__getitem__("a")'),
])
def test_read_stamps_the_explicit_calls_winner(params, read, call):
    subs, calls = _sites(f"""
def f({params}) -> None:
    print({read})
    print({call})
""")
    _same_winner(subs[0], SubscriptDunder.GET, calls[0])


@pytest.mark.parametrize("params, store, call", [
    ("xs: list[int32]", "xs[0] = 5", "xs.__setitem__(0, 5)"),
    ("xs: list[int32], s: basic_slice", "xs[s] = [7]",
     "xs.__setitem__(s, [7])"),
    ("d: dict[str, int32]", 'd["a"] = 2', 'd.__setitem__("a", 2)'),
])
def test_store_stamps_the_explicit_calls_winner(params, store, call):
    subs, calls = _sites(f"""
def f({params}) -> None:
    {store}
    {call}
""")
    _same_winner(subs[0], SubscriptDunder.SET, calls[0])


_MEMBERS = """
from tpy import int64


class A:
    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    def __init__(self, y: int32) -> None:
        self.y = y


def wide() -> int64:
    return 7
"""


@pytest.mark.parametrize("params, prelude, store, call", [
    # A member value at a union element: a conversion only the argument
    # receive takes, which the ranking takes too.
    ("xs: list[A | B], p: A", "", "xs[0] = p", "xs.__setitem__(0, p)"),
    # A tuple widening a container literal's open element.
    ("", "xs = [(1, 2)]", "xs[0] = (wide(), 5)",
     "xs.__setitem__(0, (wide(), 6))"),
    # A typed row into a literal whose rows are still open.
    ("r: list[int64]", "xs = [[1], [2]]", "xs[1] = r",
     "xs.__setitem__(0, r)"),
])
def test_store_the_receive_takes_stamps_the_explicit_calls_winner(
        params, prelude, store, call):
    subs, calls = _sites(_MEMBERS + f"""
def f({params}) -> None:
    {prelude}
    {store}
    {call}
""")
    if not prelude:
        _same_winner(subs[0], SubscriptDunder.SET, calls[0])
        return
    # A container literal's variant is substituted at the literal as each
    # call finds it, and the first store decides it: the variant is the one.
    stamped = subs[0].dunder(SubscriptDunder.SET)
    called = calls[0].resolved_function_info
    assert stamped is not None and called is not None
    assert stamped.root is called.root


def test_a_wide_index_narrows_as_the_explicit_call_does():
    """Under `--default-int=int64` an unannotated index local is an int64,
    which the int32 index converts with a range check: the subscript, its
    explicit call and an overloaded method each take the int32 variant."""
    _, modules = _compile(HEADER + """
def f() -> None:
    xs: list[int32] = [1, 2]
    i = 0
    print(xs[i])
    print(xs.__getitem__(i))
    print(xs.pop(i))
""", default_int="int64")
    ast = _entry(modules).ast
    fn = next(f for f in ast.functions
              if isinstance(f, TpyFunction) and f.name == "f")
    subs: list[TpySubscript] = []
    calls: list[TpyMethodCall] = []

    def visit(e) -> None:
        if isinstance(e, TpySubscript):
            subs.append(e)
        elif isinstance(e, TpyMethodCall) and e.method in ("__getitem__",
                                                           "pop"):
            calls.append(e)
        for c in e.children():
            visit(c)

    walk_body_stmts(fn.body, visit, lambda s: None)
    _same_winner(subs[0], SubscriptDunder.GET, calls[0])
    for call in calls:
        assert str(call.resolved_function_info.params[0].type) == "int32"


# --- the applicability filter (`CallAnalyzer.admitted_overloads`) ----------

_TWO_VALUES = """
class R:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    def __setitem__(self, k: int32, v: dict[str, int32]) -> None:
        self.n = 1

    @dispatch
    def __setitem__(self, k: int32, v: list[int32]) -> None:
        self.n = 2


def f() -> None:
    r = R()
    r[0] = {"a": 1}
    r[0] = [2]
    r.__setitem__(0, {"b": 2})
    r.__setitem__(0, [1])
"""


def test_survivors_with_different_value_hints_lend_none():
    """Both variants take the key; their value parameters differ, so neither
    hints the value -- a dict and a list literal each reach their own
    variant, at the subscript and at the explicit call alike."""
    subs, calls = _sites(HEADER.replace("basic_slice", "basic_slice, dispatch")
                         + _TWO_VALUES)
    picked = [str(unwrap_ref_type(s.dunder(SubscriptDunder.SET).params[1].type))
              for s in subs]
    called = [str(unwrap_ref_type(c.resolved_function_info.params[1].type))
              for c in calls]
    assert picked == ["dict[str, int32]", "list[int32]"]
    assert called == picked


_CONVERTED_KEY = """
class G:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    def __setitem__(self, k: int32, v: str) -> None:
        self.n = 1

    @dispatch
    def __setitem__(self, k: int64, v: list[int32]) -> None:
        self.n = 2


def f(i: int32) -> None:
    g = G()
    g[i] = [5]
    g.__setitem__(i, [6])
"""


def test_a_variant_the_key_reaches_by_conversion_stays():
    """An `int32` key matches one variant exactly and reaches the other by
    a conversion: the filter keeps both, so a value only the converting
    variant takes still finds it."""
    src = HEADER.replace("basic_slice", "basic_slice, int64, dispatch")
    subs, calls = _sites(src + _CONVERTED_KEY)
    assert str(subs[0].dunder(SubscriptDunder.SET).params[0].type) == "int64"
    assert str(calls[0].resolved_function_info.params[0].type) == "int64"
    _, modules = _compile(src + _CONVERTED_KEY)
    analyzer = _entry(modules).analyzer
    setters = analyzer.registry.get_record("G").get_method_overloads(
        "__setitem__")
    kept = analyzer.calls.admitted_overloads(setters, CallShape(2),
                                             {0: INT32})
    assert len(kept) == 2


_PENDING_VALUE = """
def f() -> None:
    ws = [0, 0]
    v = 1
    ws[0] = v
    v = 5000000000
    us = [0, 0]
    u = 1
    us.__setitem__(0, u)
    u = 5000000000
    print(ws, us)
"""


def test_a_pending_value_widens_after_an_ambiguous_preparation():
    """A literal-seeded local stored through a call with several `__setitem__`
    variants of its arity (an index and a slice) is not settled by the
    preparation: a later wider store widens it and the list it went into."""
    _, modules = _compile(HEADER + _PENDING_VALUE)
    analyzer = _entry(modules).analyzer
    ast = _entry(modules).ast
    fn = next(f for f in ast.functions if f.name == "f")
    printed: list = []
    walk_body_stmts(fn.body, lambda e: printed.extend(
        a for a in getattr(e, "args", []) if isinstance(e, TpyCall)),
        lambda s: None)
    elems = [analyzer.ctx.get_expr_type(a).get_element_type()
             for a in printed]
    assert [str(t) for t in elems] == ["int", "int"]


# --- one ranking rule at every call kind (`CallRanking`) -------------------

_UNION_PARAM = _MEMBERS + """
from tpy import dispatch


class H:
    @dispatch
    def m(self, v: A | B, k: int32) -> int32:
        return 1

    @dispatch
    def m(self, v: str, k: int32) -> int32:
        return 2


@dispatch
def g(v: A | B, k: int32) -> int32:
    return 1


@dispatch
def g(v: str, k: int32) -> int32:
    return 2


class K:
    n: int32

    @dispatch
    def __init__(self, v: A | B, k: int32) -> None:
        self.n = 1

    @dispatch
    def __init__(self, v: str, k: int32) -> None:
        self.n = 2


def f(h: H, p: A) -> None:
    print(h.m(p, 1))
    print(g(p, 1))
    print(K(p, 1).n)
"""


def _calls_in_f(source: str) -> list:
    _, modules = _compile(source)
    return _calls_in_f_of(modules)


def _calls_in_f_of(modules: list[CompiledModule]) -> list:
    ast = _entry(modules).ast
    fn = next(f for f in ast.functions
              if isinstance(f, TpyFunction) and f.name == "f")
    found: list = []

    def visit(e) -> None:
        if isinstance(e, (TpyCall, TpyMethodCall)) and getattr(
                e, "resolved_function_info", None) is not None:
            found.append(e)
        for c in e.children():
            visit(c)

    walk_body_stmts(fn.body, visit, lambda s: None)
    return found


def test_member_of_a_union_parameter_ranks_alike_at_every_call_kind():
    """A member value at a union parameter, beside a variant that does not
    take the call: the method, the free function and the constructor group
    each pick the union variant (the constructor group is not lowered yet,
    BUGS.md#dispatch-ctor-group-unlowered, so sema's pick is pinned here)."""
    picked = {}
    for call in _calls_in_f(HEADER + _UNION_PARAM):
        fi = call.resolved_function_info
        name = getattr(call, "method", None) or getattr(call, "func_name", "")
        if name in ("m", "g", "K"):
            picked[name] = str(unwrap_ref_type(fi.params[0].type))
    assert picked == {"m": "A | B", "g": "A | B", "K": "A | B"}


def _ranking():
    _, modules = _compile(HEADER + _MEMBERS)
    return _entry(modules).analyzer.calls.ranking()


def test_union_member_is_priced_at_its_best_member():
    """An `int` injected into `None | int32 | str` costs its conversion at
    the `int32` member plus one widening -- so it narrows, as at a bare
    `int32` parameter -- not the flat injection a member at its home costs."""
    ranking = _ranking()
    union = UnionType((NONE, INT32, STR))
    at_member = _scalar_widening_cost(BIGINT, INT32, INT32)
    assert ranking.received(BIGINT, union) == (1, at_member + 1, 0)
    assert ranking.received(INT32, UnionType((INT64, STR))) == (0, 1 + 4, 0)
    assert ranking.received(STR, INT32) is None


def test_union_member_pricing_keeps_the_bare_variant():
    """`e(k)` with `k: int` at `e(c: int32)` / `e(c: None | int32 | str)`:
    both narrow the `int`, and the union variant widens once more, so the
    bare `int32` variant wins (as `sys.exit(k)` picks `exit(code: int32)`)."""
    _, modules = _compile(HEADER + """
from tpy import dispatch


@dispatch
def e(c: int32) -> int32:
    return 1


@dispatch
def e(c: None | int32 | str) -> int32:
    return 2


def f(k: int) -> None:
    print(e(k))
""")
    ast = _entry(modules).ast
    fn = next(f for f in ast.functions
              if isinstance(f, TpyFunction) and f.name == "f")
    found: list = []
    walk_body_stmts(fn.body, lambda e: found.extend(
        a for a in getattr(e, "args", [])
        if isinstance(a, TpyCall) and a.func_name == "e"), lambda s: None)
    assert str(found[0].resolved_function_info.params[0].type) == "int32"


def test_receive_only_cell_loses_to_a_strict_cell():
    """A variant only the argument receive takes ranks in the conversion
    stage, so a variant every argument matches strictly beats it."""
    ranking = _ranking()
    union_v = FunctionInfo(name="u", params=(
        ParamInfo("v", UnionType((STR, BIGINT))),), return_type=VOID)
    strict_v = FunctionInfo(name="u", params=(ParamInfo("v", INT32),),
                            return_type=VOID)
    pick = ranking.rank([union_v, strict_v], [INT32])
    assert pick.winner == 1 and pick.strict == (1,)
    assert ranking.received(INT32, UnionType((STR, BIGINT))) is not None


_UNION_FIRST = """
    @dispatch
    def u(self, x: int | str) -> str:
        return "union"
"""
_FLOAT_FIRST = """
    @dispatch
    def u(self, x: float) -> str:
        return "float"
"""


def _order_source(first: str, second: str) -> str:
    free_first = first.replace("self, ", "").replace("\n    ", "\n")
    free_second = second.replace("self, ", "").replace("\n    ", "\n")
    return (HEADER + "from tpy import dispatch\n\n\nclass V:" + first + second
            + free_first + free_second
            + "\n\ndef f(v: V, k: int32) -> None:\n"
            "    print(u(3), u(k), v.u(3), v.u(k))\n")


@pytest.mark.parametrize("order", ["union-first", "float-first"])
def test_union_member_pick_does_not_depend_on_declaration_order(order):
    """`u(3)` and `u(k)` (`k: int32`) at `u(x: int | str)` / `u(x: float)`
    take the union variant, free function and method alike, whichever
    variant is declared first."""
    first, second = ((_UNION_FIRST, _FLOAT_FIRST) if order == "union-first"
                     else (_FLOAT_FIRST, _UNION_FIRST))
    picked = [str(unwrap_ref_type(c.resolved_function_info.params[0].type))
              for c in _calls_in_f(_order_source(first, second))
              if (getattr(c, "method", None) or getattr(c, "func_name", ""))
              == "u"]
    assert picked == ["int | str"] * 4


def test_raise_takes_the_union_variant_of_its_constructor_group():
    """`raise E(p, 1)` with `p: A` at `__init__(v: A | B, k: int32)` /
    `__init__(v: str, k: int32)` picks the union variant, as the
    constructor call outside a `raise` does."""
    source = HEADER + _MEMBERS + """
from tpy import dispatch


class E(Exception):
    n: int32

    @dispatch
    def __init__(self, v: A | B, k: int32) -> None:
        self.n = 1

    @dispatch
    def __init__(self, v: str, k: int32) -> None:
        self.n = 2


def f(p: A) -> None:
    raise E(p, 1)
"""
    _, modules = _compile(source)
    ast = _entry(modules).ast
    fn = next(f for f in ast.functions
              if isinstance(f, TpyFunction) and f.name == "f")
    raised = [s for s in fn.body if isinstance(s, TpyRaise)]
    ctor = raised[0].raised_construction().resolved_function_info
    assert len(raised) == 1 and ctor is not None
    assert str(unwrap_ref_type(ctor.params[0].type)) == "A | B"


def test_union_variant_argument_renders_at_the_union():
    """`v.b(1)` with `b(x: float)` declared before `b(x: int64 | str)`:
    sema picks the union variant whatever the declaration order, and the
    literal renders at the member sema chose wrapped in the union, so C++
    binds that variant (named `b__int64_str`, the literal kept at the
    union's member)."""
    compiler, modules = _compile(HEADER + """
from tpy import dispatch, int64


class V:
    def __init__(self) -> None:
        pass

    @dispatch
    def b(self, x: float) -> str:
        return "float"

    @dispatch
    def b(self, x: int64 | str) -> str:
        return "union"


def f(v: V) -> None:
    print(v.b(1))
""")
    picked = [str(unwrap_ref_type(c.resolved_function_info.params[0].type))
              for c in _calls_in_f_of(modules) if getattr(c, "method", None)
              == "b"]
    assert picked == ["int64 | str"]
    cpp = compiler.generate_module_sources(_entry(modules)).cpp
    assert ("v.b__int64_str(::tpy::Union<int64_t, std::string>("
            "static_cast<int64_t>(1)))") in cpp


# A container literal as a list index is the key's mismatch, in the words
# any other wrongly-typed key gets (`xs[[]]` is pinned by a case,
# list/error_subscript_container_literal_key).
@pytest.mark.parametrize("key, shown", [
    pytest.param("{}", "dict[???, ???]", id="empty-dict"),
    pytest.param("[1]", "list[int32]", id="list"),
])
def test_a_container_literal_key_is_the_index_mismatch(key: str,
                                                       shown: str) -> None:
    src = HEADER + f"def f() -> None:\n    xs: list[int32] = [1]\n" \
        f"    print(xs[{key}])\n"
    with pytest.raises(SemanticError) as err:
        _compile(src)
    assert str(err.value).endswith(
        "Type mismatch in list index: expected int32 or slice, got "
        + shown), str(err.value)
