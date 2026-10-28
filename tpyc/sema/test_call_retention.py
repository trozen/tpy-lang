"""The one retention verdict (`callee_retains_arg`): whether a callee keeps an
argument past the call, read off a body-less binding's signature and binding
keywords -- the result channel, the copies channel, never the address of a
plain parameter -- and off a bodied callee's measured facts.

The case corpus pins the verdict through what it admits (a getter read off a
temporary at a non-retaining argument); these pin the per-channel answer at
signatures no case needs to spell, and which arguments a kept view parameter
refuses, each shape asserted on its own.
"""
from collections.abc import Iterator

import pytest

from ..compilation_context import activate_compiler
from ..parse import (TpyCall, TpyCoerce, TpyExpr, TpyExprStmt, TpyMethodCall,
                     walk_body_stmts)
from ..thir.testutil import (_assert_byte_identical, _compile, _entry,
                             _strict_reject)
from ..thir.lower.checks import arg_retention
from ..thir.lower.expressions import _arg_holds_dying
from ..value_category import lends_from_dying_source
from .call_lends import Retention, callee_retains_arg, retention_channel

_SRC = '''
from typing import Iterator, overload
from tpy import Span, StrView, ValueType, int32, pure, readonly
from tpy.extern import native


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def walk(self, k: int32) -> Iterator[int32]:
        yield self.n + k


@native("::Holder")
class Holder:
    @native("get")
    def get(self, i: int32) -> Box: ...


@native("::Tok")
class Tok(ValueType):
    s: StrView
    n: int32

    def __init__(self, s: StrView, n: int32) -> None: ...


@pure
@native("pick", borrows=("a",))
def pick_a(a: StrView, b: StrView) -> StrView: ...


@native("pick_any")
def pick_any(a: StrView, b: StrView) -> StrView: ...


@native("pick_box", borrows=("a",))
def pick_box(a: Box, b: Box) -> Box: ...


@native("span_of", borrows=("xs",))
def span_of(xs: readonly[list[int32]]) -> Span[readonly[int32]]: ...


@native("gkeep")
def gkeep[T](x: T, y: T = T()) -> T: ...


@native("tlen", transient=True)
def tlen(v: StrView) -> int32: ...


@native("slen")
def slen(v: StrView) -> int32: ...


@native("total")
def total(*xs: int32) -> int32: ...


@native("keep_views")
def keep_views(*xs: StrView) -> None: ...


@overload
def over(x: StrView) -> int32: ...
@overload
def over(x: int32) -> int32: ...
def over(x: StrView | int32) -> int32:
    return 1


def subject(acc: list[Box], xs: list[Box], b: Box, vs: list[StrView],
            ws: list[StrView], v: StrView, d: dict[StrView, int32],
            e: dict[StrView, int32], ss: set[int32], tt: set[int32],
            names: list[str], nums: list[int32], h: Holder) -> None:
    acc.extend(xs)
    vs.extend(ws)
    acc.append(b)
    vs.append(v)
    d.update(e)
    ss.__ior__(tt)
    "-".join(names)
    sum(nums)
    min(nums)
    pick_a(v, v)
    pick_any(v, v)
    list(ws)
    tlen(v)
    slen(v)
    over(v)
    h.get(0)
    Tok(v, 1)
    gkeep(names[0])
    w = "ab"
    gkeep(w)
    gkeep(v)
    total(1, 2)
    keep_views(*ws)
    pick_box(b, b)
    span_of(nums)
    b.walk(1)
'''


@pytest.fixture(scope="module")
def calls() -> Iterator[tuple[object, dict[str, TpyExpr]]]:
    compiler, modules = _compile(_SRC)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    found: dict[str, TpyExpr] = {}

    def keep(s: object) -> None:
        if isinstance(s, TpyExprStmt) and isinstance(
                s.expr, (TpyCall, TpyMethodCall)):
            found[f"{s.loc.line}"] = s.expr
    walk_body_stmts(fn.body, lambda e: None, keep)
    by_line = sorted(found.items(), key=lambda kv: int(kv[0]))
    labels = ["extend_box", "extend_view", "append_box", "append_view",
              "dict_update_view", "set_ior", "join", "sum", "min",
              "pick_a", "pick_any", "list_views", "transient", "plain",
              "overload", "ref_result", "ctor", "generic_str",
              "generic_pending", "generic_view", "total_ints",
              "keep_spread", "pick_box", "span", "gen_method"]
    assert len(by_line) == len(labels)
    with activate_compiler(compiler):
        yield entry.analyzer.ctx, dict(zip(labels, (c for _l, c in by_line)))


