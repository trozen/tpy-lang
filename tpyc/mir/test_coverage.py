"""Unsupported THIR must never masquerade as an empty or partial MIR body."""

from dataclasses import replace
from collections.abc import Callable, Iterable

import pytest

from ..parse import SourceLocation
from ..thir import nodes as th
from ..typesys import BOOL, INT32, INT64, STR, IntLiteralType, TpyType, TupleType
from .lower import lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered

LOC = SourceLocation(17, 4, "case.py")
X = th.THIRName(INT32, "x", loc=LOC)
ONE = th.THIRLiteral(INT32, 1, loc=LOC)


def function(body: Iterable[th.THIRStmt], return_type: TpyType = INT32) -> th.THIRFunction:
    return th.THIRFunction("f", (th.THIRParam("x", INT32),), return_type,
                           tuple(body), th.THIRFunctionLayout())


def reject(fn: th.THIRFunction, reason: str, node_kind: str | None = None) -> MIRNotCovered:
    result = lower_function(fn, MIRBodyId("test", "f"), kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRNotCovered), result
    assert reason in result.reason
    if node_kind is not None:
        assert result.node_kind == node_kind
        assert result.loc == LOC
    return result


@pytest.mark.parametrize("expr,reason", [
    (th.THIRCall(INT32, "g", (), loc=LOC), "call needs resolved ordinary callee"),
    (th.THIRBinOp(INT32, X, "+", ONE, None, loc=LOC), "unsupported binary operation"),
    (th.THIRName(INT32, "global", loc=LOC), "non-local name"),
    (replace(X, cpp="::other::x"), "unsupported metadata: cpp"),
    (replace(X, deref=True), "unsupported metadata: deref"),
    (replace(X, form=th.Form.BORROW), "unsupported expression form"),
    (th.THIRCoerce(INT32, ONE, "checked_cast", loc=LOC), "unsupported coercion"),
    (th.THIRCoerce(INT32, ONE, "int_literal_to_fixed_int", wrap="cast({0})", loc=LOC),
     "unsupported metadata: wrap"),
    (th.THIRWalrus(INT32, "y", "y", ONE, loc=LOC), "existing scalar local"),
    (th.THIRWalrus(INT32, "x", "x", ONE, cpp_type="int32_t", loc=LOC),
     "unsupported metadata: cpp_type"),
    (th.THIRLiteral(INT32, 2**31, loc=LOC), "unsupported literal value"),
    (th.THIRLiteral(IntLiteralType(2**31), 2**31, loc=LOC), "unsupported literal value"),
    (th.THIRLiteral(INT64, 1, loc=LOC), "unsupported expression type"),
    (th.THIRLiteral(STR, "s", loc=LOC), "unsupported expression type"),
    (th.THIRLiteral(TupleType((INT32,)), (1,), loc=LOC), "unsupported expression type"),
])
def test_expression_coverage(expr: th.THIRExpr, reason: str) -> None:
    reject(function([th.THIRReturn(expr)]), reason, type(expr).__name__)


@pytest.mark.parametrize("left,right", [
    (X, th.THIRWalrus(INT32, "x", "x", ONE, loc=LOC)),
    (th.THIRWalrus(INT32, "x", "x", ONE, loc=LOC), X),
    (th.THIRWalrus(INT32, "x", "x", ONE, loc=LOC),
     th.THIRWalrus(INT32, "x", "x", ONE, loc=LOC)),
    (th.THIRIfExpr(INT32, th.THIRLiteral(BOOL, False), X,
                  th.THIRWalrus(INT32, "x", "x", ONE)), X),
])
def test_eager_order_sensitive_operands_are_not_analyzed(left: th.THIRExpr,
                                                        right: th.THIRExpr) -> None:
    expr = th.THIRBinOp(BOOL, left, "==", right, None, loc=LOC)
    reject(function([th.THIRReturn(expr)], BOOL), "order-sensitive", "THIRBinOp")


