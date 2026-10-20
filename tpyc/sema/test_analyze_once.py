"""An expression analyzed once: a site that enters an analyzed node again
(a dunder call synthesized over the same receiver, an indexed callee) reads
its result back through `ExpressionAnalyzer.reuse` instead of analyzing it
a second time, and only under the hint it was analyzed under."""

import pytest

from ..parse import TpyCall, TpyName
from ..parse.nodes import walk_body_stmts
from ..thir.testutil import _compile, _entry
from ..typesys import INT32
from .slot_hint import SlotHint


def test_reuse_under_another_hint_is_an_internal_error():
    """A node handed back by `reuse` and entered under a hint other than
    the one it was analyzed under would be a different analysis."""
    _, modules = _compile(
        "from tpy import int32\n"
        "def f(n: int32) -> int32:\n"
        "    return n\n"
        "def main() -> None:\n"
        "    print(f(1))\n"
        "main()\n")
    entry = _entry(modules)
    main = next(f for f in entry.ast.functions if f.name == "main")
    calls: list[TpyCall] = []

    def on_expr(e) -> None:
        if (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
                and e.func.name == "print"):
            calls.append(e)

    walk_body_stmts(main.body, on_expr, lambda s: None)
    (arg,) = calls[0].args
    expr = entry.analyzer.expr
    with expr.reuse(arg, INT32):
        assert expr.analyze_expr(arg) is INT32
        with entry.analyzer.ctx.slot_hint_scope(SlotHint.declared(INT32)):
            with pytest.raises(AssertionError, match="another slot hint"):
                expr.analyze_expr(arg)


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