def _retains(case, label: str, index: int) -> bool:
    ctx, found = case
    call = found[label]
    return callee_retains_arg(call.resolved_function_info, index, call,
                              ctx.get_expr_type)


@pytest.mark.parametrize("label,index,want", [
    # Copies channel: a Box holds no pointer, a StrView is one.
    ("extend_box", 0, False),
    ("extend_view", 0, True),
    ("append_box", 0, False),
    ("append_view", 0, True),
    ("dict_update_view", 0, True),
    ("set_ior", 0, False),
    # `@pure` keeps nothing; an owned `str` result holds no pointer.
    ("join", 0, False),
    ("sum", 0, False),
    # A declared borrow at a value-shaped instantiation is a fresh value.
    ("min", 0, False),
    # The result channel is never suppressed: `borrows=` names `a` alone.
    ("pick_a", 0, True),
    ("pick_a", 1, False),
    # An undeclared free view result may read either operand.
    ("pick_any", 0, True),
    ("pick_any", 1, True),
    # An owned result holding pointers carries its operands' borrows.
    ("list_views", 0, True),
    ("transient", 0, False),
    ("plain", 0, True),
    # A reference result (`-> Box` rendered `Box&`) borrows the receiver.
    ("ref_result", -1, True),
    ("ref_result", 0, False),
    # A constructor's object keeps a view argument, not a number.
    ("ctor", 0, True),
    ("ctor", 1, False),
    # A `T` slot instantiated at `str` is a `str` parameter, whose view a
    # binding never keeps; at a spelled `StrView` it is the view.
    ("generic_str", 0, False),
    ("generic_str", 1, False),
    ("generic_pending", 0, False),
    ("generic_pending", 1, False),
    ("generic_view", 0, True),
    # A `*args` pack's array is never kept; its elements follow the copies
    # channel: numbers hold no pointer, views are pointers.
    ("total_ints", 0, False),
    ("keep_spread", 0, True),
])
def test_stub_channels(calls, label: str, index: int, want: bool) -> None:
    assert _retains(calls, label, index) is want


def test_result_and_copies_are_both_reported(calls) -> None:
    # One verdict, every channel: a binding whose result keeps the view may
    # also keep a copy of it, unless `@pure` promises it keeps nothing.
    ctx, found = calls
    for label, want in (("pick_any", Retention.RESULT | Retention.COPIES),
                        ("pick_a", Retention.RESULT)):
        call = found[label]
        assert retention_channel(call.resolved_function_info, 0, call,
                                 ctx.get_expr_type) == want


@pytest.mark.parametrize("label,want", [
    # A declared borrow whose value form holds no pointer: an argument that
    # lends nothing leaves a result that keeps nothing of it.
    ("pick_box", Retention.DECLARED_RESULT),
    # A declared VIEW result is the pointer itself, value or not.
    ("pick_a", Retention.RESULT),
    ("span", Retention.RESULT),
])
def test_declared_result_channel(calls, label: str, want: Retention) -> None:
    ctx, found = calls
    call = found[label]
    assert retention_channel(call.resolved_function_info, 0, call,
                             ctx.get_expr_type) == want


def test_overload_stub_backed_by_a_body_answers_from_its_facts(calls) -> None:
    # A `typing.overload` stub is no binding: the implementation's measured
    # facts answer, and none measured yet retains (fail closed).
    fi = calls[1]["overload"].resolved_function_info
    root = fi.root
    saved = root.return_borrows_from
    try:
        root.return_borrows_from = frozenset()
        assert not _retains(calls, "overload", 0)
        root.return_borrows_from = frozenset({0})
        assert _retains(calls, "overload", 0)
        root.return_borrows_from = None
        assert _retains(calls, "overload", 0)
    finally:
        root.return_borrows_from = saved