@pytest.mark.parametrize("wrapper", [
    lambda bad: (th.THIRReturn(ONE), bad),
    lambda bad: (th.THIRIf(th.THIRLiteral(BOOL, False), (bad,)), th.THIRReturn(ONE)),
    lambda bad: (th.THIRWhile(th.THIRLiteral(BOOL, False), (bad,)), th.THIRReturn(ONE)),
])
def test_unreachable_unsupported_nodes_still_fail_whole_body(
    wrapper: Callable[[th.THIRStmt], tuple[th.THIRStmt, ...]],
) -> None:
    bad = th.THIRExprStmt(th.THIRCall(INT32, "unknown", (), loc=LOC))
    reject(function(wrapper(bad)), "call needs resolved ordinary callee", "THIRCall")


@pytest.mark.parametrize("stmt,reason", [
    (th.THIRBreak(loc=LOC), "outside loop"),
    (th.THIRContinue(loc=LOC), "outside loop"),
    (th.THIRIf(th.THIRLiteral(BOOL, True), (), hoist_decls=(th.HoistDecl("v", "int32_t"),), loc=LOC),
     "missing or inconsistent hoisted binding facts"),
    (th.THIRWhile(th.THIRLiteral(BOOL, True), (), hoist_decls=(th.HoistDecl("v", "int32_t"),), loc=LOC),
     "missing or inconsistent hoisted binding facts"),
    (th.THIRIf(th.THIRLiteral(BOOL, True), (), is_constexpr=True, loc=LOC),
     "unsupported metadata: is_constexpr"),
    (th.THIRAssign(X, ONE, slot_cpp="int32_t", loc=LOC), "unsupported metadata: slot_cpp"),
])
def test_statement_metadata_coverage(stmt: th.THIRStmt, reason: str) -> None:
    reject(function([stmt, th.THIRReturn(ONE)]), reason, type(stmt).__name__)


def test_branch_and_late_declarations_are_covered() -> None:
    decl = th.THIRVarDecl("y", INT32, ONE, loc=LOC)
    result = lower_function(function([th.THIRIf(th.THIRLiteral(BOOL, True), (decl,)), th.THIRReturn(ONE)]),
                            MIRBodyId("test", "f"), kind=MIRBodyKind.FREE_FUNCTION)
    assert isinstance(result, MIRFunction)
    late = function([th.THIRAssign(X, ONE), decl, th.THIRReturn(th.THIRName(INT32, "y"))])
    assert isinstance(lower_function(late, MIRBodyId("test", "late"), kind=MIRBodyKind.FREE_FUNCTION), MIRFunction)


def test_uninitialized_source_is_not_covered() -> None:
    fn = function([th.THIRVarDecl("y", INT32), th.THIRReturn(th.THIRName(INT32, "y"))])
    reject(fn, "read before definite assignment")


def test_function_metadata_and_fallthrough() -> None:
    fn = function([th.THIRReturn(ONE)])
    reject(replace(fn, error_return_cpp="Error"), "error-return")
    reject(replace(fn, layout=th.THIRFunctionLayout(hoisted_locals=frozenset({"y"}))), "hoisted")
    reject(replace(fn, return_type=STR), "return type")
    reject(replace(fn, params=(th.THIRParam("x", INT64),)), "parameter type")
    reject(replace(fn, body=()), "non-void fallthrough")


def test_returns_in_both_loop_exits_do_not_create_fake_fallthrough() -> None:
    fn = function([th.THIRWhile(th.THIRLiteral(BOOL, True), (th.THIRReturn(ONE),),
                                (th.THIRReturn(X),))])
    assert isinstance(lower_function(fn, MIRBodyId("test", "f"), kind=MIRBodyKind.FREE_FUNCTION), MIRFunction)


@pytest.mark.parametrize("op", ["and", "or"])
def test_value_select_requires_canonical_thir_operators(op: str) -> None:
    value = th.THIRValueSelect(BOOL, th.THIRLiteral(BOOL, True), th.THIRLiteral(BOOL, False),
                               op, loc=LOC)
    reject(function([th.THIRReturn(value)], BOOL), "unsupported value select", "THIRValueSelect")
