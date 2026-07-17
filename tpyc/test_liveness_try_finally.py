"""Tests for `tpyc.liveness.try_terminates_ignoring_finally`.

Codegen elides a try's normal-path finally copy on this predicate, so a
wrong True silently drops the finally (the fall-through copy never emits).
It must answer for the try body + handlers ALONE -- `stmts_terminate` on
the whole statement folds in the finally's own termination and is a
different question.
"""

from __future__ import annotations

from tpyc.liveness import stmts_terminate, try_terminates_ignoring_finally
from tpyc.parse.nodes import (
    TpyExceptHandler, TpyPassStmt, TpyRaise, TpyStmt, TpyTry,
)


def _raise() -> TpyRaise:
    return TpyRaise(exception_type="ValueError")


def _try(try_body: list[TpyStmt],
         handlers: list[TpyExceptHandler] | None = None,
         finally_body: list[TpyStmt] | None = None) -> TpyTry:
    return TpyTry(
        try_body=try_body,
        handlers=handlers or [],
        else_body=[],
        finally_body=finally_body or [],
        tier="throw" if handlers else "finally_only",
    )


def _handler(body: list[TpyStmt]) -> TpyExceptHandler:
    return TpyExceptHandler(exception_type="ValueError", binding=None, body=body)


def test_terminating_finally_does_not_make_the_try_terminate():
    # The bug: a finally that always raises made the whole statement look
    # terminating, so the fall-through copy was elided and never ran.
    stmt = _try([TpyPassStmt()], [_handler([TpyPassStmt()])], [_raise()])
    assert try_terminates_ignoring_finally(stmt) is False
    # ...while the whole-statement question legitimately still says True.
    assert stmts_terminate([stmt]) is True


def test_handler_falling_through_defeats_a_terminating_try_body():
    stmt = _try([_raise()], [_handler([TpyPassStmt()])], [_raise()])
    assert try_terminates_ignoring_finally(stmt) is False


def test_try_and_every_handler_terminating_terminates():
    stmt = _try([_raise()], [_handler([_raise()])], [_raise()])
    assert try_terminates_ignoring_finally(stmt) is True


def test_one_falling_through_handler_of_several_defeats_it():
    stmt = _try([_raise()],
                [_handler([_raise()]), _handler([TpyPassStmt()])],
                [_raise()])
    assert try_terminates_ignoring_finally(stmt) is False


def test_without_handlers_the_try_body_decides():
    assert try_terminates_ignoring_finally(_try([_raise()])) is True
    assert try_terminates_ignoring_finally(_try([TpyPassStmt()])) is False
