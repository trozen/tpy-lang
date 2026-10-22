"""An expression analyzed once: a site that enters an analyzed node again
(a dunder call synthesized over the same receiver, an indexed callee, a
winner receiving what an overload probe analyzed) meets it through its
record (`ExpressionAnalyzer.reenter`) instead of analyzing it a second
time."""

import ast
import pathlib
from contextlib import contextmanager

import pytest

from ..diagnostics import SemanticError
from ..parse import TpyCall, TpyName
from ..parse.nodes import walk_body_stmts
from ..thir.testutil import _compile, _entry
from ..typesys import INT32
from . import context as context_module
from .calls import CallAnalyzer
from .context import ProbeSlot, SemanticContext
from .methods import MethodAnalyzer
from .slot_hint import SlotHint


def test_a_closed_node_met_again_hands_back_its_record():
    """A node whose analysis does not depend on its slot, met again under
    another hint, hands back what its one analysis returned; analyzing it
    a second time is an internal error under the harness."""
    _, modules = _compile(
        "from tpy import int32\n"
        "def f(n: int32) -> int32:\n"
        "    return n\n"
        "def main() -> None:\n"
        "    x: int32 = 1\n"
        "    print(f(x))\n"
        "main()\n")
    entry = _entry(modules)
    main = next(f for f in entry.ast.functions if f.name == "main")
    calls: list[TpyCall] = []

    def on_expr(e) -> None:
        if (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
                and e.func.name == "print"):
            calls.append(e)

    walk_body_stmts(main.body, on_expr, lambda s: None)
    (call,) = calls[0].args
    while not isinstance(call, TpyCall):
        call = call.expr
    (arg,) = call.args
    while not isinstance(arg, TpyName):
        arg = arg.expr
    expr = entry.analyzer.expr
    ctx = entry.analyzer.ctx
    recorded = ctx.analysis_records[arg].typ
    with ctx.slot_hint_scope(SlotHint.declared(INT32)):
        assert expr.analyze_expr(arg) == recorded
    with pytest.raises(AssertionError, match="analyzed twice"):
        expr._first_analysis(arg)


def test_both_dunders_write_evaluates_the_receiver_once():
    """A write the read side resolves through `__getattr__` becomes a
    `__setattr__` call over the receiver already analyzed: consuming it
    there is one consume. Sema only: lowering rejects every write on a
    class with both dunders (BUGS.md#dyn-attr-fallback-rvalue-receiver-rejects)."""
    _compile(
        "from tplib.box import Box\n"
        "class Both:\n"
        "    def __getattr__(self, name: str) -> int:\n"
        "        return 1\n"
        "    def __setattr__(self, name: str, value: int) -> None:\n"
        "        print('set', name, value)\n"
        "def main() -> None:\n"
        "    e = Box(Both())\n"
        "    e.take().z = 3\n"
        "main()\n")


def test_indexed_callee_evaluates_the_receiver_once():
    """`o.fs[0](x)` resolves its callee off the receiver the call already
    analyzed. Sema only: the lowering rejects a call-rooted container field
    (BUGS.md#call-rooted-container-field-index)."""
    _compile(
        "from typing import Callable\n"
        "from tpy import int32\n"
        "from tplib.box import Box\n"
        "def double(x: int32) -> int32:\n"
        "    return x * 2\n"
        "class Ops:\n"
        "    fs: list[Callable[[int32], int32]]\n"
        "    def __init__(self) -> None:\n"
        "        self.fs = [double]\n"
        "def main() -> None:\n"
        "    b = Box(Ops())\n"
        "    print(b.take().fs[0](4))\n"
        "main()\n")


def test_no_receive_loop_outside_the_one_receive_step():
    """Every winner and commit receives its arguments through
    `CallAnalyzer.receive_winner_args`: the per-argument receive duties are
    called from its per-argument step and from nowhere else."""
    duties = {"check_own_param", "mark_pending_arg_context"}
    callers: set[str] = set()
    sema = pathlib.Path(__file__).resolve().parent
    for path in sorted(sema.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text())
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            for fn in cls.body:
                if not isinstance(fn, ast.FunctionDef):
                    continue
                for node in ast.walk(fn):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr in duties):
                        callers.add(f"{cls.name}.{fn.name}")
    assert callers == {"CallAnalyzer._receive_arg"}


# Each refused candidate of the @dispatch generic fallback meets the first
# argument at a probe slot: an `Own[B]` name, and an aggregate whose element
# check (list, dict, set, comprehension), arm coercion (select), element
# conversion (tuple) or literal element (`Literal[True]`) is its slot step's.
_FALLBACK_CANDIDATES = """\
from typing import Any, Literal
from tpy import dispatch, Own, int32, int64

class B:
    def __init__(self, v: int) -> None:
        self.v = v

@dispatch
def f[T](x: {param}, y: T, z: int) -> None:
    print("f")

@dispatch
def f[T](x: {param}, y: T, z: float) -> None:
    print("f")

def main(c: bool) -> None:
    b = B(4)
    n: int = 5
    ys = [1, 2]
    f[int32]({arg}, 1, "bad")
    print(b.v, n, ys)

main(True)
"""

