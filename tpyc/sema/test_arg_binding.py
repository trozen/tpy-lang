"""A call holds one argument list (`sema/arg_binding.py`): before its first
commit it is the call as written, after it the parameter-ordered layout its
binding describes, and the call as written is read back through the binding
(`source_view`). Pinned here: only the commit and `relayout` change a call's
argument layout, and the source view of every committed call kind is what
the call was written with -- the source a re-entry binds from."""
import ast
import copy
import dataclasses
from pathlib import Path

import pytest

from tpyc.compilation_context import activate_compiler
from tpyc.parse import nodes as N
from tpyc.parse.nodes import (TpyCall, TpyCallLike, TpyExpr, TpyFunction,
                              TpyIntLiteral, TpyMethodCall, TpyName,
                              TpyStrLiteral, walk_body_stmts, walk_expr_tree)
from tpyc.sema import calls as calls_module
from tpyc.sema.calls import CallAnalyzer
from tpyc.sema.methods import MethodAnalyzer
from tpyc.sema.arg_binding import (ArgBinding, CallSource, SlotKind, bind,
                                   relaid_copy, source_view)
from tpyc.thir.testutil import _compile, _entry
from tpyc.typesys import INT32, VOID, FunctionInfo, ParamInfo

_TPYC = Path(__file__).resolve().parent.parent

# The fields that hold a call's arguments, and the list / dict methods that
# change their length or order.
_LAYOUT_FIELDS = frozenset(("args", "kwargs", "double_star_unpack"))
_LAYOUT_METHODS = frozenset(("append", "insert", "extend", "pop", "clear",
                             "remove", "popitem", "update", "setdefault",
                             "sort", "reverse"))
# The functions that may change a call's argument layout.
_LAYOUT_WRITERS = frozenset(("commit_args", "fill_resolved_defaults",
                             "relayout"))


