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
    _stmts_have_suspending_compound,
    _stmts_have_tuple_unpack_for_with_suspension,
)
from .generator import GEN_DEFERRED_SUSPENDING_COMPOUNDS


def _gen_body(src: str):
    """Parse `src` and return the body (list of TpyStmt) of its first
    function."""
    module = Parser().parse(src, module_name="t")
    assert module.functions, "expected at least one function"
    return module.functions[0].body


def _build(src: str):
    return CFGBuilder().build(_gen_body(src))


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


class TestLoopElseSupported:
    # Phase D3: the CFG now models break-vs-normal-exit via distinct edges
    # (else runs on the normal-exit edge; `break` targets a separate
    # after-BB that skips it), so a suspension inside a loop `else` is
    # supported instead of rejected. These build the CFG and assert both
    # the body and else yields are recorded.
    def test_yield_in_while_else_builds(self):
        cfg = _build(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g(n: Int32) -> Iterator[Int32]:\n"
            "    while n > 0:\n"
            "        yield n\n"
            "        n -= 1\n"
            "    else:\n"
            "        yield -1\n"
        )
        # one yield in the body, one in the else clause.
        assert len(cfg.yield_sites) == 2

    def test_yield_in_for_else_builds(self):
        # for-loops need a registered uid (normally from the gen_async
        # pre-scan); supply one so the builder reaches the else handling.
        body = _gen_body(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g(xs: list[Int32]) -> Iterator[Int32]:\n"
            "    for x in xs:\n"
            "        yield x\n"
            "    else:\n"
            "        yield -1\n"
        )
        for_stmt = body[0]
        cfg = CFGBuilder(for_uid_map={id(for_stmt): 0}).build(body)
        assert len(cfg.yield_sites) == 2


class TestUndecomposedLeafSuspensionRejected:
    # The generator eligibility gate trial-builds the CFG and falls back to
    # the legacy path when the builder raises. A compound the builder does
    # NOT decompose (today: `match`) that hides a `yield`/`await` must raise
    # `_CFGNotYetSupported` rather than be appended as a leaf -- otherwise
    # the nested suspension would be emitted as straight-line code and the
    # post-compound yields silently dropped (BUGS.md). This guards the
    # leaf-append check in `_build_stmt`.
    def test_yield_in_match_rejected(self):
        with pytest.raises(_CFGNotYetSupported):
            _build(
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

    def test_match_without_suspension_is_plain_leaf(self):
        # A `match` with no `yield` inside is ordinary straight-line code
        # between suspensions -- the builder appends it as a leaf and the
        # CFG builds cleanly.
        cfg = _build(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g(sel: Int32) -> Iterator[Int32]:\n"
            "    yield 1\n"
            "    match sel:\n"
            "        case 2:\n"
            "            print(99)\n"
            "        case _:\n"
            "            print(0)\n"
            "    yield 3\n"
        )
        assert len(cfg.yield_sites) == 2


class TestDeferredCompoundDetection:
    # Phase D1 routed `if`/`while` generators onto the resumable emitter;
    # D2 added `for`; E added `try`/`with`. GEN_DEFERRED_SUSPENDING_COMPOUNDS
    # is now empty -- no compound kind is deferred. Tests confirm the
    # predicate returns False for every handled kind.
    def _body(self, header: str, body_src: str):
        return _gen_body(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            f"def g(xs: list[Int32]) -> Iterator[Int32]:\n{body_src}"
        )

    def test_for_with_yield_is_not_deferred(self):
        body = self._body("", "    for x in xs:\n        yield x\n")
        assert not _stmts_have_suspending_compound(
            body, GEN_DEFERRED_SUSPENDING_COMPOUNDS)

    def test_try_with_yield_is_not_deferred(self):
        # Phase E: try/with are now handled on the resumable path.
        body = self._body(
            "", "    try:\n        yield xs[0]\n    finally:\n        print(1)\n")
        assert not _stmts_have_suspending_compound(
            body, GEN_DEFERRED_SUSPENDING_COMPOUNDS)

    def test_if_while_with_yield_not_deferred(self):
        body = self._body(
            "",
            "    if xs:\n        yield xs[0]\n"
            "    n = 0\n"
            "    while n < 3:\n        yield n\n        n += 1\n")
        assert not _stmts_have_suspending_compound(
            body, GEN_DEFERRED_SUSPENDING_COMPOUNDS)

    def test_yield_free_for_not_deferred(self):
        # A `for` with no suspension is plain leaf code -> not deferred.
        body = self._body(
            "",
            "    total = 0\n    for x in xs:\n        total += x\n    yield total\n")
        assert not _stmts_have_suspending_compound(
            body, GEN_DEFERRED_SUSPENDING_COMPOUNDS)

    def test_tuple_unpack_for_with_yield_is_deferred(self):
        # A `for a, b in ...` carrying a yield is deferred to the legacy
        # path (the resumable emit doesn't frame-store the unpack targets).
        body = _gen_body(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g(ps: list[tuple[Int32, Int32]]) -> Iterator[Int32]:\n"
            "    for a, b in ps:\n        yield a + b\n")
        assert _stmts_have_tuple_unpack_for_with_suspension(body)

    def test_plain_unpack_free_for_not_deferred(self):
        # A non-tuple-unpack `for` with a yield is NOT caught by the
        # tuple-unpack predicate (it routes to the resumable path).
        body = _gen_body(
            "from typing import Iterator\n"
            "from tpy import Int32\n"
            "def g(xs: list[Int32]) -> Iterator[Int32]:\n"
            "    for x in xs:\n        yield x\n")
        assert not _stmts_have_tuple_unpack_for_with_suspension(body)