_FALLBACK_ARGS = {
    "own_name": ("Own[B]", "b"),
    "list_element": ("list[Any]", "[b]"),
    "dict_value": ("dict[str, Any]", '{"k": b}'),
    "select_arm": ("B | None", "b if c else None"),
    "tuple_element": ("tuple[int64]", "(n,)"),
    "set_element": ("set[Any]", "{b}"),
    "select_pending_list": ("list[int64]", "ys if c else ys"),
    "list_comprehension": ("list[Any]", "[b for i in range(2)]"),
    "dict_comprehension": ("dict[int32, Any]", "{i: b for i in range(2)}"),
    "literal_element": ("list[Literal[True]]", "[True]"),
}


def _node_shape(node: object) -> list[str]:
    """The node kinds under `node`, so a coercion node a step wrapped
    around a child shows."""
    out = [type(node).__name__]
    for child in getattr(node, "children", lambda: [])():
        out += _node_shape(child)
    return out


@pytest.mark.parametrize("shape", sorted(_FALLBACK_ARGS))
def test_a_refused_fallback_candidate_leaves_nothing_behind(monkeypatch,
                                                            shape):
    """The @dispatch generic fallback validates each candidate at a probe
    slot inside a trial: a refused one writes no coercion, copy record,
    pending cell or instantiation root -- for an aggregate argument
    neither, its slot step deciding types only there."""
    param, arg = _FALLBACK_ARGS[shape]
    seen: list[tuple[object, object]] = []
    original = CallAnalyzer._validates

    def state(analyzer: CallAnalyzer, expr: TpyCall) -> tuple:
        ctx = analyzer.ctx
        return (len(ctx.func.owning_reads), sorted(ctx.func.consumed_vars),
                len(ctx.diagnostics), len(ctx.func.tuple_sinks),
                len(ctx.own_copy_roots),
                len(ctx.pending_num_cells),
                [_node_shape(a) for a in expr.args])

    def validates(self, expr, func):
        before = state(self, expr)
        passed = original(self, expr, func)
        seen.append((passed, before == state(self, expr)))
        return passed

    monkeypatch.setattr(CallAnalyzer, "_validates", validates)
    with pytest.raises(SemanticError, match="No matching overload"):
        _compile(_FALLBACK_CANDIDATES.format(param=param, arg=arg))
    assert seen == [(False, True), (False, True)]


def test_a_fallback_candidate_is_validated_as_its_committed_slot_checks():
    """A fallback candidate's check is the committed one, deciding nothing:
    a `[True]` argument fits its `list[Literal[True]]` parameter by the
    literal it is written as, so the second candidate takes the call."""
    _, modules = _compile(
        "from typing import Literal\n"
        "from tpy import dispatch, int32\n"
        "@dispatch\n"
        "def use[T](xs: list[Literal[True]], tag: T, z: int) -> None:\n"
        "    print(xs)\n"
        "@dispatch\n"
        "def use[T](xs: list[Literal[True]], tag: T, z: str) -> None:\n"
        "    print(xs)\n"
        "def main() -> None:\n"
        "    use[int32]([True], 0, 'ok')\n"
        "main()\n")
    assert _entry(modules).analyzer.ctx.diagnostics == []


def test_a_record_written_at_a_query_is_an_internal_error(monkeypatch):
    """Under the harness a slot-step record written where the step does not
    commit is an internal error: its writer missed the gate."""
    monkeypatch.setattr(context_module, "_ASSERT_SLOT_EFFECTS", True)
    ctx = SemanticContext.__new__(SemanticContext)
    ctx.probe_slot = ProbeSlot.COMMITTED
    ctx.probe_step_commits = None
    ctx.assert_slot_effect("a record")
    with ctx.probe_slot_scope(ProbeSlot.QUERY):
        assert not ctx.slot_step_commits()
        with ctx.probe_slot_scope(True):
            # A probe inside a query stays a query.
            assert ctx.probe_slot is ProbeSlot.QUERY
        with pytest.raises(AssertionError, match="does not commit"):
            ctx.assert_slot_effect("a record")
        with ctx.probe_slot_scope(False):
            # So does a body: the trial around the query rolls it back.
            assert ctx.probe_slot is ProbeSlot.QUERY
            with pytest.raises(AssertionError, match="does not commit"):
                ctx.assert_slot_effect("a body's record")
    # At an overload probe, the step running decides (`probe_step_commits`).
    ctx.probe_step_commits = lambda: False
    with ctx.probe_slot_scope(True):
        assert ctx.probe_slot is ProbeSlot.PROBE
        with pytest.raises(AssertionError, match="does not commit"):
            ctx.assert_slot_effect("an open node's record")
        with ctx.probe_slot_scope(False):
            ctx.assert_slot_effect("a body's record")
    ctx.probe_step_commits = lambda: True
    with ctx.probe_slot_scope(True):
        ctx.assert_slot_effect("a carrier's record")


