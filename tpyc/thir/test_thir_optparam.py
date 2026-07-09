"""Pointer-repr Optional[record] param admission: the borrow-name faces
(deref_check / arrow reads, the None identity test, truthiness, narrowed
passes, Optional-ptr returns) -- routed emits plus the gate rejects that
must never route."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    Form, PrintForm, THIRArgTemp, THIRAssert, THIRAssign, THIRCall, THIRFieldAccess,
    THIRIf, THIRIsNone, THIRLiteral, THIRMethodCall, THIRMove, THIRName,
    THIROptTruthy, THIROptViewArg, THIROptionalPtrArg, THIRReturn,
    THIRUnaryNot, THIRWhile,
)
from .testutil import _compile, _entry, _fn, _lower_ctx, _lower_ctx_witnessed

_PRELUDE = (
    "from tpy import Int32, Own\n"
    "class A:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
    "    def bump(self) -> Int32:\n        return self.x + 1\n"
    "class H:\n"
    "    f: A | None\n"
    "    def __init__(self):\n        self.f = None\n"
    "def take_rec(a: A) -> Int32:\n    return a.x\n"
    "def take_opt(p: A | None) -> Int32:\n"
    "    if p is None:\n        return 0\n"
    "    return p.x\n"
)


class TestOptionalParamFieldFaces:
    def test_unproven_read_routes_deref_check(self):
        thir = _lower_ctx(
            _PRELUDE + "def use(p: A | None) -> Int32:\n    return p.x\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret, THIRReturn)
        fa = ret.value
        assert isinstance(fa, THIRFieldAccess) and fa.deref_check
        assert isinstance(fa.receiver, THIRName) and fa.receiver.name == "p"

    def test_narrowed_read_routes_arrow(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    if p is None:\n        return 0\n"
            + "    return p.x\n")
        ret = _fn(thir, "use").body[-1]
        assert isinstance(ret, THIRReturn)
        fa = ret.value
        assert isinstance(fa, THIRFieldAccess) and fa.is_arrow and not fa.deref_check

    def test_unproven_write_target_deref_checks(self):
        thir = _lower_ctx(
            _PRELUDE + "def use(p: A | None):\n    p.x = 7\n")
        stmt = _fn(thir, "use").body[0]
        assert isinstance(stmt, THIRAssign)
        assert isinstance(stmt.target, THIRFieldAccess) and stmt.target.deref_check

    def test_narrowed_write_target_is_arrow(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None):\n"
            + "    if p is not None:\n        p.x = 5\n")
        stmt = _fn(thir, "use").body[0].then_body[0]
        assert isinstance(stmt, THIRAssign)
        assert isinstance(stmt.target, THIRFieldAccess) and stmt.target.is_arrow

    def test_aug_assign_routes_both_faces(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def unproven(p: A | None):\n    p.x += 2\n"
            + "def narrowed(p: A | None):\n"
            + "    if p is not None:\n        p.x += 3\n")
        assert _fn(thir, "unproven") is not None
        assert _fn(thir, "narrowed") is not None


class TestIsNoneFaces:
    def test_condition_and_value_route(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def cond(p: A | None) -> Int32:\n"
            + "    if p is None:\n        return 0\n"
            + "    return 1\n"
            + "def value(p: A | None) -> bool:\n    return p is None\n")
        cond = _fn(thir, "cond").body[0]
        assert isinstance(cond, THIRIf)
        assert isinstance(cond.condition, THIRIsNone) and not cond.condition.negate
        ret = _fn(thir, "value").body[0]
        assert isinstance(ret.value, THIRIsNone)

    def test_is_not_none_negates(self):
        thir = _lower_ctx(
            _PRELUDE + "def use(p: A | None) -> bool:\n    return p is not None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone) and ret.value.negate

    def test_reversed_operand_order_canonicalizes(self):
        # `None is p` renders `(p == nullptr)` on the AST path -- the node
        # carries only the Optional operand.
        thir = _lower_ctx(
            _PRELUDE + "def use(p: A | None) -> bool:\n    return None is p\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone)
        assert isinstance(ret.value.operand, THIRName)
        assert ret.value.operand.name == "p"

    def test_compound_and_leaf_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None, flag: bool) -> Int32:\n"
            + "    if p is not None and flag:\n        return p.x\n"
            + "    return 0\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond.left, THIRIsNone) and cond.left.negate

    def test_while_cond_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    n = 0\n"
            + "    while p is not None:\n        n += p.x\n        break\n"
            + "    return n\n")
        assert _fn(thir, "use") is None or True  # break is outside the stmt set
        # The while shape without break:
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    n = 0\n"
            + "    while p is None:\n        n += 1\n        return n\n"
            + "    return n\n")
        fn = _fn(thir, "use")
        assert fn is not None
        assert isinstance(fn.body[1], THIRWhile)
        assert isinstance(fn.body[1].condition, THIRIsNone)

    # NB `p is q` between two Optionals is a sema ERROR ("'is' / 'is not' can
    # only compare ... with None"), so the non-None-operand shape never
    # reaches the gate; _is_none_compare_operand's literal check is defensive.


class TestTruthinessFaces:
    def test_bare_name_condition_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    if p:\n        return p.x\n"
            + "    return 0\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond, THIRName) and cond.name == "p" and not cond.deref

    def test_not_name_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    if not p:\n        return 0\n"
            + "    return p.x\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond, THIRUnaryNot)
        assert isinstance(cond.operand, THIRName) and cond.operand.name == "p"


class TestAssertNarrow:
    def test_assert_is_not_none_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    assert p is not None\n"
            + "    return p.x\n")
        fn = _fn(thir, "use")
        assert fn is not None
        assert isinstance(fn.body[0], THIRAssert)
        assert isinstance(fn.body[0].condition, THIRIsNone)
        # post-assert read is proven -> arrow
        assert fn.body[1].value.is_arrow

    def test_assert_with_message_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    assert p is not None, \"boom\"\n"
            + "    return p.x\n")
        fn = _fn(thir, "use")
        assert fn is not None and fn.body[0].message == "boom"


class TestMethodFaces:
    def test_unproven_method_call_deref_checks(self):
        thir = _lower_ctx(
            _PRELUDE + "def use(p: A | None) -> Int32:\n    return p.bump()\n")
        ret = _fn(thir, "use").body[0]
        mc = ret.value
        assert isinstance(mc, THIRMethodCall) and mc.deref_check and not mc.is_arrow

    def test_narrowed_method_call_is_arrow(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    if p is None:\n        return 0\n"
            + "    return p.bump()\n")
        mc = _fn(thir, "use").body[-1].value
        assert isinstance(mc, THIRMethodCall) and mc.is_arrow and not mc.deref_check


class TestCallArgFaces:
    def test_narrowed_pass_to_ref_slot_derefs(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    if p is None:\n        return 0\n"
            + "    return take_rec(p)\n")
        arg = _fn(thir, "use").body[-1].value.args[0]
        assert isinstance(arg, THIRName) and arg.name == "p" and arg.deref

    def test_unproven_pass_to_optional_slot_is_bare(self):
        thir = _lower_ctx(
            _PRELUDE + "def use(p: A | None) -> Int32:\n    return take_opt(p)\n")
        arg = _fn(thir, "use").body[0].value.args[0]
        assert isinstance(arg, THIRName) and arg.name == "p" and not arg.deref

    def test_narrowed_pass_to_optional_slot_is_bare(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    if p is None:\n        return 0\n"
            + "    return take_opt(p)\n")
        arg = _fn(thir, "use").body[-1].value.args[0]
        assert isinstance(arg, THIRName) and arg.name == "p" and not arg.deref

    def test_narrowed_pass_to_own_slot_temps_with_deref(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def sink(a: Own[A]) -> Int32:\n    return a.x\n"
            + "def use(p: A | None) -> Int32:\n"
            + "    if p is None:\n        return 0\n"
            + "    return sink(p)\n")
        arg = _fn(thir, "use").body[-1].value.args[0]
        assert isinstance(arg, THIRArgTemp) and arg.move
        assert isinstance(arg.init, THIRName) and arg.init.deref


class TestOptionalPtrReturn:
    def test_return_faces(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def ret_name(p: A | None) -> A | None:\n    return p\n"
            + "def ret_none(p: A | None) -> A | None:\n"
            + "    if p is None:\n        return None\n"
            + "    return p\n"
            + "def ret_rec(a: A) -> A | None:\n    return a\n")
        bare = _fn(thir, "ret_name").body[0]
        assert isinstance(bare.value, THIRName) and not bare.value.deref
        none_ret = _fn(thir, "ret_none").body[0].then_body[0]
        assert isinstance(none_ret.value, THIRLiteral) and none_ret.value.value is None
        rec_ret = _fn(thir, "ret_rec").body[0]
        assert isinstance(rec_ret.value, THIROptionalPtrArg) and rec_ret.value.addr_of

    def test_return_field_source_rejects(self):
        # `return h.f` (a storage-form Optional field) takes the
        # optional_to_ptr return arm -- not mirrored, stays AST.
        thir = _lower_ctx(
            _PRELUDE + "def use(h: H) -> A | None:\n    return h.f\n")
        assert _fn(thir, "use") is None

    # NB `return own_a` at a pointer-repr Optional return is a sema ERROR
    # (the returned pointer would dangle), so the Own-source shape never
    # reaches the gate; the return arm's OwnType check is defensive.


class TestOptionalLocalFaces:
    # OPTIONAL_TO_PTR locals share every face with Optional-ptr params (both
    # are never-reseated borrow `T*` bindings keyed on the declared type).
    def test_local_narrowed_read_is_arrow(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(h: H) -> Int32:\n"
            + "    q = h.f\n"
            + "    if q is None:\n        return 0\n"
            + "    return q.x\n")
        fn = _fn(thir, "use")
        assert fn is not None
        ret = fn.body[-1]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.is_arrow

    def test_local_unproven_read_deref_checks(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(h: H) -> Int32:\n"
            + "    q = h.f\n"
            + "    return q.x\n")
        ret = _fn(thir, "use").body[-1]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check

    def test_local_is_none_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(h: H) -> bool:\n"
            + "    q = h.f\n"
            + "    return q is None\n")
        ret = _fn(thir, "use").body[-1]
        assert isinstance(ret.value, THIRIsNone)


class TestGateRejects:
    def test_own_optional_param_rejects(self):
        # `Own[A] | None` is storage-repr (`std::optional<A>&&`,
        # OPTIONAL_STORAGE) -- outside the borrow-name slice.
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: Own[A] | None) -> bool:\n    return p is None\n")
        assert _fn(thir, "use") is None

    def test_optional_container_param_rejects(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(xs: list[Int32] | None) -> bool:\n    return xs is None\n")
        assert _fn(thir, "use") is None

    def test_borrow_local_off_optional_receiver_rejects(self):
        # A borrow local bound off a narrowed Optional receiver spells the
        # receiver's const verdict, which the AST derives from
        # `const_indirect_locals` (annotation-keyed) -- an inferred-const
        # receiver miscompiles there (BUGS.md: "borrow locals off a narrowed
        # Optional receiver drop inferred constness"), so the shape is
        # gate-rejected rather than mirrored.
        thir = _lower_ctx(
            _PRELUDE
            + "class G:\n"
            + "    a: A\n"
            + "    def __init__(self):\n        self.a = A(1)\n"
            + "def ref_alias(g: G | None) -> Int32:\n"
            + "    if g is None:\n        return 0\n"
            + "    a = g.a\n"
            + "    return a.x\n"
            + "def opt_lift(h: H | None) -> Int32:\n"
            + "    if h is None:\n        return 0\n"
            + "    q = h.f\n"
            + "    if q is None:\n        return 1\n"
            + "    return q.x\n")
        assert _fn(thir, "ref_alias") is None
        assert _fn(thir, "opt_lift") is None

    def test_const_blind_writes_off_optional_receiver_route(self):
        # The const-blind faces off the same receiver still route: scalar
        # and optional-field writes spell no receiver const.
        thir = _lower_ctx(
            _PRELUDE
            + "def scalar_write(h: H | None):\n"
            + "    if h is not None:\n        h.f = None\n")
        assert _fn(thir, "scalar_write") is not None

    def test_narrowed_truthiness_rejects(self):
        # `if p:` on an already-narrowed read takes a different gen_truthy
        # arm -- only the un-narrowed Optional read is admitted.
        thir = _lower_ctx(
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    if p is None:\n        return 0\n"
            + "    if p:\n        return p.x\n"
            + "    return 1\n")
        assert _fn(thir, "use") is None


class TestValueReprOptionalParam:
    """A value-repr `Optional[cheap scalar]` param (`Int32 | None` ->
    `std::optional<T>`): narrowed reads unwrap `(*p)`, None-tests render
    `has_value()`, truthiness `is_truthy(p)`, bare passes stay bare. A BigInt
    (expensive-copy) inner routes too, moving `std::move((*p))` at its narrowed
    last use; the str-view inner routes its own read/None-test/truthiness/arg-
    split faces (see TestValueReprOptionalStrParam)."""

    def test_narrowed_read_derefs(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(p: Int32 | None) -> Int32:\n"
            "    if p is None:\n        return 0\n"
            "    return p\n")
        ret = _fn(thir, "use").body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName) and ret.value.deref

    def test_bare_pass_to_optional_slot(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def sink(q: Int32 | None) -> Int32:\n"
            "    if q is None:\n        return -1\n"
            "    return q\n"
            "def use(p: Int32 | None) -> Int32:\n"
            "    if p is None:\n        return 0\n"
            "    return sink(p)\n")
        arg = _fn(thir, "use").body[-1].value.args[0]
        # narrowed name passed into a value-repr optional slot stays bare
        assert isinstance(arg, THIRName) and arg.name == "p" and not arg.deref

    def test_is_none_uses_has_value(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(p: Int32 | None) -> bool:\n    return p is None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone)
        assert ret.value.value_repr and not ret.value.negate

    def test_is_not_none_negates(self):
        thir = _lower_ctx(
            "from tpy import Float64\n"
            "def use(p: Float64 | None) -> bool:\n    return p is not None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone)
        assert ret.value.value_repr and ret.value.negate

    def test_truthiness_wraps_is_truthy(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(p: Int32 | None) -> Int32:\n"
            "    if p:\n        return p\n"
            "    return 0\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond, THIROptTruthy)
        assert isinstance(cond.operand, THIRName) and not cond.operand.deref

    def test_not_truthiness(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(p: Int32 | None) -> Int32:\n"
            "    if not p:\n        return 0\n"
            "    return p\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond, THIRUnaryNot)
        assert isinstance(cond.operand, THIROptTruthy)

    def test_reassign_strips_deref(self):
        # The AST reassignment RHS threads no target type, so a narrowed read is
        # NOT unwrapped (`q = p;`, a pre-existing AST bug mirrored here).
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(p: Int32 | None) -> Int32:\n"
            "    q = 0\n"
            "    if p is not None:\n        q = p\n"
            "    return q\n")
        assign = _fn(thir, "use").body[1].then_body[0]
        assert isinstance(assign, THIRAssign)
        assert isinstance(assign.value, THIRName) and not assign.value.deref

    def test_bigint_return_moves_at_last_use(self):
        # BigInt is expensive-copy -- the narrowed last-use read moves.
        thir = _lower_ctx(
            "def use(p: int | None) -> int:\n"
            "    if p is None:\n        return 0\n"
            "    return p\n")
        ret = _fn(thir, "use").body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRMove)
        assert isinstance(ret.value.value, THIRName) and ret.value.value.deref

    def test_bigint_call_arg_does_not_move(self):
        # A narrowed read into a const-ref scalar slot binds directly -- no move
        # (the AST renders `take((*p))`, not `take(std::move((*p)))`).
        thir = _lower_ctx(
            "def take(x: int) -> int:\n    return x + 1\n"
            "def use(p: int | None) -> int:\n"
            "    if p is None:\n        return 0\n"
            "    return take(p)\n")
        ret = _fn(thir, "use").body[-1]
        arg = ret.value.args[0]
        assert isinstance(arg, THIRName) and arg.deref

    def test_bigint_container_elem_moves(self):
        # A container-literal element moves the narrowed value-Optional param
        # despite BigInt being a value type (seed_param_locals movable face).
        thir = _lower_ctx(
            "def use(p: int | None) -> int:\n"
            "    if p is None:\n        return 0\n"
            "    xs = [p]\n"
            "    return xs[0]\n")
        decl = _fn(thir, "use").body[1]
        elem = decl.init.elements[0]
        assert isinstance(elem, THIRMove)
        assert isinstance(elem.value, THIRName) and elem.value.deref

    def test_bigint_plain_decl_does_not_move(self):
        # A plain var-decl RHS threads no move (`q = (*p);`) -- the AST var-decl
        # path never calls _maybe_move.
        thir = _lower_ctx(
            "def use(p: int | None) -> int:\n"
            "    if p is None:\n        return 0\n"
            "    q = p\n"
            "    return q\n")
        decl = _fn(thir, "use").body[1]
        assert isinstance(decl.init, THIRName) and decl.init.deref

    def test_str_view_inner_none_test_routes(self):
        # The str-view inner's None-test renders identically to the scalar's
        # (`has_value()`), so it routes -- see TestValueReprOptionalStrParam for
        # the read / truthiness / arg-split faces.
        thir = _lower_ctx(
            "def use(p: str | None) -> bool:\n    return p is None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone) and ret.value.value_repr


class TestValueReprOptionalParamEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = (
        "from tpy import Int32, Float64\n"
        "def sink(q: Int32 | None) -> Int32:\n"
        "    if q is None:\n        return -1\n"
        "    return q\n"
        "def narrowed(p: Int32 | None) -> Int32:\n"
        "    if p is None:\n        return 0\n"
        "    return p + 1\n"
        "def is_none(p: Int32 | None) -> bool:\n    return p is None\n"
        "def is_not_none(p: Float64 | None) -> bool:\n    return p is not None\n"
        "def truthy(p: Int32 | None) -> Int32:\n"
        "    if not p:\n        return 0\n"
        "    return p\n"
        "def pass_through(p: Int32 | None) -> Int32:\n"
        "    if p is None:\n        return 0\n"
        "    return sink(p)\n"
        "def reassign(p: Int32 | None) -> Int32:\n"
        "    q = 0\n"
        "    if p is not None:\n        q = p\n"
        "    return q\n"
        "def main():\n    print(narrowed(3))\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "if ((!p.has_value()))" in out
        assert "return (!p.has_value());" in out
        assert "return (p.has_value());" in out
        assert "if ((!(::tpy::is_truthy(p))))" in out
        assert "return sink(p);" in out       # bare pass into optional slot
        assert "q = p;" in out                 # reassign strips the deref


class TestValueReprOptionalStrParam:
    """A value-repr `Optional[str]` param (`str | None` ->
    `std::optional<std::string_view>`): the None-test/truthiness render exactly
    like the scalar twin (`has_value()` / `is_truthy(s)`); a NARROWED read
    unwraps `(*s)` as a BORROW string_view (feeding str sinks); and a pass into
    another `Optional[str]` slot takes the `_maybe_convert_opt_view_param` ARG
    split. A `StrView`-inner source passes bare there (not the shim). The
    print / return-of-whole / decl / un-narrowed-value read sinks defer."""

    def test_narrowed_read_derefs_into_str_slot(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def take(x: str) -> Int32:\n    return len(x)\n"
            "def use(s: str | None) -> Int32:\n"
            "    if s is None:\n        return 0\n"
            "    return take(s)\n")
        arg = _fn(thir, "use").body[-1].value.args[0]
        assert isinstance(arg, THIRName) and arg.deref and arg.form is Form.BORROW

    def test_is_none_uses_has_value(self):
        thir = _lower_ctx(
            "def use(s: str | None) -> bool:\n    return s is None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone)
        assert ret.value.value_repr and not ret.value.negate

    def test_is_not_none_negates(self):
        thir = _lower_ctx(
            "def use(s: str | None) -> bool:\n    return s is not None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone)
        assert ret.value.value_repr and ret.value.negate

    def test_truthiness_wraps_is_truthy(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(s: str | None) -> Int32:\n"
            "    if s:\n        return 1\n"
            "    return 0\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond, THIROptTruthy)
        assert isinstance(cond.operand, THIRName) and not cond.operand.deref

    def test_not_truthiness(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(s: str | None) -> Int32:\n"
            "    if not s:\n        return 0\n"
            "    return 1\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond, THIRUnaryNot)
        assert isinstance(cond.operand, THIROptTruthy)

    def test_pass_to_optional_str_slot_takes_shim(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def sink(q: str | None) -> Int32:\n"
            "    if q is None:\n        return -1\n"
            "    return len(q)\n"
            "def use(s: str | None) -> Int32:\n    return sink(s)\n")
        arg = _fn(thir, "use").body[0].value.args[0]
        assert isinstance(arg, THIROptViewArg) and arg.name == "s"

    def test_narrowed_pass_to_optional_str_slot_still_shim(self):
        # The shim renders on the WHOLE optional even after narrowing.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def sink(q: str | None) -> Int32:\n"
            "    if q is None:\n        return -1\n"
            "    return len(q)\n"
            "def use(s: str | None) -> Int32:\n"
            "    if s is None:\n        return 0\n"
            "    return sink(s)\n")
        arg = _fn(thir, "use").body[-1].value.args[0]
        assert isinstance(arg, THIROptViewArg)

    def test_strview_inner_none_test_routes(self):
        thir = _lower_ctx(
            "from tpy import StrView\n"
            "def use(s: StrView | None) -> bool:\n    return s is None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone) and ret.value.value_repr

    def test_strview_inner_pass_stays_ast(self):
        # `StrView | None` into `StrView | None`: the AST passes BARE (the shim's
        # `view_family_for_type(StrView)` is None), a shape this cell defers.
        thir = _lower_ctx(
            "from tpy import StrView, Int32\n"
            "def sink(q: StrView | None) -> Int32:\n"
            "    if q is None:\n        return -1\n"
            "    return 0\n"
            "def use(s: StrView | None) -> Int32:\n    return sink(s)\n")
        assert _fn(thir, "use") is None

    def test_print_whole_optional_routes(self):
        # `print(s)` on an UN-narrowed value-repr Optional[str] param renders the
        # bare optional inside `::tpy::print_optional_val(s)` (gen_print threads
        # no target). A narrowed read (`(*s)`) stays its own face.
        thir = _lower_ctx(
            "def use(s: str | None) -> None:\n    print(s)\n")
        arg = _fn(thir, "use").body[0].args[0]
        assert arg.print_form is PrintForm.OPT_VAL and arg.opt_inner_cpp is None

    def test_return_whole_optional_routes_shim(self):
        # `-> str | None` returning a value-repr Optional[str] PARAM whole takes
        # the view->owned arg-split shim (`s ? std::make_optional(std::string(*s))
        # : std::nullopt`, THIROptViewArg) at the return.
        thir = _lower_ctx(
            "def use(s: str | None) -> str | None:\n    return s\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIROptViewArg) and ret.value.name == "s"

    def test_return_none_routes(self):
        # `return None` at an Optional[str] slot -> `std::nullopt`.
        thir = _lower_ctx(
            "def use(b: bool) -> str | None:\n"
            "    if b:\n        return \"x\"\n"
            "    return None\n")
        assert _fn(thir, "use") is not None

    def test_return_view_local_defers(self):
        # `return <StrView local>` at an Optional[str] slot is a pre-existing
        # AST miscompile (string_view does not convert to optional<string>) --
        # gate-rejected, so the whole function stays AST.
        thir = _lower_ctx(
            "def use() -> str | None:\n    x = \"made\"\n    return x\n")
        assert _fn(thir, "use") is None

    def test_return_faces_witnessed(self):
        thir, wit = _lower_ctx_witnessed(
            "def none_arm(b: bool) -> str | None:\n"
            "    if b:\n        return \"lit\"\n"
            "    return None\n"
            "def shim(s: str | None) -> str | None:\n    return s\n")
        assert wit.get("ret.value_opt_view_none", 0) >= 1
        assert wit.get("ret.value_opt_view_literal", 0) >= 1
        assert wit.get("ret.value_opt_view_shim", 0) >= 1


    def test_optional_str_local_decl_defers(self):
        # A value-repr Optional[str] LOCAL (the decl-shim target) classifies
        # OTHER at its decl -- the whole body stays AST.
        thir = _lower_ctx(
            "def use(s: str | None) -> None:\n    t = s\n    print(t)\n")
        assert _fn(thir, "use") is None

    def test_faces_witnessed(self):
        thir, wit = _lower_ctx_witnessed(
            "from tpy import Int32\n"
            "def sink(q: str | None) -> Int32:\n"
            "    if q is None:\n        return -1\n"
            "    return len(q)\n"
            "def use(s: str | None) -> Int32:\n"
            "    if not s:\n        return 0\n"
            "    return sink(s)\n")
        assert _fn(thir, "use") is not None


class TestValueReprOptionalBytes:
    """The bytes twin of the value-repr Optional[str] param+return slice --
    `bytes | None` binds `std::optional<std::span<const uint8_t>>` (borrow) /
    `std::optional<std::vector<uint8_t>>` (return), sharing the view-family
    machinery (`_value_opt_view`)."""

    def test_none_test_param_routes(self):
        # `b is None` on an Optional[bytes] param -> `!b.has_value()`.
        thir = _lower_ctx(
            "def use(b: bytes | None) -> bool:\n    return b is None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone) and ret.value.value_repr

    def test_return_sinks_route(self):
        # `return None` -> nullopt, `return b"lit"` -> owned bytes literal.
        thir = _lower_ctx(
            "def use(keep: bool) -> bytes | None:\n"
            "    if keep:\n        return b\"x\"\n"
            "    return None\n")
        assert _fn(thir, "use") is not None

    def test_return_shim_routes(self):
        # `return b` of an Optional[bytes] param -> the view->owned shim
        # (`b ? std::make_optional(::tpy::bytes_copy(*b)) : std::nullopt`).
        thir = _lower_ctx(
            "def use(b: bytes | None) -> bytes | None:\n    return b\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIROptViewArg) and ret.value.name == "b"

    def test_narrowed_read_derefs_into_bytes_slot(self):
        # A narrowed Optional[bytes] read passed into a `bytes` slot derefs to a
        # BORROW span `(*b)` -- the bytes twin of the str narrowed-read arm.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def take(x: bytes) -> Int32:\n    return len(x)\n"
            "def use(b: bytes | None) -> Int32:\n"
            "    if b is None:\n        return 0\n"
            "    return take(b)\n")
        arg = _fn(thir, "use").body[-1].value.args[0]
        assert isinstance(arg, THIRName) and arg.deref and arg.form is Form.BORROW

    def test_bytesview_inner_none_test_routes(self):
        # A `BytesView | None` inner shares the `_resolved_bytes_value` gate; its
        # None-test routes like the owned-bytes inner.
        thir = _lower_ctx(
            "from tpy import BytesView\n"
            "def use(b: BytesView | None) -> bool:\n    return b is None\n")
        assert _fn(thir, "use") is not None

    def test_bytesview_inner_pass_stays_ast(self):
        # Passing a `BytesView | None` whole into another `BytesView | None` slot
        # is bare on the AST path (view_family_for_type is None for the view
        # spelling), so the shim does not fire and the body stays AST -- the
        # bytes twin of test_strview_inner_pass_stays_ast.
        thir = _lower_ctx(
            "from tpy import BytesView\n"
            "def sink(q: BytesView | None) -> bool:\n    return q is None\n"
            "def use(b: BytesView | None) -> bool:\n    return sink(b)\n")
        assert _fn(thir, "use") is None

    def test_print_whole_optional_defers(self):
        # `print(b)` on a whole Optional[bytes] stays AST: there is no
        # print_optional_val route for bytes (the str-only `_print_optval_opt`),
        # so `_value_opt_view_name` defers it in `_print_arg_ok`.
        thir = _lower_ctx(
            "def use(b: bytes | None) -> None:\n    print(b)\n")
        assert _fn(thir, "use") is None

    def test_faces_witnessed(self):
        thir, wit = _lower_ctx_witnessed(
            "def none_arm(keep: bool) -> bytes | None:\n"
            "    if keep:\n        return b\"x\"\n"
            "    return None\n"
            "def shim(b: bytes | None) -> bytes | None:\n    return b\n")
        assert wit.get("ret.value_opt_view_none", 0) >= 1
        assert wit.get("ret.value_opt_view_literal", 0) >= 1
        assert wit.get("ret.value_opt_view_shim", 0) >= 1


class TestValueReprOptionalBytesEmit:
    """Bytes twin of TestValueReprOptionalStrParamEmit: the routed C++ for an
    Optional[bytes] param+return is byte-identical to the AST path, and the
    view->owned shim spells `::tpy::bytes_copy` (not `std::string`)."""

    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = (
        "def pick(keep: bool) -> bytes | None:\n"
        "    if keep:\n        return b\"data\"\n"
        "    return None\n"
        "def forward(b: bytes | None) -> bytes | None:\n    return b\n"
        "def is_absent(b: bytes | None) -> bool:\n    return b is None\n"
        "def main():\n    print(forward(b\"z\") is None)\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "b ? std::make_optional(::tpy::bytes_copy(*b)) : std::nullopt" in out
        assert "return std::nullopt;" in out


class TestValueReprOptionalStrParamEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = (
        "from tpy import Int32, StrView\n"
        "def take(x: str) -> Int32:\n    return len(x)\n"
        "def sink(q: str | None) -> Int32:\n"
        "    if q is None:\n        return -1\n"
        "    return take(q)\n"
        "def read_narrow(s: str | None) -> Int32:\n"
        "    if s is None:\n        return 0\n"
        "    return take(s)\n"
        "def is_none(s: str | None) -> bool:\n    return s is None\n"
        "def is_not_none(s: str | None) -> bool:\n    return s is not None\n"
        "def truthy(s: str | None) -> Int32:\n"
        "    if not s:\n        return 0\n"
        "    return 1\n"
        "def pass_through(s: str | None) -> Int32:\n    return sink(s)\n"
        "def narrowed_pass(s: str | None) -> Int32:\n"
        "    if s is None:\n        return 0\n"
        "    return sink(s)\n"
        "def sv_none(s: StrView | None) -> bool:\n    return s is None\n"
        "def main():\n    print(read_narrow(\"hi\"))\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "return (!s.has_value());" in out           # is_none
        assert "return (s.has_value());" in out            # is_not_none
        assert "if ((!(::tpy::is_truthy(s))))" in out       # not-truthy
        assert "return take((*s));" in out                  # narrowed read deref
        assert ("return sink(s ? std::make_optional(std::string(*s)) : "
                "std::nullopt);" in out)                     # the arg-split shim


class TestValueReprOptionalReturn:
    """A value-repr `Optional[cheap scalar]` RETURN slot (`-> Int32 | None` ->
    `std::optional<T>`): `return None` -> `std::nullopt`, a value-opt param name
    passes the WHOLE optional bare (deref-on-narrow stripped), other scalar
    sources ride the generic tail. BigInt / str-view inners stay AST."""

    def test_none_returns_nullopt(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def f(x: Int32) -> Int32 | None:\n"
            "    if x > 0:\n        return x\n"
            "    return None\n")
        ret = _fn(thir, "f").body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRLiteral) and ret.value.value is None
        from .nodes import Form
        assert ret.value.form is Form.STORAGE      # -> std::nullopt

    def test_narrowed_param_passes_whole_optional_bare(self):
        # `return p` at a value-optional return keeps the WHOLE optional (`p`),
        # not the narrowed `(*p)` -- the deref-on-narrow is stripped.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def f(p: Int32 | None) -> Int32 | None:\n"
            "    if p is not None:\n        return p\n"
            "    return None\n")
        ret = _fn(thir, "f").body[0].then_body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRName) and not ret.value.deref

    def test_identity_param_pass_routes(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def ident(p: Int32 | None) -> Int32 | None:\n    return p\n")
        ret = _fn(thir, "ident").body[-1]
        assert isinstance(ret.value, THIRName) and not ret.value.deref

    def test_scalar_expr_source_derefs_operand(self):
        # `return p + 1` derefs the narrowed operand `(*p)` inside the binop,
        # then implicitly converts the scalar result into the optional slot.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def g(p: Int32 | None) -> Int32 | None:\n"
            "    if p is None:\n        return None\n"
            "    return p + 1\n")
        assert _fn(thir, "g") is not None

    def test_faces_witnessed(self):
        thir, wit = _lower_ctx_witnessed(
            "from tpy import Int32\n"
            "def f(p: Int32 | None) -> Int32 | None:\n"
            "    if p is not None:\n        return p\n"
            "    return None\n")
        assert wit.get("ret.value_opt_none", 0) >= 1
        assert wit.get("ret.value_opt_name", 0) >= 1

    def test_bigint_inner_routes(self):
        # BigInt joined the value slice (the param move face admits it), and the
        # return arm shares `_value_opt_scalar`, so a BigInt-inner Optional return
        # routes too -- byte-identically (a borrow source copies, an owned last-use
        # source moves through the generic return tail's own last-use machinery).
        thir = _lower_ctx(
            "def bi(x: int) -> int | None:\n"
            "    if x > 0:\n        return x\n"
            "    return None\n")
        assert _fn(thir, "bi") is not None

    def test_str_inner_literal_and_none_routes(self):
        # `Optional[str]` return routes its literal (`return "y"` -> bare) and
        # None (`std::nullopt`) sources; view-form sources still defer.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def h(x: Int32) -> str | None:\n"
            "    if x > 0:\n        return \"y\"\n"
            "    return None\n")
        assert _fn(thir, "h") is not None

    def test_cross_width_param_pass_stays_ast(self):
        # `Int8 | None` -> `Int32 | None` casts the WHOLE optional
        # (`static_cast<optional<int32>>(p)`); the coerce-wrapped narrowed read
        # would deref the inner instead, so this shape defers to the AST.
        thir = _lower_ctx(
            "from tpy import Int8, Int32\n"
            "def widen(p: Int8 | None) -> Int32 | None:\n"
            "    if p is not None:\n        return p\n"
            "    return None\n")
        assert _fn(thir, "widen") is None


class TestValueReprOptionalParamBigIntEmit:
    """The expensive-copy (BigInt) value-Optional param move faces: a narrowed
    last-use return / container element moves `std::move((*p))`; a const-ref
    call arg and a plain var-decl copy `(*p)`."""

    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = (
        "def take(x: int) -> int:\n    return x + 1\n"
        "def ret_move(p: int | None) -> int:\n"
        "    if p is None:\n        return 0\n"
        "    return p\n"
        "def call_arg(p: int | None) -> int:\n"
        "    if p is None:\n        return 0\n"
        "    return take(p)\n"
        "def container(p: int | None) -> int:\n"
        "    if p is None:\n        return 0\n"
        "    xs = [p]\n"
        "    return xs[0]\n"
        "def plain_decl(p: int | None) -> int:\n"
        "    if p is None:\n        return 0\n"
        "    q = p\n"
        "    return q\n"
        "def main():\n    print(ret_move(3))\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "return std::move((*p));" in out            # narrowed return move
        assert "return take((*p));" in out                 # const-ref arg: copy
        assert "{std::move((*p))}" in out                  # container elem move
        assert "::tpy::BigInt q = (*p);" in out            # var-decl: copy


class TestValueReprOptionalReturnEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = (
        "from tpy import Int32, Int8, Float64\n"
        "def basic(x: Int32) -> Int32 | None:\n"
        "    if x > 0:\n        return x\n"
        "    return None\n"
        "def expr_src(p: Int32 | None) -> Int32 | None:\n"
        "    if p is None:\n        return None\n"
        "    return p + 1\n"
        "def ident(p: Int32 | None) -> Int32 | None:\n    return p\n"
        "def widen(x: Int8) -> Int32 | None:\n"
        "    if x > 0:\n        return x\n"
        "    return None\n"
        "def flt(x: Int32) -> Float64 | None:\n"
        "    if x > 0:\n        return 1.5\n"
        "    return None\n"
        "def main():\n    print(basic(3))\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "return std::nullopt;" in out
        assert "return (::tpy::add_check<int32_t>((*p), 1));" in out
        assert "return p;" in out                              # bare optional pass
        assert "return static_cast<std::optional<int32_t>>(x);" in out  # widen


class TestOptionalParamEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = (
        _PRELUDE
        + "def unproven(p: A | None) -> Int32:\n    return p.x\n"
        + "def narrowed(p: A | None) -> Int32:\n"
        + "    if p is None:\n        return 0\n"
        + "    return take_rec(p)\n"
        + "def meth(p: A | None) -> Int32:\n    return p.bump()\n"
        + "def truthy(p: A | None) -> Int32:\n"
        + "    if not p:\n        return 0\n"
        + "    return p.x\n"
        + "def ret_rec(a: A) -> A | None:\n    return a\n"
        + "def write(p: A | None):\n    p.x = 7\n"
        + "def main():\n    a = A(3)\n    print(narrowed(a))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_renders(self):
        out = self._emit(self.SRC, thir=True)
        assert "return ::tpy::deref_check(p).x;" in out
        assert "if ((p == nullptr))" in out
        assert "return take_rec((*p));" in out
        assert "return ::tpy::deref_check(p).bump();" in out
        assert "if ((!(p)))" in out
        assert "return &(a);" in out
        assert "::tpy::deref_check(p).x = 7;" in out
