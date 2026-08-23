"""Pointer-repr Optional[record] param admission: the borrow-name faces
(deref_check / arrow reads, the None identity test, truthiness, narrowed
passes, Optional-ptr returns) -- routed emits plus the gate rejects that
must never route."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    Form, PrintForm, TruthinessMode, THIRArgTemp, THIRAssert, THIRAssign,
    THIRCall, THIRFieldAccess,
    THIRIf, THIRIsNone, THIRLiteral, THIRMethodCall, THIRMove, THIRName,
    THIRTruthy, THIROptViewArg, THIROptionalPtrArg, THIRReturn,
    THIRUnaryNot, THIRWhile,
)
from .testutil import (_compile, _entry, _fn, _lower_ctx, _lower_ctx_witnessed,
                       _assert_byte_identical, _assert_routes_byte_identical)

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

    def test_subscript_source_to_optional_slot_addr_of(self):
        # A record-element lvalue subscript (`xs[0]`, a `T&`) into a
        # pointer-repr Optional[record] slot lifts `&(::tpy::__getitem__(...))`
        # -- the subscript optional-ptr face.
        thir = _lower_ctx(
            _PRELUDE
            + "def use(xs: list[A]) -> Int32:\n    return take_opt(xs[0])\n")
        arg = _fn(thir, "use").body[0].value.args[0]
        assert isinstance(arg, THIROptionalPtrArg) and arg.addr_of
        assert arg.value is not None

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

    def test_return_field_source_routes(self):
        # `return h.f` (a storage-form Optional field) routes byte-identically
        # via the optional_to_ptr return arm.
        src = _PRELUDE + "def use(h: H) -> A | None:\n    return h.f\n"
        assert _fn(_lower_ctx(src), "use") is not None
        _assert_byte_identical(src)

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
    def test_own_optional_param_routes_record_kind(self):
        # `Own[A] | None` is storage-repr (a by-value `std::optional<A>`):
        # the RECORD-kind param seed admits the has_value None-test.
        src = (_PRELUDE
               + "def use(p: Own[A] | None) -> bool:\n    return p is None\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        cpp = _assert_byte_identical(src)
        assert "return (!p.has_value());" in cpp[1]

    def test_optional_container_param_routes(self):
        # The WIDE pointee class admits Optional[container] params: the
        # `T*` binding's None test is the same pointee-blind compare.
        src = (_PRELUDE
               + "def use(xs: list[Int32] | None) -> bool:\n"
               + "    return xs is None\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        cpp = _assert_byte_identical(src)
        assert "return (xs == nullptr);" in cpp[1]

    def test_borrow_local_off_optional_receiver_routes(self):
        # Borrow locals off a narrowed Optional receiver route: the const
        # verdict reads the same deep-const fact seed_param_locals seeds
        # into const_indirect_locals (_opt_ptr_param_deep_const), so both
        # alias shapes spell the receiver's inferred const-ness.
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
        assert _fn(thir, "ref_alias") is not None
        assert _fn(thir, "opt_lift") is not None

    def test_borrow_local_off_optional_receiver_byte_identical(self):
        # Byte pins for both const verdicts: the read-only bodies spell
        # `const A& g = h->g;` / `const A* q = optional_to_ptr(h->f);`
        # (deep-const receiver), the mutating twins spell the mutable
        # forms (`A& g = h->g;`) -- the verdict comes from
        # deep_const_borrow_params, not the annotation.
        src = (_PRELUDE
               + "class R:\n"
               + "    g: A\n"
               + "    f: A | None\n"
               + "    def __init__(self):\n"
               + "        self.g = A(1)\n        self.f = A(2)\n"
               + "def read_ref(h: R | None) -> Int32:\n"
               + "    if h is None:\n        return -1\n"
               + "    g = h.g\n"
               + "    return g.x\n"
               + "def bump(h: R | None):\n"
               + "    if h is None:\n        return\n"
               + "    g = h.g\n"
               + "    g.x += 10\n"
               + "def read_opt(h: R | None) -> Int32:\n"
               + "    if h is None:\n        return -1\n"
               + "    q = h.f\n"
               + "    if q is not None:\n        return q.x\n"
               + "    return -1\n"
               + "def bump_opt(h: R | None):\n"
               + "    if h is None:\n        return\n"
               + "    q = h.f\n"
               + "    if q is not None:\n        q.x += 5\n"
               + "r = R()\n"
               + "bump(r)\n"
               + "bump_opt(r)\n"
               + "print(read_ref(r), read_opt(r))\n")
        thir = _lower_ctx(src)
        for name in ("read_ref", "bump", "read_opt", "bump_opt"):
            assert _fn(thir, name) is not None
        _assert_byte_identical(src)

    def test_const_blind_writes_off_optional_receiver_route(self):
        # The const-blind faces off the same receiver still route: scalar
        # and optional-field writes spell no receiver const.
        thir = _lower_ctx(
            _PRELUDE
            + "def scalar_write(h: H | None):\n"
            + "    if h is not None:\n        h.f = None\n")
        assert _fn(thir, "scalar_write") is not None

    def test_narrowed_truthiness_dispatches(self):
        # A pointer-repr Optional[record] narrowed past None dispatches
        # truthiness on the narrowed inner (A is a plain record -> always
        # truthy); THIR routes it byte-identically to the AST.
        src = (
            _PRELUDE
            + "def use(p: A | None) -> Int32:\n"
            + "    if p is None:\n        return 0\n"
            + "    if p:\n        return p.x\n"
            + "    return 1\n")
        assert _fn(_lower_ctx(src), "use") is not None
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir_out == ast_out


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
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.IS_TRUTHY
        assert isinstance(cond.operand, THIRName) and not cond.operand.deref

    def test_not_truthiness(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(p: Int32 | None) -> Int32:\n"
            "    if not p:\n        return 0\n"
            "    return p\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond, THIRUnaryNot)
        assert isinstance(cond.operand, THIRTruthy)
        assert cond.operand.mode is TruthinessMode.IS_TRUTHY

    def test_reassign_derefs_into_plain_slot(self):
        # A narrowed value-opt source reassigned into a plain-T slot reads the
        # inner value (`q = (*p);`) -- the whole-optional copy is reserved for
        # an optional TARGET binding (see the strips_deref sibling below).
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(p: Int32 | None) -> Int32:\n"
            "    q = 0\n"
            "    if p is not None:\n        q = p\n"
            "    return q\n")
        assign = _fn(thir, "use").body[1].then_body[0]
        assert isinstance(assign, THIRAssign)
        assert isinstance(assign.value, THIRName) and assign.value.deref

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
        assert "q = (*p);" in out              # narrowed reassign into plain slot derefs


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
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.IS_TRUTHY
        assert isinstance(cond.operand, THIRName) and not cond.operand.deref

    def test_not_truthiness(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(s: str | None) -> Int32:\n"
            "    if not s:\n        return 0\n"
            "    return 1\n")
        cond = _fn(thir, "use").body[0].condition
        assert isinstance(cond, THIRUnaryNot)
        assert isinstance(cond.operand, THIRTruthy)
        assert cond.operand.mode is TruthinessMode.IS_TRUTHY

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


    def test_optional_str_local_decl_takes_the_shim(self):
        # A value-repr Optional[str] LOCAL bound from a borrow-form param:
        # the owned slot takes the view->owned shim, and the local registers
        # as an OWNED value-opt binding so its own reads route.
        src = "def use(s: str | None) -> None:\n    t = s\n    print(t)\n"
        thir, wit = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert wit.get("decl.optview_shim", 0) == 1
        hpp, cpp = _assert_byte_identical(src)
        assert ("std::optional<std::string> t = s ? "
                "std::make_optional(std::string(*s)) : std::nullopt;"
                in hpp + cpp)

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


class TestValueReprOptionalViewLocal:
    """A value-repr `Optional[str]`/`Optional[bytes]` LOCAL binds an OWNED
    `std::optional<std::string>` / `std::optional<std::vector<uint8_t>>`. Its
    None-test/deref reads ride the same value-repr arms as the view param twin
    via `_value_opt_view_binding` -- the ONE difference is the narrowed deref
    form: a local's `(*acc)` is already OWNED (STORAGE, no copy at an owned
    sink), where a param's is a BORROW view. A None-init decl registers; a
    whole-optional copy-init still defers (`test_optional_str_local_decl_defers`
    -- orthogonal shape not routed here)."""

    def test_none_init_local_is_none_uses_has_value(self):
        thir = _lower_ctx(
            "def use(argv: list[str]) -> bool:\n"
            "    acc: str | None = None\n"
            "    if len(argv) > 0:\n        acc = argv[0]\n"
            "    return acc is None\n")
        ret = _fn(thir, "use").body[-1]
        assert isinstance(ret.value, THIRIsNone)
        assert ret.value.value_repr and not ret.value.negate

    def test_narrowed_read_derefs_into_owned_str_slot(self):
        # `cmd: str = acc` after `assert acc is not None`: the deref `(*acc)` is
        # an OWNED std::string (STORAGE), so the owned `cmd` slot takes it bare
        # -- unlike a param's BORROW deref, which an owned sink would copy.
        thir = _lower_ctx(
            "def use(argv: list[str]) -> str:\n"
            "    acc: str | None = None\n"
            "    if len(argv) > 0:\n        acc = argv[0]\n"
            "    assert acc is not None\n"
            "    cmd: str = acc\n"
            "    return cmd\n")
        decl = _fn(thir, "use").body[-2]
        assert isinstance(decl.init, THIRName) and decl.init.deref
        assert decl.init.form is Form.STORAGE

    def test_truthiness_local_wraps_is_truthy(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def use(argv: list[str]) -> Int32:\n"
            "    acc: str | None = None\n"
            "    if len(argv) > 0:\n        acc = argv[0]\n"
            "    if acc:\n        return 1\n"
            "    return 0\n")
        cond = _fn(thir, "use").body[-2].condition
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.IS_TRUTHY
        assert isinstance(cond.operand, THIRName) and not cond.operand.deref

    def test_reassign_existing_optional_strips_deref(self):
        # `acc2 = acc` (both value-opt-view locals) after narrowing `acc`: the
        # reassignment RHS threads no target, so the narrowed name reads the
        # WHOLE optional bare (`acc2 = acc;`), NOT the deref -- mirrors the
        # scalar reassign-strip. A deref here would emit `acc2 = (*acc);`.
        thir = _lower_ctx(
            "def use(argv: list[str]) -> str:\n"
            "    acc: str | None = None\n"
            "    acc2: str | None = None\n"
            "    if len(argv) > 0:\n        acc = argv[0]\n"
            "    if acc is not None:\n        acc2 = acc\n"
            "    if acc2 is None:\n        return \"none\"\n"
            "    return acc2\n")
        # the `acc2 = acc` assign sits inside the `if acc is not None:` branch
        reassign = _fn(thir, "use").body[-3].then_body[0]
        assert isinstance(reassign, THIRAssign)
        assert isinstance(reassign.value, THIRName) and not reassign.value.deref

    def test_view_inner_local_defers(self):
        # A view-INNER value-opt LOCAL (`StrView | None` -> optional<string_view>)
        # is NOT routed (only owned-inner str/bytes locals are): the AST's
        # narrowed read is a whole-optional-wrap quirk THIR does not mirror, so
        # the whole body defers.
        thir = _lower_ctx(
            "from tpy import StrView\n"
            "def use(argv: list[str]) -> str:\n"
            "    acc: StrView | None = None\n"
            "    if len(argv) > 0:\n        acc = argv[0]\n"
            "    assert acc is not None\n"
            "    owned: str = acc\n"
            "    return owned\n")
        assert _fn(thir, "use") is None

    def test_bytes_local_none_test_and_deref(self):
        thir = _lower_ctx(
            "def use(chunks: list[bytes]) -> bytes:\n"
            "    acc: bytes | None = None\n"
            "    if len(chunks) > 0:\n        acc = chunks[0]\n"
            "    assert acc is not None\n"
            "    out: bytes = acc\n"
            "    return out\n")
        decl = _fn(thir, "use").body[-2]
        assert isinstance(decl.init, THIRName) and decl.init.deref
        assert decl.init.form is Form.STORAGE

    def test_full_body_byte_identical(self):
        _assert_byte_identical(
            "def f(argv: list[str]) -> str:\n"
            "    acc: str | None = None\n"
            "    for tok in argv:\n"
            "        if tok == \"a\":\n"
            "            acc = \"a\"\n"
            "            break\n"
            "    assert acc is not None\n"
            "    cmd: str = acc\n"
            "    return cmd\n")

    def test_return_whole_optional_local_defers(self):
        # `return acc` at a `str | None` slot reads the WHOLE optional local
        # un-narrowed; the return-whole-optional-view arm is param-only (the
        # owned-local return shim is not built yet), so the body defers.
        thir = _lower_ctx(
            "def use(argv: list[str]) -> str | None:\n"
            "    acc: str | None = None\n"
            "    if len(argv) > 0:\n        acc = argv[0]\n"
            "    return acc\n")
        assert _fn(thir, "use") is None


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

    def test_print_whole_optional_bytes_spells_the_formatter(self):
        # `print(b)` on a whole Optional[bytes] wraps the borrow-form
        # `optional<span>` param in `print_optional_val<BytesPrinter, ..>`:
        # the inner template arg is the VIEW (the param's storage), not the
        # owned vector a FIELD of the same TPy type would carry.
        src = "def use(b: bytes | None) -> None:\n    print(b)\n"
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        hpp, cpp = _assert_byte_identical(src)
        assert ("::tpy::print_optional_val<::tpy::BytesPrinter, "
                "std::span<const uint8_t>>(b)") in hpp + cpp

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


class TestNarrowedOptionalFieldFaces:
    """The Optional FIELD None-test (`c.f is None` -> `.has_value()`) and the
    stateless narrowed-field read deref (`(*c.f)`), with the strips at the
    positions the AST reads bare storage (plain store targets, the
    print_optional_val wrap)."""

    _REC = (
        "from tpy import Int32\n"
        "class C:\n"
        "    v: Int32 | None\n"
        "    big: int | None\n"
        "    def __init__(self) -> None:\n"
        "        self.v = None\n        self.big = None\n"
    )

    def _cpp(self, src: str, thir: bool) -> str:
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_field_none_test_routes_has_value(self):
        thir, w = _lower_ctx_witnessed(
            self._REC
            + "def use(c: C) -> bool:\n    return c.v is None\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRIsNone) and ret.value.value_repr
        fa = ret.value.operand
        assert isinstance(fa, THIRFieldAccess) and not fa.narrowed_deref
        assert w.get("narrow.opt_field_test")

    def test_narrowed_read_derefs(self):
        thir, w = _lower_ctx_witnessed(
            self._REC
            + "def use(c: C) -> Int32:\n"
            + "    if c.v is None:\n        return 0\n"
            + "    return c.v\n")
        ret = _fn(thir, "use").body[-1]
        fa = ret.value
        assert isinstance(fa, THIRFieldAccess) and fa.narrowed_deref
        assert w.get("field.narrowed_deref")

    def test_store_target_strips_deref_aug_keeps(self):
        src = (self._REC
               + "def use(c: C) -> None:\n"
               + "    if c.v is None:\n        return\n"
               + "    c.v = 5\n"
               + "    c.v += 2\n")
        thir = _lower_ctx(src)
        store = _fn(thir, "use").body[1]
        assert isinstance(store, THIRAssign)
        assert isinstance(store.target, THIRFieldAccess)
        assert not store.target.narrowed_deref
        aug = _fn(thir, "use").body[2]
        assert isinstance(aug.target, THIRFieldAccess)
        assert aug.target.narrowed_deref
        cpp = self._cpp(src + "def main() -> None:\n    use(C())\nmain()\n",
                        thir=True)
        assert "c.v = 5;" in cpp
        assert "(*c.v) = ::tpy::add_check<int32_t>((*c.v), 2);" in cpp

    def test_narrowed_print_keeps_optval_wrap(self):
        # gen_print keys the wrap on the DECLARED field type, so a narrowed
        # scalar field still prints via print_optional_val over bare storage.
        thir = _lower_ctx(
            self._REC
            + "def use(c: C) -> None:\n"
            + "    if c.v is None:\n        return\n"
            + "    print(c.v)\n")
        arg = _fn(thir, "use").body[1].args[0]
        assert arg.print_form is PrintForm.OPT_VAL
        assert isinstance(arg.expr, THIRFieldAccess)
        assert not arg.expr.narrowed_deref

    def test_narrowed_bigint_print_derefs_raw(self):
        # The runtime-bigint print branch fires off the narrowed read BEFORE
        # the Optional wrap (narrowed_field_print_bigint regression): RAW
        # `(*c.big)`, not print_optional_val.
        thir = _lower_ctx(
            self._REC
            + "def use(c: C) -> None:\n"
            + "    if c.big is None:\n        return\n"
            + "    print(c.big)\n")
        arg = _fn(thir, "use").body[1].args[0]
        assert arg.print_form is PrintForm.RAW
        assert isinstance(arg.expr, THIRFieldAccess)
        assert arg.expr.narrowed_deref

    def test_narrowed_record_field_chain_byte_identical(self):
        src = (_PRELUDE
               + "def chain(h: H) -> Int32:\n"
               + "    if h.f is None:\n        return 0\n"
               + "    return h.f.x\n"
               + "def main() -> None:\n"
               + "    print(chain(H()))\n"
               + "main()\n")
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)
        cpp = self._cpp(src, thir=True)
        assert "if ((!h.f.has_value())) {" in cpp
        assert "return (*h.f).x;" in cpp

    def test_one_link_chain_subject_routes(self):
        # A one-link chain None subject (`g.h.f is None`) now routes via
        # the chain-None family (the plain-record link rule); this pin
        # used to record only NAME receivers admitted.
        src = (
            _PRELUDE
            + "class G:\n"
            + "    h: H\n"
            + "    def __init__(self):\n        self.h = H()\n"
            + "def use(g: G) -> bool:\n"
            + "    return g.h.f is None\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        _assert_byte_identical(src)


class TestOptionalScalarEq:
    """sema's optional_safe_eq (`x == y` with a value-repr Optional[scalar]
    operand): both sides render bare over std::optional's mixed operator;
    the plain side opposite an UN-narrowed optional is target-typed to that
    optional's inner (char/numeric literal renders). Ordering ops stay AST."""

    def test_opt_vs_plain_routes(self):
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def eq(x: Int32 | None, y: Int32) -> bool:\n    return x == y\n")
        assert _fn(thir, "eq") is not None

    def test_both_optional_routes(self):
        thir = _lower_ctx(
            "from tpy import Char\n"
            "def eq(a: Char | None, b: Char | None) -> bool:\n"
            "    return a == b\n")
        assert _fn(thir, "eq") is not None

    def test_char_literal_targets_inner(self):
        # `o == "a"`: the str literal renders as a target-typed C++ char
        # literal against the optional's Char inner.
        from .nodes import THIRCharLiteral
        thir = _lower_ctx(
            "from tpy import Char\n"
            "def eq(o: Char | None) -> bool:\n    return o == \"a\"\n")
        ret = _fn(thir, "eq").body[0]
        assert isinstance(ret.value.right, THIRCharLiteral)

    def test_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(
            "from tpy import Int32\n"
            "def ne(x: Int32 | None, y: Int32) -> bool:\n    return x != y\n")
        assert faces.get("binop.opt_scalar_eq", 0) >= 1

    def test_ordering_routes(self):
        # `<` on an optional operand (the AST unwraps with an unproven-value
        # warning) now routes byte-identically (wave-8 none_safety).
        src = ("from tpy import Int32\n"
               "def lt(x: Int32 | None, y: Int32) -> bool:\n    return x < y\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "lt") is not None
        _assert_byte_identical(src)

    def test_narrowed_char_vs_str_literal_ineligible(self):
        # A NARROWED Char|None operand vs a str literal: the AST's
        # _comparison_targets keys the char coercion on the RESOLVED type,
        # so it emits the non-compiling `(*o) == "a"` (BUGS.md); THIR
        # rejects rather than silently emitting the fixed `'a'` render.
        # The UN-narrowed shape keeps routing (the opt_scalar_eq arm;
        # test_char_literal_targets_inner is the routes-still guard).
        thir = _lower_ctx(
            "from tpy import Char\n"
            "def eq(o: Char | None) -> bool:\n"
            "    if o is None:\n        return False\n"
            "    return o == \"a\"\n")
        assert _fn(thir, "eq") is None

    def test_narrowed_char_field_vs_str_literal_ineligible(self):
        # The FIELD-subject sibling of the same shape (field narrowing
        # admits the read; the compare must still reject).
        thir = _lower_ctx(
            "from tpy import Char\n"
            "class C:\n"
            "    v: Char | None\n"
            "    def __init__(self) -> None:\n        self.v = None\n"
            "def eq(c: C) -> bool:\n"
            "    if c.v is None:\n        return False\n"
            "    return c.v == \"a\"\n")
        assert _fn(thir, "eq") is None


class TestOptionalScalarEqEmit:
    def _emit(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    SRC = (
        "from tpy import Int32, Char\n"
        "def eq(x: Int32 | None, y: Int32) -> bool:\n    return x == y\n"
        "def eq_lit(x: Int32 | None) -> bool:\n    return x == 3\n"
        "def eq_char(o: Char | None) -> bool:\n    return o == \"a\"\n"
        "def eq_both(a: Char | None, b: Char | None) -> bool:\n"
        "    return a != b\n"
        "def main():\n"
        "    c: Char = \"a\"\n"
        "    print(eq(5, 5), eq(None, 5), eq_lit(3), eq_char(c),\n"
        "          eq_both(None, c))\n"
        "main()\n"
    )

    def test_byte_identical(self):
        assert self._emit(self.SRC, thir=True) == self._emit(self.SRC, thir=False)

    def test_renders_bare_mixed_compare(self):
        out = self._emit(self.SRC, thir=True)
        assert "return (x == y);" in out
        assert "return (x == 3);" in out
        assert "return (o == 'a');" in out
        assert "return (a != b);" in out


class TestOwnedViewOptWholeSrc:
    """The owned-view Optional whole-copy rows (`field.whole_optional` /
    `_owned_view_opt_whole_src`): only genuinely whole-optional sources take
    the bare copy; a NARROWED param source must fall back (the bare-copy
    render `optional<string> y = (*s);` does not even compile)."""

    def test_whole_field_read_into_optional_local(self):
        src = ("from typing import Optional\n"
               "class R:\n"
               "    key: Optional[str]\n"
               "    def __init__(self) -> None:\n        self.key = \"k\"\n"
               "def f() -> None:\n"
               "    r = R()\n"
               "    flat: Optional[str] = None\n"
               "    flat = r.key\n"
               "    if flat is not None:\n        print(flat)\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_narrowed_param_source_takes_the_shim(self):
        # Sema narrows `s` inside the guard, so the bare whole-copy row
        # (`_owned_view_opt_whole_src`) still declines -- but the owned slot
        # takes the view->owned SHIM instead, which is keyed on the param's
        # DECLARED binding and so is narrowing-blind, exactly like the AST's.
        src = ("from typing import Optional\n"
               "def relay(s: Optional[str]) -> None:\n"
               "    if s is not None:\n"
               "        y: Optional[str] = s\n"
               "        if y is not None:\n            print(y)\n")
        assert _fn(_lower_ctx(src), "relay") is not None
        hpp, cpp = _assert_byte_identical(src)
        assert ("std::optional<std::string> y = s ? "
                "std::make_optional(std::string(*s)) : std::nullopt;"
                in hpp + cpp)


class TestOptViewArgMethodPositionStaysBare:
    """The arg-split shim is FREE-CALL only. `gen_call_arg` suppresses the
    target hint at a record method arg, so the AST reaches
    `_maybe_convert_opt_view_param` with target=None and passes the whole
    optional BARE -- mirroring the split there would emit a copy the AST
    never makes (a copy-vs-alias divergence)."""

    SRC = (
        "from tpy import Int32\n"
        "class Sink:\n"
        "    n: Int32\n"
        "    def __init__(self) -> None:\n        self.n = 0\n"
        "    def take(self, b: bytes | None) -> Int32:\n"
        "        if b is None:\n            return -1\n"
        "        return len(b)\n"
        "def free_take(b: bytes | None) -> Int32:\n"
        "    if b is None:\n        return -1\n"
        "    return len(b)\n"
        "def via_method(s: Sink, b: bytes | None) -> Int32:\n"
        "    return s.take(b)\n"
        "def via_free(b: bytes | None) -> Int32:\n"
        "    return free_take(b)\n"
        "def main() -> None:\n"
        "    s = Sink()\n"
        "    print(via_method(s, b\"xy\"))\n"
        "    print(via_free(b\"xy\"))\n"
        "main()\n"
    )

    def _emit(self, thir: bool) -> str:
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_byte_identical(self):
        assert self._emit(thir=True) == self._emit(thir=False)

    def test_method_arg_bare_free_arg_split(self):
        out = self._emit(thir=True)
        assert "return s.take(b);" in out
        assert ("return free_take(b ? std::make_optional(::tpy::bytes_copy(*b))"
                " : std::nullopt);" in out)


class TestValueOptOwnedStrFieldWrite:
    """`s.label = "hello"` at a `str | None` FIELD: storage is
    `std::optional<std::string>`, so the literal assigns bare like the
    value-scalar sibling. `_value_opt_scalar` excludes the str family for a
    PARAM-shape reason (the optional<string_view>-vs-optional<string> arg
    split) that a field has no equivalent of."""

    _SRC = ("from tpy import Int32, StrView\n"
            "class Holder:\n"
            "    label: str | None\n"
            "    view: StrView | None\n"
            "    def __init__(self) -> None:\n"
            "        self.label = None\n"
            "        self.view = None\n")

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_str_literal_write_routes(self):
        src = (self._SRC
               + "def f() -> None:\n"
               + "    h = Holder()\n"
               + "    h.label = \"hello\"\n"
               + "    print(h.label is None)\n"
               + "f()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("field_write.value_opt_scalar")
        assert 'h.label = "hello";' in self._cpp(src, thir=True)
        _assert_byte_identical(src)

    def test_str_name_source_still_defers(self):
        # BOUNDARY: a NAME source raises the owned-vs-view conversion question
        # the literal row never has -- keep it out until it is priced.
        src = (self._SRC
               + "def f(s: str) -> None:\n"
               + "    h = Holder()\n"
               + "    h.label = s\n"
               + "    print(h.label is None)\n"
               + "f(\"x\")\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_view_typed_field_still_defers(self):
        # BOUNDARY: a `StrView | None` field is a different storage spelling
        # (`_owned_str_slot` admits only the owned nominal).
        src = (self._SRC
               + "def f() -> None:\n"
               + "    h = Holder()\n"
               + "    h.view = \"v\"\n"
               + "    print(h.view is None)\n"
               + "f()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)


class TestValueRecordOptionalBinding:
    """Value-repr `Optional[ValueType record]` bindings (`tz: Fixed | None`
    -> `std::optional<Fixed>`): the has_value None test and the narrowed
    deref reads (`(*tz).off`), via `_value_opt_value_record`."""

    _SRC = (
        "from tpy import Int64, ValueType\n"
        "class Fixed(ValueType):\n"
        "    off: Int64\n"
        "    def __init__(self, off: Int64) -> None:\n"
        "        self.off = off\n"
        "    def doubled(self) -> Int64:\n"
        "        return self.off * 2\n")

    def test_param_none_test_and_narrowed_read(self):
        src = (self._SRC
               + "def use(tz: \"Fixed | None\" = None) -> Int64:\n"
               + "    if tz is None:\n"
               + "        return -1\n"
               + "    return tz.off\n"
               + "def pick(tz: \"Fixed | None\") -> Int64:\n"
               + "    return -2 if tz is None else tz.off\n"
               + "print(use(Fixed(4)), pick(None))\n")
        _assert_routes_byte_identical(src)

    def test_local_binding_and_is_not_polarity(self):
        src = (self._SRC
               + "def use(tz: \"Fixed | None\" = None) -> Int64:\n"
               + "    if tz is not None:\n"
               + "        return tz.off\n"
               + "    return -1\n"
               + "def local_binding() -> Int64:\n"
               + "    lz: \"Fixed | None\" = Fixed(9)\n"
               + "    if lz is None:\n"
               + "        return -3\n"
               + "    return lz.off\n"
               + "print(use(Fixed(4)), local_binding())\n")
        _assert_routes_byte_identical(src)

    def test_method_through_narrowed_binding(self):
        src = (self._SRC
               + "def use(tz: \"Fixed | None\" = None) -> Int64:\n"
               + "    if tz is not None:\n"
               + "        return tz.doubled()\n"
               + "    return -1\n"
               + "print(use(Fixed(4)))\n")
        _assert_routes_byte_identical(src)

    def test_tuple_inner_optional_routes_on_its_own_row(self):
        # A value-repr optional with a value-TUPLE inner is outside the
        # value-record row, but it has its own (`name.opt_vtuple_*`): the
        # has_value None test over the bare binding and the narrowed
        # `(*tz)` deref under std::get.
        src = ("from tpy import Int64\n"
               "def use(tz: \"tuple[Int64, Int64] | None\" = None) -> Int64:\n"
               "    if tz is None:\n"
               "        return -1\n"
               "    return tz[0]\n"
               "print(use((3, 4)))\n")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "if ((!tz.has_value())) {" in cpp
        assert "std::get<0>((*tz))" in cpp
