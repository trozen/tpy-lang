"""Unit tests for the resumable-frame CFG builder.

Phase A of the generator -> resumable-frame migration makes the CFG
builder suspension-generic: a `yield` statement is now a suspension
point alongside `await`, the lazy-decomposition predicate
(`_stmt_has_any_suspension`) treats both uniformly, and a top-level
`yield` lowers to a `Yield` terminator carrying a `YieldPayload`. These
tests exercise that wiring at the CFG level, before any generator emit
exists (the builder runs on raw parsed `TpyStmt` bodies, no sema/codegen
needed).
"""
import pytest

from ..parse import Parser
from .resumable_cfg import (
    CFGBuilder, YieldPayload, AwaitPayload,
    _stmt_has_any_suspension, _CFGNotYetSupported,
)


def _gen_body(src: str):
    """Parse `src` and return the body (list of TpyStmt) of its first
    function."""
    module = Parser().parse(src, module_name="t")
    assert module.functions, "expected at least one function"
    return module.functions[0].body


def _build(src: str):
    return CFGBuilder().build_async(_gen_body(src))


class TestYieldDispatch:
    def test_top_level_yields_produce_yield_payloads(self):
        cfg = _build(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g() -> Iterator[Int32]:\n"
            "    yield 1\n"
            "    yield 2\n"
        )
        assert len(cfg.yield_sites) == 2
        for y in cfg.yield_sites:
            assert isinstance(y.payload, YieldPayload)
            assert not isinstance(y.payload, AwaitPayload)
            # A non-bare yield always carries its value expression.
            assert y.payload.value_expr is not None
        # Suspension indices are assigned in source order.
        assert [y.suspension_index for y in cfg.yield_sites] == [0, 1]

    def test_yield_in_while_decomposes_loop(self):
        # A yield inside a while body must force the loop into a CFG
        # region (multiple BBs), driven by the generic suspension
        # predicate -- not left as a single leaf statement.
        cfg = _build(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g(n: Int32) -> Iterator[Int32]:\n"
            "    i = 0\n"
            "    while i < n:\n"
            "        yield i\n"
            "        i += 1\n"
        )
        assert len(cfg.yield_sites) == 1
        assert isinstance(cfg.yield_sites[0].payload, YieldPayload)
        # Loop decomposition produces several BBs (check/body/exit + ...),
        # not the single block a non-decomposed leaf body would yield.
        assert len(cfg.blocks) > 1


class TestSuspensionPredicate:
    def test_detects_nested_yield(self):
        body = _gen_body(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g(n: Int32) -> Iterator[Int32]:\n"
            "    while n > 0:\n"
            "        yield n\n"
        )
        # body[-1] is the while-loop; it nests a yield.
        assert _stmt_has_any_suspension(body[-1]) is True

    def test_plain_loop_without_yield_is_not_a_suspension(self):
        body = _gen_body(
            "from tpy import Int32\n"
            "def f(n: Int32) -> Int32:\n"
            "    total = 0\n"
            "    while n > 0:\n"
            "        total += n\n"
            "        n -= 1\n"
            "    return total\n"
        )
        # The while loop has no suspension; predicate must say so.
        whiles = [s for s in body if type(s).__name__ == "TpyWhile"]
        assert whiles and _stmt_has_any_suspension(whiles[0]) is False


class TestLoopElseStillRejected:
    # Regression guard for the known for/while-else blocker (BUGS.md:26):
    # the CFG does not model break-vs-normal-exit, so a suspension inside
    # the else clause is rejected -- now via the generic predicate, for
    # `yield` too, not just `await`. Phase D of the migration lifts this.
    def test_yield_in_for_else_rejected(self):
        with pytest.raises(_CFGNotYetSupported):
            _build(
                "from typing import Iterator\n"
                "from tpy import Int32\n"
                "def g(xs: list[Int32]) -> Iterator[Int32]:\n"
                "    for x in xs:\n"
                "        yield x\n"
                "    else:\n"
                "        yield -1\n"
            )

    def test_yield_in_while_else_rejected(self):
        with pytest.raises(_CFGNotYetSupported):
            _build(
                "from typing import Iterator\n"
                "from tpy import Int32\n"
                "def g(n: Int32) -> Iterator[Int32]:\n"
                "    while n > 0:\n"
                "        yield n\n"
                "        n -= 1\n"
                "    else:\n"
                "        yield -1\n"
            )


class TestResumableGateStructuralSignal:
    # The resumable-generator eligibility gate
    # (generator.py::_resumable_generator_eligible) routes a generator onto
    # the resumable emitter only if its body is entirely leaf statements:
    # `all(not s.sub_bodies() for s in func.body)`. A `match` (or any
    # compound) wrapping a `yield` must be seen as a nested body so the
    # generator stays on the legacy path -- routing it to the resumable
    # emitter silently drops post-compound yields (BUGS.md). These guard
    # the load-bearing `sub_bodies()` signal the gate keys on.
    def test_match_body_is_compound_so_gate_rejects(self):
        body = _gen_body(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g() -> Iterator[Int32]:\n"
            "    yield 1\n"
            "    sel = 2\n"
            "    match sel:\n"
            "        case 2:\n"
            "            yield 99\n"
            "        case _:\n"
            "            yield 0\n"
            "    yield 3\n"
        )
        # The `match` is a nested-body statement -> gate predicate is False.
        assert any(s.sub_bodies() for s in body)
        assert not all(not s.sub_bodies() for s in body)

    def test_linear_body_is_all_leaves_so_gate_accepts(self):
        body = _gen_body(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g() -> Iterator[Int32]:\n"
            "    n = 10\n"
            "    yield n\n"
            "    yield n * 2\n"
            "    return\n"
        )
        # No nested-body statement -> gate predicate is True.
        assert all(not s.sub_bodies() for s in body)