def test_a_frame_keeps_its_receiver(calls) -> None:
    # A generator method's frame holds `self` though no measured fact names
    # it (BUGS.md#generator-method-receiver-borrow-unrepresented); a
    # pointer-free argument it copies in is not kept.
    assert _retains(calls, "gen_method", -1)
    assert not _retains(calls, "gen_method", 0)


def test_unknown_callee_retains() -> None:
    assert callee_retains_arg(None, 0)


_DYING_SRC = '''
from tpy import Own, StrView, int32
from tpy.extern import native


@native("::Sink")
class Sink:
    @native("push_back")
    def stash(self, text: StrView) -> None: ...

    @native("emplace_back", borrows=("text",))
    def echo(self, text: StrView) -> StrView: ...

    @native("push_all")
    def stash_all(self, *xs: StrView) -> None: ...


class Child:
    text: str

    def __init__(self) -> None:
        self.text = "abcdef"


class H:
    text: str
    names: list[str]
    child: Child

    def __init__(self) -> None:
        self.text = "abcdef"
        self.names = ["ab"]
        self.child = Child()

    @property
    def ch(self) -> Child:
        return self.child


def mk() -> Own[H]:
    return H()


def make_str() -> str:
    return "abc"


G = "global"


class Keeper:
    text: str

    def __init__(self) -> None:
        self.text = "abc"

    def subject(self, sink: Sink, p: str, c: bool,
                views: list[StrView]) -> None:
        name = "a"
        sink.stash(mk().text)
        sink.echo(mk().text)
        sink.stash(mk().ch.text)
        sink.stash(StrView(mk().text))
        sink.stash(make_str())
        sink.stash(mk().text.strip())
        sink.stash(StrView(mk().text) if c else StrView(name))
        sink.stash(mk().names[0])
        sink.stash(name)
        sink.stash(p)
        sink.stash(self.text)
        sink.stash(G)
        sink.stash("lit")
        sink.stash_all(name, StrView(mk().text))
        sink.stash_all(*views)
'''


@pytest.fixture(scope="module")
def dying_verdicts() -> dict[str, 'tuple[Retention | None, bool]']:
    # Asked right after the compile: the per-test reset clears the
    # compilation's registries the verdict reads.
    compiler, modules = _compile(_DYING_SRC)
    entry = _entry(modules)
    rec = next(r for r in entry.ast.records if r.name == "Keeper")
    fn = next(m for m in rec.methods if m.name == "subject")
    found: dict[int, TpyMethodCall] = {}

    def keep(s: object) -> None:
        if isinstance(s, TpyExprStmt) and isinstance(s.expr, TpyMethodCall):
            found[s.loc.line] = s.expr
    walk_body_stmts(fn.body, lambda e: None, keep)
    labels = ["view_of_temp_field", "kept_by_result_too", "getter_field",
              "explicit_view", "fresh_str", "strip", "conditional_view",
              "temp_list_element", "local", "param", "self_field",
              "global", "literal", "pack_view_of_temp",
              "pack_spread_local"]
    assert len(found) == len(labels)
    analyzer = entry.analyzer
    verdicts = {}
    with activate_compiler(compiler):
        for label, line in zip(labels, sorted(found)):
            call = found[line]
            fi = call.resolved_function_info
            arg = call.args[0]
            keep_arg = arg_retention(fi, 0, call, analyzer, arg)
            verdicts[label] = (keep_arg, _arg_holds_dying(
                arg, fi.params[0].type, keep_arg, analyzer))
    return verdicts


@pytest.mark.parametrize("label,want", [
    # A kept view parameter given a source a `StrView` local could not
    # bind: the temporary's storage dies with the statement.
    ("view_of_temp_field", True),
    ("kept_by_result_too", True),
    ("getter_field", True),
    ("explicit_view", True),
    ("fresh_str", True),
    ("strip", True),
    ("conditional_view", True),
    ("temp_list_element", True),
    # A method's `*args` pack: an element viewing the temporary.
    ("pack_view_of_temp", True),
    # Storage that outlives the statement.
    ("local", False),
    ("param", False),
    ("self_field", False),
    ("global", False),
    ("literal", False),
    # A spread of a list the caller holds: copies of views that outlive it.
    ("pack_spread_local", False),
])
def test_kept_view_parameter_refuses_a_temporary(dying_verdicts, label: str,
                                                 want: bool) -> None:
    keep, dying = dying_verdicts[label]
    assert keep is not None
    assert dying is want