# A lambda met at a generic call's probe captures a local whose type a
# pending number decides: the capture settles it as the lambda's body.
_LAMBDA_CAPTURES = {
    "pending_number": (
        "    n = 1\n"
        "    print(apply(lambda x: x + n, int32(2)))\n"
        "    n = int32(3)\n"
        "    print(n)\n"),
    "pending_list": (
        "    xs = [1, 2]\n"
        "    print(apply(lambda x: x + xs[0], int32(2)))\n"
        "    xs.append(int32(3))\n"
        "    print(xs)\n"),
}


@pytest.mark.parametrize("shape", sorted(_LAMBDA_CAPTURES))
def test_a_lambda_capture_settles_as_its_body(monkeypatch, shape):
    """The settlement of a lambda's captures is a body effect: at the
    generic call's probe it runs where the body runs, not as the probe's
    slot step."""
    monkeypatch.setattr(context_module, "_ASSERT_SLOT_EFFECTS", True)
    _compile(
        "from typing import Callable\n"
        "from tpy import int32\n"
        "def apply[T](f: Callable[[T], T], x: T) -> T:\n"
        "    return f(x)\n"
        "def main() -> None:\n"
        + _LAMBDA_CAPTURES[shape] +
        "main()\n")


def test_a_nested_def_escapes_at_its_committed_slot_only(monkeypatch):
    """A nested def passed to a generic callee's `Callable` parameter is
    marked escaping by the winner's receive (`nested_def_escapes`), never
    by the probe that infers the callee's type parameters."""
    probe_writes: list[set[str]] = []
    original = SemanticContext.probe_slot_scope

    @contextmanager
    def probe_slot_scope(self, probing):
        before = set(self.func.nested_def_escapes)
        with original(self, probing):
            yield
        if probing and self.func.nested_def_escapes != before:
            probe_writes.append(self.func.nested_def_escapes - before)

    monkeypatch.setattr(SemanticContext, "probe_slot_scope",
                        probe_slot_scope)
    _, modules = _compile(
        "from typing import Callable\n"
        "from tpy import int32\n"
        "def use[T](fn: Callable[[int32], int32], x: T) -> int32:\n"
        "    return fn(1)\n"
        "def main() -> None:\n"
        "    k = 5\n"
        "    def inc(x: int32) -> int32:\n"
        "        return x + k\n"
        "    print(use(inc, 7))\n"
        "main()\n")
    assert probe_writes == []
    main = next(f for f in _entry(modules).ast.functions
                if f.name == "main")
    assert main.body[1].escapes


def test_a_generic_method_step_keeps_its_receiver_checks(monkeypatch):
    """A generic method call completed at its slot infers its type
    parameters again from the resolution its receiver made; the receiver's
    checks (a consuming call's) ran once, in its body."""
    calls: list[str] = []
    original = MethodAnalyzer._validate_consuming_call

    def validate(self, expr):
        calls.append(expr.method)
        return original(self, expr)

    monkeypatch.setattr(MethodAnalyzer, "_validate_consuming_call", validate)
    _compile(
        "from typing import Self\n"
        "from tpy import Own\n"
        "class B:\n"
        "    def __init__(self, v: int) -> None:\n"
        "        self.v = v\n"
        "    def take[T](self: Own[Self], x: T) -> T:\n"
        "        print(self.v)\n"
        "        return x\n"
        "def ident[T](x: T) -> T:\n"
        "    return x\n"
        "def main() -> None:\n"
        "    b = B(5)\n"
        "    print(ident(b.take(7)))\n"
        "main()\n")
    assert calls == ["take"]


# `run`'s lambda is dry-run at each candidate inside a trial; its generator
# expression's loop releases a held copy warning there, inserted in source
# order ahead of the TypedDict warning made before the trial.
_TRIAL_RELEASES_HELD_WARNING = """\
from typing import TypedDict
from tpy import Fn, Own, int32, dispatch


class B:
    def __init__(self, v: int32) -> None:
        self.v = v


def eat(x: Own[B]) -> int32:
    return x.v


@dispatch
def m[T, U](f: Fn[[T], U], xs: list[T]) -> int32:
    return len(xs)


@dispatch
def m[T, U](f: Fn[[T, T], U], xs: list[T]) -> int32:
    return 2 * len(xs)


def run(bs: list[B]) -> None:
    ys: list[int32] = [1, 2]
    print(m(lambda a: a + sum(eat(x) for x in bs), ys))


class Late(TypedDict):
    level: int32 = 3
"""


def test_a_trial_keeps_the_diagnostics_made_before_it():
    """A trial restores the diagnostics list itself: a held warning it
    releases in source order does not push an earlier one out."""
    _, modules = _compile(_TRIAL_RELEASES_HELD_WARNING)
    messages = [(d.loc.line, d.message.split(";")[0])
                for d in _entry(modules).analyzer.ctx.diagnostics]
    assert (10, "Own[B] param 'x' is never consumed (not stored in a "
                "field, forwarded to another Own[T], or returned)") in messages
    assert sum("copies B into owned storage" in m for _, m in messages) == 1