def _layout_field(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr in _LAYOUT_FIELDS


def _layout_writes(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Every write of a call's argument layout in `tree`: `(line, function,
    what)`. Replacing one element (`call.args[i] = x`, `call.kwargs[k] =
    x`) keeps the layout and is not one."""
    found: list[tuple[int, str, str]] = []

    def targets(t: ast.AST):
        if isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                yield from targets(e)
        else:
            yield t

    def visit(node: ast.AST, func: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        written: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            written = [t for tgt in node.targets for t in targets(tgt)]
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            written = [node.target]
        elif isinstance(node, ast.Delete):
            written = [t for tgt in node.targets for t in targets(tgt)]
        for t in written:
            if _layout_field(t):
                found.append((node.lineno, func, f"assigns .{t.attr}"))
            elif (isinstance(t, ast.Subscript) and _layout_field(t.value)
                  and (isinstance(t.slice, ast.Slice)
                       or isinstance(node, (ast.Delete, ast.AugAssign)))):
                found.append((node.lineno, func,
                              f"rewrites a slice of .{t.value.attr}"))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LAYOUT_METHODS
                and _layout_field(node.func.value)):
            found.append((node.lineno, func,
                          f".{node.func.value.attr}.{node.func.attr}()"))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setattr" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in _LAYOUT_FIELDS):
            found.append((node.lineno, func,
                          f"setattr(.., {node.args[1].value!r}, ..)"))
        for child in ast.iter_child_nodes(node):
            visit(child, func)

    visit(tree, "<module>")
    return found


def _compiler_sources() -> list[Path]:
    files = [*sorted((_TPYC / "sema").rglob("*.py")),
             *sorted((_TPYC / "parse").rglob("*.py")),
             _TPYC / "macro_api.py", _TPYC / "macro_loader.py"]
    return [f for f in files if not f.name.startswith("test_")]


def test_only_the_commit_and_relayout_change_an_argument_layout() -> None:
    offenders = []
    for path in _compiler_sources():
        tree = ast.parse(path.read_text(), filename=str(path))
        for line, func, what in _layout_writes(tree):
            if func not in _LAYOUT_WRITERS:
                offenders.append(
                    f"{path.relative_to(_TPYC.parent)}:{line} {func}: {what}")
    assert not offenders, (
        "a call's argument layout is changed outside the commit and "
        "`relayout`:\n  " + "\n  ".join(offenders))


def test_the_scan_sees_each_kind_of_layout_write() -> None:
    src = ("def f(call, x):\n"
           "    call.args = [x]\n"
           "    call.kwargs.pop('k')\n"
           "    call.args[1:] = []\n"
           "    del call.args[0]\n"
           "    setattr(call, 'double_star_unpack', x)\n"
           "    call.args[0] = x\n"
           "    call.kwargs['k'] = x\n")
    whats = [w for _, _, w in _layout_writes(ast.parse(src))]
    assert whats == ["assigns .args", ".kwargs.pop()",
                     "rewrites a slice of .args", "rewrites a slice of .args",
                     "setattr(.., 'double_star_unpack', ..)"]


def test_a_call_node_keeps_no_second_copy_of_its_arguments() -> None:
    assert not hasattr(N, "WrittenArgs")
    for cls in (TpyCall, TpyMethodCall, N.TpyRaise):
        assert "written" not in {f.name for f in dataclasses.fields(cls)}
    assert "written" not in {f.name for f in dataclasses.fields(ArgBinding)}


# One call per kind. The operands are names and literals so a source view
# compares by spelling; `main` holds the calls in this order.
_PROGRAM = """\
from typing import TypedDict, Unpack
from tpy import int32


class Opts(TypedDict):
    s: str
    k: int32


def pos(a: int32, b: int32) -> None:
    print(a, b)


def kw(a: int32, b: int32 = 2, c: int32 = 3) -> None:
    print(a, b, c)


def va(a: int32, *xs: int32, k: int32 = 5) -> None:
    print(a, k)


def kwr(**kw: Unpack[Opts]) -> None:
    print(kw["s"], kw["k"])


class Base:
    def m(self, a: int32, b: int32 = 4) -> None:
        print(a, b)


class D(Base):
    def __init__(self) -> None:
        pass

    def go(self, y: int32) -> None:
        Base.m(self, y, b=9)


def main() -> None:
    x = 1
    y = 2
    pos(x, y)
    kw(x, c=y)
    kw(x)
    va(x, y, 3, k=4)
    va(x)
    kwr(s="a", k=x)
    o = Opts(s="b", k=y)
    kwr(**o)
    D().go(y)


main()
"""

_KINDS = ["positional", "keyword", "defaulted", "varargs+kw-only",
          "varargs-empty", "kwargs-keywords", "double-star",
          "explicit-receiver"]
_WRITTEN = {
    "positional": (("x", "y"), ()),
    "keyword": (("x",), (("c", "y"),)),
    "defaulted": (("x",), ()),
    "varargs+kw-only": (("x", "y", 3), (("k", 4),)),
    "varargs-empty": (("x",), ()),
    "kwargs-keywords": ((), (("s", "a"), ("k", "x"))),
    "double-star": ((), ()),
    "explicit-receiver": (("self", "y"), (("b", 9),)),
}


def _spelled(e: TpyExpr) -> object:
    if isinstance(e, TpyName):
        return e.name
    if isinstance(e, (TpyIntLiteral, TpyStrLiteral)):
        return e.value
    return type(e).__name__


def _received(e: TpyExpr) -> TpyExpr:
    """The operand under the coercions its receive placed."""
    while isinstance(e, N.TpyCoerce):
        e = e.expr
    return e


def _calls(fn: TpyFunction) -> list[TpyCallLike]:
    out: list[TpyCallLike] = []

    def visit(e: TpyExpr) -> bool:
        if isinstance(e, (TpyCall, TpyMethodCall)) and (
                isinstance(e, TpyMethodCall)
                or e.func_name in ("pos", "kw", "va", "kwr")):
            out.append(e)
            return False
        return True

    walk_body_stmts(fn.body, lambda e: walk_expr_tree(e, visit),
                    lambda s: None)
    return out


@pytest.fixture(scope="module")
def committed_calls():
    compiler, modules = _compile(_PROGRAM)
    entry = _entry(modules)
    funcs = {f.name: f for f in entry.ast.functions}
    calls = [c for c in _calls(funcs["main"])
             if isinstance(c, TpyCall)]
    d = next(r for r in entry.ast.records if r.name == "D")
    go = next(m for m in d.methods if m.name == "go")
    calls.append(_calls(go)[0])
    assert len(calls) == len(_KINDS)
    return compiler, entry.analyzer, dict(zip(_KINDS, calls))


@pytest.mark.parametrize("kind", _KINDS)
def test_source_view_after_the_commit_is_the_call_as_written(
        committed_calls, kind) -> None:
    _, _, calls = committed_calls
    call = calls[kind]
    assert call.binding is not None and call.binding.at is not None
    src = source_view(call)
    positional, keywords = _WRITTEN[kind]
    assert tuple(_spelled(a) for a in src.positional) == positional
    assert tuple((k, _spelled(v)) for k, v in src.keywords) == keywords
    if kind == "double-star":
        assert _spelled(src.double_star) == "o"
    else:
        assert src.double_star is None


@pytest.mark.parametrize("kind", _KINDS)
def test_a_copy_of_a_committed_call_is_written_as_its_source(
        committed_calls, kind) -> None:
    compiler, _, calls = committed_calls
    call = calls[kind]
    with activate_compiler(compiler):
        copy = relaid_copy(call, lambda e: e)
    assert copy.binding is None
    assert source_view(copy) == source_view(call)


_SECOND = [ParamInfo("a", INT32),
           ParamInfo("b", INT32, default_expr=TpyIntLiteral(0)),
           ParamInfo("c", INT32, default_expr=TpyIntLiteral(0)),
           ParamInfo("d", INT32, default_expr=TpyIntLiteral(0)),
           ParamInfo("k", INT32, default_expr=TpyIntLiteral(0))]


@pytest.mark.parametrize("kind", ["positional", "keyword", "defaulted",
                                  "varargs+kw-only"])
def test_reentry_binds_a_second_candidate_from_the_source_view(
        committed_calls, kind) -> None:
    """The committed call bound again against another signature binds the
    operands it was written with, not its committed layout; committed and
    received there, it holds them at that signature's slots and is still
    read back as written."""
    compiler, analyzer, calls = committed_calls
    call = calls[kind]
    src = source_view(call)
    binding = bind(src, _SECOND, "g")
    assert isinstance(binding, ArgBinding)
    bound = {_SECOND[i].name: _spelled(e) for i, e in binding.evidence(src)}
    positional, keywords = _WRITTEN[kind]
    assert bound == {**dict(zip("abcd", positional)), **dict(keywords)}

    # The fixture's call stays committed to its own callee.
    again = copy.copy(call)
    again.args = list(call.args)
    second = FunctionInfo(name="g", params=_SECOND, return_type=VOID)
    with activate_compiler(compiler):
        layout = analyzer.calls.commit_args(again, second, binding, {},
                                            analyze=False)
        analyzer.calls.receive_winner_args(
            None, layout.args, None,
            [_SECOND[i] for i in layout.binding.param_order()], second,
            call=again)
    assert again.args is layout.args and again.binding is layout.binding
    held = {_SECOND[i].name: _spelled(_received(again.args[k]))
            for k, i in enumerate(again.binding.param_order())}
    filled = {_SECOND[i].name: 0 for i in layout.filled}
    assert held == {**bound, **filled}
    assert all(binding.slots[i].kind is SlotKind.DEFAULT
               for i in layout.filled)
    assert source_view(again) == src


_PROBED = """\
from tpy import int32, dispatch
from tpy.extern import cpp_template


@cpp_template("({0} + {1})")
def inner[T](x: T, k: int32 = 5) -> T: ...


class C:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def m[T](self, x: T, k: int32 = 5) -> T:
        return x


@dispatch
def outer(x: int32) -> int32:
    return x


@dispatch
def outer(x: str) -> int32:
    return 0


@dispatch
def outer(x: list[int32]) -> int32:
    return len(x)


c = C()
print(outer({call}))
"""


@pytest.mark.parametrize("call,callee,fills", [
    ("inner(1)", "inner", 1),
    ("c.m(1)", "m", 0),  # its default stays the C++ declaration's
    ("sorted([3, 1], reverse=True)", "sorted", 0),
    ("sorted([3, 1], key=lambda v: -v)", "sorted", 0),
    ("min(4, 7, key=lambda v: -v)", "min", 0),
])
def test_a_probe_binds_and_reads_only(monkeypatch, call, callee,
                                      fills) -> None:
    """The call is analyzed at `outer`'s probe, a step that does not
    commit: its argument list, keywords and binding stay as written and no
    default is filled -- a builtin's `key=` / `reverse=` included; the
    commit at the winner lays it out and fills a default once."""
    # Per analysis of the watched call: whether a commit ran in it, whether
    # its argument fields came out as they went in, the defaults filled.
    seen: list[tuple[bool, bool, int]] = []
    fill_count: list[object] = []
    commits_in: list[list[bool]] = []
    commit_seen: list[tuple[bool, int]] = []
    commit, fill = CallAnalyzer.commit_args, calls_module.filled_default
    analyze_call = CallAnalyzer.analyze_call
    analyze_method_call = MethodAnalyzer.analyze_method_call

    def filled_default(*args, **kwargs):
        fill_count.append(args[0])
        return fill(*args, **kwargs)

    def commit_args(self, call, callee_info, binding, subst, **kwargs):
        commits = self.step_commits(call)
        if commits_in:
            commits_in[-1].append(commits)
        n = len(fill_count)
        layout = commit(self, call, callee_info, binding, subst, **kwargs)
        if callee_info.name == callee:
            commit_seen.append((commits, len(fill_count) - n))
        return layout

    def watched(analyze, name_of):
        def run(self, expr):
            if name_of(expr) != callee:
                return analyze(self, expr)
            before = (list(expr.args), dict(expr.kwargs or {}), expr.binding)
            n = len(fill_count)
            commits_in.append([])
            try:
                return analyze(self, expr)
            finally:
                unchanged = (
                    len(expr.args) == len(before[0])
                    and all(a is b for a, b in zip(expr.args, before[0]))
                    and dict(expr.kwargs or {}).keys() == before[1].keys()
                    and all(expr.kwargs[k] is v for k, v in before[1].items())
                    and expr.binding is before[2])
                seen.append((any(commits_in.pop()), unchanged,
                             len(fill_count) - n))
        return run

    monkeypatch.setattr(calls_module, "filled_default", filled_default)
    monkeypatch.setattr(CallAnalyzer, "commit_args", commit_args)
    monkeypatch.setattr(CallAnalyzer, "analyze_call",
                        watched(analyze_call, lambda e: e.func_name))
    monkeypatch.setattr(MethodAnalyzer, "analyze_method_call",
                        watched(analyze_method_call, lambda e: e.method))
    _compile(_PROBED.replace("{call}", call))
    # The probe's analysis commits nothing and leaves the call as written;
    # every commit that does not commit fills nothing; the last one, at
    # the winner, commits.
    assert seen and all(s == (False, True, 0) for s in seen)
    assert all(n == 0 for commits, n in commit_seen if not commits)
    assert commit_seen[0][0] is False and commit_seen[-1] == (True, fills)


def test_source_view_of_an_uncommitted_call_is_its_fields() -> None:
    a, b = TpyName("a"), TpyName("b")
    call = TpyCall(TpyName("f"), [a], kwargs={"k": b})
    assert source_view(call) == CallSource((a,), (("k", b),))
