"""Tests for the definition-kill and closure-capture halves of
`tpyc.liveness.analyze_last_uses`.

Both decide whether an EARLIER read of a name is its last use, which codegen
turns into a `std::move`. A wrong last-use there moves a value something later
still reads, so these pin the transfer functions directly rather than through
a construct that happens to consume the same fact.
"""

from __future__ import annotations

from tpyc.liveness import analyze_last_uses
from tpyc.parse.nodes import (
    TpyExceptHandler, TpyFunction, TpyMethodCall, TpyName, TpyNestedDef,
    TpyNoneLiteral,
    TpyPassStmt, TpyRaise, TpyReturn, TpyStmt, TpyTry, TpyVarDecl,
)


def _decl(name: str, init) -> TpyVarDecl:
    return TpyVarDecl(name=name, type=None, init=init)


def _call(recv: str, method: str) -> TpyMethodCall:
    return TpyMethodCall(obj=TpyName(name=recv), method=method, args=[])


def _nested_def(name: str, body: list[TpyStmt]) -> TpyNestedDef:
    return TpyNestedDef(
        func=TpyFunction(name=name, params=[], return_type=None, body=body))


def _is_last_use(marks, node: TpyName) -> bool:
    return node in marks


class TestSelfReferentialDefinition:
    """`g = g.next()` reads g before rebinding it, so the transfer is
    (live_after - {g}) | {g} -- g stays live entering the statement. Killing
    after the union dropped it and made an earlier consume look terminal."""

    def test_earlier_consume_is_not_last_use_when_a_later_stmt_rebinds_from_it(self):
        consumed = TpyName(name="g")
        marks = analyze_last_uses([
            _decl("g", None),
            _decl("h", consumed),            # would move g ...
            _decl("g", _call("g", "next")),  # ... but this still reads it
        ])
        assert not _is_last_use(marks, consumed)

    def test_the_rebinding_read_itself_is_still_a_last_use(self):
        read = TpyName(name="g")
        marks = analyze_last_uses([
            _decl("g", None),
            _decl("g", TpyMethodCall(obj=read, method="next", args=[])),
        ])
        assert _is_last_use(marks, read)

    def test_a_plain_rebind_still_kills(self):
        consumed = TpyName(name="g")
        marks = analyze_last_uses([
            _decl("g", None),
            _decl("h", consumed),
            _decl("g", None),  # no read of g -- the kill stands
        ])
        assert _is_last_use(marks, consumed)


class TestNestedDefCaptures:
    """A nested def stays callable to the end of the function, so a name it
    captures is live at and after the def. `captured_names` is empty while this
    pass runs (sema fills it later), so the arm falls back to the syntactic
    free-name approximation, which is what keeps a name live ABOVE the def.

    Reads BETWEEN the def and a terminator are not covered: the entry seed is
    the only thing protecting them and a `return` clears it (BUGS.md). Widening
    the fix to pin every captured name unconditionally is wrong -- a rebind
    between the consume and the closure call soundly kills the seed, which
    `tests/cases/auto_move/closure_capture_reassign_moves` pins."""

    def test_consume_before_the_def_is_not_last_use(self):
        consumed = TpyName(name="b")
        marks = analyze_last_uses([
            _decl("b", None),
            _decl("c", consumed),
            _nested_def("inner", [TpyReturn(value=TpyName(name="b"))]),
            TpyReturn(value=TpyNoneLiteral()),
        ])
        assert not _is_last_use(marks, consumed)

    def test_a_name_no_nested_def_captures_is_still_movable(self):
        consumed = TpyName(name="b")
        marks = analyze_last_uses([
            _decl("b", None),
            _nested_def("inner", [TpyReturn(value=TpyNoneLiteral())]),
            _decl("c", consumed),
            TpyReturn(value=TpyName(name="c")),
        ])
        assert _is_last_use(marks, consumed)


class TestExceptionPathRestore:
    """A handler or finally runs on the exception path, which is reachable from
    anywhere in the try body -- so what they read is live ENTERING the
    statement. A terminator in the try body clears the live set for the normal
    path; the exception-path names have to survive that."""

    def test_consume_before_a_try_whose_finally_reads_it(self):
        consumed = TpyName(name="v")
        marks = analyze_last_uses([
            _decl("v", None),
            _decl("w", consumed),
            TpyTry(
                try_body=[TpyRaise(exception_type="ValueError")],
                handlers=[],
                else_body=[],
                finally_body=[_decl("x", TpyName(name="v"))],
                tier="finally_only",
            ),
        ])
        assert not _is_last_use(marks, consumed)

    def test_consume_before_a_try_whose_handler_reads_it(self):
        consumed = TpyName(name="v")
        marks = analyze_last_uses([
            _decl("v", None),
            _decl("w", consumed),
            TpyTry(
                try_body=[TpyRaise(exception_type="ValueError")],
                handlers=[TpyExceptHandler(
                    exception_type="ValueError", binding=None,
                    body=[_decl("x", TpyName(name="v"))])],
                else_body=[],
                finally_body=[],
                tier="throw",
            ),
        ])
        assert not _is_last_use(marks, consumed)

    def test_a_try_reading_nothing_leaves_the_earlier_consume_movable(self):
        consumed = TpyName(name="v")
        marks = analyze_last_uses([
            _decl("v", None),
            _decl("w", consumed),
            TpyTry(
                try_body=[TpyRaise(exception_type="ValueError")],
                handlers=[TpyExceptHandler(
                    exception_type="ValueError", binding=None,
                    body=[TpyPassStmt()])],
                else_body=[],
                finally_body=[],
                tier="throw",
            ),
        ])
        assert _is_last_use(marks, consumed)
