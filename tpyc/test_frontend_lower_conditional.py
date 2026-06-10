"""Lowering converts a frontend-IR `Conditional(cond, then, else_)` into a
`TpyIfExpr` -- the node a plugin emits for a source-level ternary.
Sub-expressions lower recursively, so a Conditional works in any expression
position, and a failure to lower any sub-expression propagates as a lowering
failure.
"""

from tpyc.frontend_ir.lower import lower_module
from tpyc.frontend_ir.nodes import (
    BinOp, BinOpKind, Conditional, ExprStmt, FrontendModule, Function, IntLit,
    Name)
from tpyc.parse.nodes import (
    TpyBinOp, TpyExprStmt, TpyIfExpr, TpyIntLiteral, TpyName)


class _UnknownExpr:
    """A node the lowerer doesn't recognize, so _lower_expr returns None for
    it -- lets us check that Conditional propagates a failing sub-expression."""
    loc = None


def _lower_module(expr):
    fn = Function(name="f", body=(ExprStmt(value=expr),))
    return lower_module(FrontendModule(qname="testmod", functions=(fn,)),
                        "testplugin")


def _lower_expr(expr):
    res = _lower_module(expr)
    assert res.module is not None, res.diagnostics
    stmt = res.module.functions[0].body[0]
    assert isinstance(stmt, TpyExprStmt)
    return stmt.expr


def test_conditional_lowers_to_ifexpr():
    cond = Conditional(
        cond=Name(ident="flag"),
        then=IntLit(value=1),
        else_=IntLit(value=-1))
    out = _lower_expr(cond)
    assert isinstance(out, TpyIfExpr)
    assert isinstance(out.condition, TpyName)
    assert out.condition.name == "flag"
    assert isinstance(out.then_expr, TpyIntLiteral)
    assert out.then_expr.value == 1
    assert isinstance(out.else_expr, TpyIntLiteral)
    assert out.else_expr.value == -1


def test_conditional_nested_in_then_branch():
    cond = Conditional(
        cond=Name(ident="flag"),
        then=Conditional(
            cond=Name(ident="b"),
            then=IntLit(value=1),
            else_=IntLit(value=2)),
        else_=IntLit(value=3))
    out = _lower_expr(cond)
    assert isinstance(out, TpyIfExpr)
    assert isinstance(out.then_expr, TpyIfExpr)
    assert out.then_expr.then_expr.value == 1
    assert out.then_expr.else_expr.value == 2
    assert out.else_expr.value == 3


def test_conditional_nested_in_else_branch():
    cond = Conditional(
        cond=Name(ident="flag"),
        then=IntLit(value=1),
        else_=Conditional(
            cond=Name(ident="b"),
            then=IntLit(value=2),
            else_=IntLit(value=3)))
    out = _lower_expr(cond)
    assert isinstance(out, TpyIfExpr)
    assert out.then_expr.value == 1
    assert isinstance(out.else_expr, TpyIfExpr)
    assert out.else_expr.then_expr.value == 2
    assert out.else_expr.else_expr.value == 3


def test_conditional_in_arbitrary_expression_position():
    # Operand of a BinOp -- confirms recursive lowering reaches a Conditional
    # nested anywhere, not just at a statement root.
    expr = BinOp(
        op=BinOpKind.ADD,
        lhs=Conditional(
            cond=Name(ident="flag"),
            then=IntLit(value=1),
            else_=IntLit(value=2)),
        rhs=IntLit(value=10))
    out = _lower_expr(expr)
    assert isinstance(out, TpyBinOp)
    assert isinstance(out.left, TpyIfExpr)
    assert out.left.then_expr.value == 1
    assert out.right.value == 10


def test_conditional_propagates_failing_subexpr():
    # A failing cond / then / else_ must make the whole lowering fail rather
    # than silently dropping the branch.
    for slot in ("cond", "then", "else_"):
        kwargs = {
            "cond": Name(ident="flag"),
            "then": IntLit(value=1),
            "else_": IntLit(value=2),
        }
        kwargs[slot] = _UnknownExpr()
        res = _lower_module(Conditional(**kwargs))
        assert res.module is None, f"slot {slot} should fail to lower"
        assert res.diagnostics
