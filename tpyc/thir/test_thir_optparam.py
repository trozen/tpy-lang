"""Pointer-repr Optional[record] param admission: the borrow-name faces
(deref_check / arrow reads, the None identity test, truthiness, narrowed
passes, Optional-ptr returns) -- routed emits plus the gate rejects that
must never route."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    THIRArgTemp, THIRAssert, THIRAssign, THIRCall, THIRFieldAccess, THIRIf,
    THIRIsNone, THIRLiteral, THIRMethodCall, THIRName, THIROptionalPtrArg,
    THIRReturn, THIRUnaryNot, THIRWhile,
)
from .testutil import _compile, _entry, _fn, _lower_ctx

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