_LOWERED_SRC = '''
from tpy import Own, Ptr, StrView, ValueType, int32, readonly
from tpy.extern import cpp_template, native


class ValSub(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


@native("std::vector<std::string_view>", indirecting=True)
class Sink:
    def __init__(self) -> None: ...

    @native("push_all")
    def stash_all(self, *xs: StrView) -> None: ...

    @native("push_ptr")
    def stash_ptr(self, p: Ptr[readonly[ValSub]]) -> None: ...

    @cpp_template("{self}.push_back({0})")
    def __setitem__(self, text: StrView, v: int32) -> None: ...

    @cpp_template("{self}.push_back({0})")
    def __delitem__(self, text: StrView) -> None: ...


class Child:
    vsub: ValSub

    def __init__(self) -> None:
        self.vsub = ValSub(3)


class H:
    text: str
    child: Child

    def __init__(self) -> None:
        self.text = "abcdef"
        self.child = Child()

    @property
    def ch(self) -> Child:
        return self.child


def mk() -> Own[H]:
    return H()


def main() -> None:
    sink = Sink()
    name = "a"
    {stmt}
'''


@pytest.mark.parametrize("stmt", [
    # A method's `*args` pack: the element views the temporary's field.
    "sink.stash_all(name, StrView(mk().text))",
    # A kept `Ptr` to a ValueType field read through a getter off a
    # temporary: the coercion takes the field's address.
    "sink.stash_ptr(mk().ch.vsub)",
    # A store's and a delete's key are the dunder call's argument.
    "sink[mk().text] = 1",
    "del sink[mk().text]",
])
def test_kept_argument_refuses_a_temporary_at_lowering(stmt: str) -> None:
    err, _reasons = _strict_reject(_LOWERED_SRC.replace("{stmt}", stmt))
    assert "arg.lends_from_temporary" in str(err), str(err)


def test_kept_argument_admits_storage_that_outlives_it() -> None:
    _assert_byte_identical(_LOWERED_SRC.replace(
        "{stmt}", "sink[name] = 1\n    del sink[name]\n"
        "    v = mk().ch.vsub\n    print(v.x)"))


_COERCED_SRC = '''
from typing import Any
from tpy import Own, StrView, int32
from tpy.extern import native


@native("::Sink")
class Sink:
    @native("push_back")
    def stash(self, text: StrView) -> None: ...


class Child:
    text: str
    n: int32

    def __init__(self) -> None:
        self.text = "abcdef"
        self.n = 1


class H:
    child: Child

    def __init__(self) -> None:
        self.child = Child()

    @property
    def ch(self) -> Child:
        return self.child


def mk() -> Own[H]:
    return H()


def takes_any(o: Any) -> None:
    print(o)


def subject(sink: Sink) -> None:
    sink.stash(mk().ch.text)
    takes_any(mk().ch.text)
    takes_any(mk().ch.n)
'''


def test_coerced_field_of_dying_storage() -> None:
    # A view of a dying getter's `str` field points into that storage, so
    # the COERCED type decides; a copy into `Any` hands on the field's own
    # value, so the field's type does.
    compiler, modules = _compile(_COERCED_SRC)
    entry = _entry(modules)
    fn = next(f for f in entry.ast.functions if f.name == "subject")
    args: list[TpyExpr] = []

    def keep(s: object) -> None:
        if isinstance(s, TpyExprStmt) and isinstance(
                s.expr, (TpyCall, TpyMethodCall)):
            args.append(s.expr.args[0])
    walk_body_stmts(fn.body, lambda e: None, keep)
    analyzer = entry.analyzer
    with activate_compiler(compiler):
        got = [(a.coercion.name, lends_from_dying_source(analyzer, a))
               for a in args if isinstance(a, TpyCoerce)]
    assert got == [("str_to_strview", True), ("into_any", False),
                   ("into_any", False)]
