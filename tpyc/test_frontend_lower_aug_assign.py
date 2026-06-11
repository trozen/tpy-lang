"""Lowering converts a frontend-IR `AugAssign(target, op, value)` into a
`TpyAugAssign` with the op spelled as the augmented operator string (`+`,
`-`, ...) -- the node a plugin emits for a source-level compound-assign /
increment form. Every binop has an augmented form except logical and/or,
which are rejected at the lowering boundary; a failing sub-expression
propagates as a lowering failure.
"""

from tpyc.frontend_ir.lower import lower_module
from tpyc.frontend_ir.nodes import (
    AugAssign, BinOpKind, FrontendModule, Function, IntLit, Name)
from tpyc.parse.nodes import TpyAugAssign, TpyIntLiteral, TpyName


class _UnknownExpr:
    """A node the lowerer doesn't recognize, so _lower_expr returns None for
    it -- lets us check that AugAssign propagates a failing sub-expression."""
    loc = None


def _lower_module(stmt):
    fn = Function(name="f", body=(stmt,))
    return lower_module(FrontendModule(qname="testmod", functions=(fn,)),
                        "testplugin")


def _lower_stmt(stmt):
    res = _lower_module(stmt)
    assert res.module is not None, res.diagnostics
    return res.module.functions[0].body[0]


def test_aug_assign_add_lowers_to_plus_equals():
    out = _lower_stmt(AugAssign(
        target=Name(ident="x"), op=BinOpKind.ADD, value=IntLit(value=1)))
    assert isinstance(out, TpyAugAssign)
    assert out.op == "+"
    assert isinstance(out.target, TpyName) and out.target.name == "x"
    assert isinstance(out.value, TpyIntLiteral) and out.value.value == 1


def test_aug_assign_sub_lowers_to_minus_equals():
    out = _lower_stmt(AugAssign(
        target=Name(ident="x"), op=BinOpKind.SUB, value=IntLit(value=2)))
    assert isinstance(out, TpyAugAssign)
    assert out.op == "-"
    assert out.value.value == 2


def test_all_augmentable_ops_lower():
    # Regression guard for the gate: every binop except logical and/or has an
    # augmented form (mirrors the parser's `_BINOP_TO_STR`). Enumerated as a
    # spec, not derived from the lowerer's tables, so an over-broad rejection
    # (the `**` over-rejection this guards against) fails here.
    expected = {
        BinOpKind.ADD: "+", BinOpKind.SUB: "-", BinOpKind.MUL: "*",
        BinOpKind.TRUE_DIV: "div", BinOpKind.FLOOR_DIV: "//",
        BinOpKind.MOD: "%", BinOpKind.POW: "**",
        BinOpKind.BIT_OR: "|", BinOpKind.BIT_XOR: "^", BinOpKind.BIT_AND: "&",
        BinOpKind.LSHIFT: "<<", BinOpKind.RSHIFT: ">>",
    }
    for op, op_str in expected.items():
        out = _lower_stmt(AugAssign(
            target=Name(ident="x"), op=op, value=IntLit(value=1)))
        assert isinstance(out, TpyAugAssign), f"op {op} should lower"
        assert out.op == op_str, f"op {op} -> {out.op}, expected {op_str}"


def test_aug_assign_rejects_logical_ops():
    # `_BINOP_OP_STR` is shared with BinOp lowering and carries the logical ops,
    # which have no `&&=`/`||=` augmented form. They must be rejected at the
    # lowering boundary, not passed through to fail opaquely in sema.
    for op in (BinOpKind.LOGICAL_AND, BinOpKind.LOGICAL_OR):
        res = _lower_module(AugAssign(
            target=Name(ident="x"), op=op, value=IntLit(value=1)))
        assert res.module is None, f"op {op} should fail to lower"
        assert res.diagnostics


def test_aug_assign_propagates_failing_subexpr():
    # A failing target / value must make the whole lowering fail rather than
    # silently dropping a side of the assignment.
    for slot in ("target", "value"):
        kwargs = {
            "target": Name(ident="x"),
            "op": BinOpKind.ADD,
            "value": IntLit(value=1),
        }
        kwargs[slot] = _UnknownExpr()
        res = _lower_module(AugAssign(**kwargs))
        assert res.module is None, f"slot {slot} should fail to lower"
        assert res.diagnostics
