"""A fits-i64 BigInt binop at a SLOT-THREADED position renders the full
operator on both paths -- `_gen_binop` folds only when `target_type is None`."""

from __future__ import annotations

from .testutil import _lower_ctx, _fn, _assert_byte_identical


class TestSlotThreadedBigIntBinop:
    def test_annotated_decl_renders_the_operator(self):
        # `b: int = 1 << 62` -> `((BigInt(1)) << (BigInt(62)))`, not the
        # folded literal: the decl slot is a target, so the AST does not fold.
        src = ("def use() -> None:\n"
               "    b: int = 1 << 62\n"
               "    print(b)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)

    def test_arith_sibling_routes_too(self):
        # The guard is family-wide, not shift-specific.
        src = ("def use() -> None:\n"
               "    b: int = 1000000 * 1000000\n"
               "    print(b)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestBigIntBinopBoundaries:
    def test_target_less_position_still_folds_ast_side(self):
        # A print arg is TARGET-LESS, so the AST FOLDS it. Only the positions
        # flagged `literal_fold_ok` may mirror that fold; an unflagged
        # target-less sink must keep falling back rather than render the
        # operator against a folded oracle.
        src = ("def take(x: int) -> int:\n"
               "    return x\n"
               "def use() -> None:\n"
               "    print(take(1 << 62) + (1 << 40))\n")
        thir = _lower_ctx(src)
        # Either it falls back, or it is byte-identical -- never a silent
        # operator render against a folded oracle.
        if _fn(thir, "use") is not None:
            _assert_byte_identical(src)

    def test_nested_operand_shift_stays_ast(self):
        # `(1 << 100) | (1 << 50)`: the OUTER `|` is slot-threaded, but the
        # inner shifts are operands. The AST threads its own operand target
        # down (so it renders the operator for them too) -- a chain THIR does
        # not mirror yet, so the body keeps falling back.
        src = ("def use() -> None:\n"
               "    b: int = (1 << 100) | (1 << 50)\n"
               "    print(b)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
