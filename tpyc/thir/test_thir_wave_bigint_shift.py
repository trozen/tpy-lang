"""A fits-i64 BigInt binop at a SLOT-THREADED position renders the full
operator on both paths -- `_gen_binop` folds only when `target_type is None`."""

from __future__ import annotations

from .testutil import (_lower_ctx, _fn, _assert_byte_identical,
                       _assert_routes_byte_identical)


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

    def test_nested_operand_shift_routes(self):
        # `(1 << 100) | (1 << 50)`: the OUTER `|` is slot-threaded, and the
        # AST threads its own operand target down (`gen_expr_deref(left,
        # receiver_type)`), so the inner shifts render the operator too --
        # `_ExprUse.slot_threaded` mirrors the chain at operand slots.
        src = ("def use() -> None:\n"
               "    b: int = (1 << 100) | (1 << 50)\n"
               "    print(b)\n")
        _assert_routes_byte_identical(src)

    def test_operand_slot_threads_at_targetless_outer(self):
        # The operand-slot threading is position-independent: an outer
        # binop at a TARGET-LESS sink (print arg, one name operand so the
        # whole tree cannot fold) still renders its operand shifts as
        # operators on both paths.
        src = ("def use() -> None:\n"
               "    x: int = 5\n"
               "    print(x + (1 << 50))\n")
        _assert_routes_byte_identical(src)

    def test_fixed_int_call_arg_binop_routes(self):
        # `Int64((1 << 60) + (1 << 7))`: gen_call_arg threads the fixed-int
        # param, so the both-literal arg renders `add_check<int64_t>(
        # lshift_check<int64_t>(1, 60), ...)` -- never the fold.
        src = ("from tpy import Int64\n"
               "def use() -> None:\n"
               "    a = Int64((1 << 60) + (1 << 7))\n"
               "    print(a)\n")
        _assert_routes_byte_identical(src)

    def test_record_method_arg_binop_still_defers(self):
        # A USER-RECORD method arg is target-less on the AST path (the
        # record loop passes target_type=None), so its both-literal binop
        # FOLDS there -- the arg slot stays unflagged and the body falls
        # back rather than render the operator against a folded oracle.
        src = ("class Counter:\n"
               "    n: int\n"
               "    def __init__(self) -> None:\n"
               "        self.n = 0\n"
               "    def bump(self, k: int) -> int:\n"
               "        return self.n + k\n"
               "def use() -> None:\n"
               "    c = Counter()\n"
               "    print(c.bump((1 << 33) + 1))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

    def test_bigint_free_call_arg_binop_still_defers(self):
        # A BigInt (non-fixed) free-call param arg stays outside the row:
        # the AST render there is unverified, so the shape keeps falling
        # back. Widening needs its own oracle check first.
        src = ("def take(x: int) -> int:\n"
               "    return x\n"
               "def use() -> None:\n"
               "    print(take(1 << 62))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)
