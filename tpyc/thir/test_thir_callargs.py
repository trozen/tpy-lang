"""The call-arg cascade rows: record names as free-function call args,
user-record method calls on bare-name receivers, and the arg-temp rows
(THIRArgTemp) -- routed emits plus the gate rejects that must never route."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    THIRArgTemp, THIRCall, THIRCtorCall, THIRExprStmt, THIRLiteral,
    THIRMethodCall, THIRName, THIRReturn,
)
from .nodes import Form
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _lower_ctx_witnessed, _fn,
    _assert_byte_identical,
)

# Class A's body, open for extra methods (`_src(extra_a=...)` appends at the
# end of the class); the free functions and sibling records follow.
_CLASS_A = (
    "from tpy import Int32, Own, readonly\n"
    "class A:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
    "    def combine(self, other: A) -> Int32:\n        return self.x + other.x\n"
    "    def bump(self):\n        self.x += 1\n"
    "    def get(self) -> Int32:\n        return self.x\n"
)

_REST = (
    "class B:\n"
    "    y: Int32\n"
    "    def __init__(self, y: Int32):\n        self.y = y\n"
    "class H:\n"
    "    a: A\n"
    "    b: A\n"
    "    def __init__(self):\n        self.a = A(1)\n        self.b = A(2)\n"
    "def take_rec(a: A) -> Int32:\n    return a.x\n"
    "def mutate_rec(a: A):\n    a.x += 1\n"
)

_PRELUDE = _CLASS_A + _REST

# Inheritance fixture for the upcast (Child -> Parent slot) rows.
_UPCAST_PRELUDE = (
    "from tpy import Int32, readonly\n"
    "class Base:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32):\n        self.x = x\n"
    "class Child(Base):\n"
    "    def __init__(self, x: Int32):\n        super().__init__(x)\n"
    "def take_base(b: Base) -> Int32:\n    return b.x\n"
    "def bump_base(b: Base):\n    b.x += 1\n"
)


def _src(tail: str, extra_a: str = "") -> str:
    return _CLASS_A + extra_a + _REST + tail


class TestRecordCallArgs:
    def test_record_name_into_const_and_mutated_slots_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(a: A, b: A) -> Int32:\n"
            + "    mutate_rec(b)\n"
            + "    return take_rec(a)\n")
        fn = _fn(thir, "use")
        assert fn is not None
        stmt = fn.body[0]
        assert isinstance(stmt, THIRExprStmt) and isinstance(stmt.expr, THIRCall)
        arg = stmt.expr.args[0]
        assert isinstance(arg, THIRName) and arg.name == "b" and not arg.deref
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRCall)

    def test_pointer_local_arg_derefs(self):
        # A reseated borrow local is an F2 pointer-local (`A* p`); the AST
        # passes it as `take_rec((*p))` -- the lowered arg carries deref.
        thir = _lower_ctx(
            _PRELUDE
            + "def use(h: H, flag: bool) -> Int32:\n"
            + "    p = h.a\n"
            + "    if flag:\n        p = h.b\n"
            + "    return take_rec(p)\n")
        ret = _fn(thir, "use").body[-1]
        assert isinstance(ret, THIRReturn)
        arg = ret.value.args[0]
        assert isinstance(arg, THIRName) and arg.name == "p" and arg.deref

    def test_narrowed_alias_arg_renames(self):
        # A U3 isinstance-narrowed subject read renames to the `T&` extraction
        # alias; the retyped declared type satisfies the record arg gate.
        thir = _lower_ctx(
            _PRELUDE
            + "def use(v: A | B) -> Int32:\n"
            + "    if isinstance(v, A):\n        return take_rec(v)\n"
            + "    return 0\n")
        fn = _fn(thir, "use")
        assert fn is not None
        branch = fn.body[0].then_body
        ret = branch[-1]
        assert isinstance(ret, THIRReturn)
        arg = ret.value.args[0]
        assert isinstance(arg, THIRName) and arg.name == "__v" and not arg.deref

    def test_record_rvalue_arg_temps(self):
        # `take_rec(A(7))` hoists `A __tmp_N = A(7);` (every record rvalue
        # temps, even into a const slot) -- the record-rvalue arg-temp row.
        thir = _lower_ctx(
            _PRELUDE + "def use() -> Int32:\n    return take_rec(A(7))\n")
        fn = _fn(thir, "use")
        assert fn is not None
        arg = fn.body[0].value.args[0]
        assert isinstance(arg, THIRArgTemp) and arg.cpp_type == "A"

    def test_record_returning_call_arg_temps(self):
        # A by-value record-RETURNING free call as a ref-param arg hoists the
        # same `A __tmp_N = make_a(3);` the ctor rvalue does -- the record-
        # rvalue arg-temp row is not ctor-only.
        thir = _lower_ctx(
            _PRELUDE
            + "def make_a(k: Int32) -> Own[A]:\n    return A(k)\n"
            + "def use() -> Int32:\n    return take_rec(make_a(3))\n")
        fn = _fn(thir, "use")
        assert fn is not None
        arg = fn.body[0].value.args[0]
        assert isinstance(arg, THIRArgTemp) and arg.cpp_type == "A"

    def test_record_rvalue_arg_into_owned_record_decl_routes(self):
        # `o = wrap(make_a(5))` -- a record-returning free call whose ref-param
        # arg is itself a record rvalue; the owned-record decl position flushes
        # the `A __tmp = make_a(5);` ahead of `wrap(__tmp)`.
        thir = _lower_ctx(
            _PRELUDE
            + "def make_a(k: Int32) -> Own[A]:\n    return A(k)\n"
            + "def wrap(a: A) -> Own[B]:\n    return B(a.x)\n"
            + "def use() -> Int32:\n"
            + "    o = wrap(make_a(5))\n    return o.y\n")
        fn = _fn(thir, "use")
        assert fn is not None
        arg = fn.body[0].init.args[0]
        assert isinstance(arg, THIRArgTemp) and arg.cpp_type == "A"

    def test_record_rvalue_arg_into_ctor_outer_mutated_temps(self):
        # A record-rvalue arg into a CTOR outer call whose slot is MUTATED
        # (`Sink(A(1))`, Sink's param -> `A&`): the ctor face keys the temp on
        # the slot's mutation (_gen_record_ctor_args's ctor_mutated arm), so
        # the decl position flushes `A __tmp_N = A(1);` ahead of the call.
        thir = _lower_ctx(
            _PRELUDE
            + "class Sink:\n    c: Int32\n"
            + "    def __init__(self, a: A):\n        a.bump()\n        self.c = a.x\n"
            + "def use() -> Int32:\n"
            + "    s = Sink(A(1))\n    return s.c\n")
        fn = _fn(thir, "use")
        assert fn is not None
        init = fn.body[0].init
        assert isinstance(init, THIRCtorCall)
        arg = init.args[0]
        assert isinstance(arg, THIRArgTemp) and arg.cpp_type == "A"

    def test_ctor_freefn_propagated_mutation_mirrors_ast(self):
        # Mutation reaching the ctor param only through a FREE-fn call
        # (`mut(a)`) updates the real __init__ fi (the `A&` signature) but
        # NOT the synthetic ctor fi's `mutated_params` -- so the AST call
        # site renders the rvalue INLINE against the `A&` slot, ill-formed
        # C++ (BUGS.md, pre-existing). Both paths read the same synthetic
        # fi, so THIR mirrors the render byte-identically; fixing the AST
        # fact must update this mirror in tandem.
        src = (
            _PRELUDE
            + "def mut(a: A):\n    a.x += 1\n"
            + "class Sink:\n    c: Int32\n"
            + "    def __init__(self, a: A):\n        mut(a)\n        self.c = a.x\n"
            + "def use() -> Int32:\n"
            + "    s = Sink(A(1))\n    return s.c\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "use")
        assert fn is not None
        assert isinstance(fn.body[0].init.args[0], THIRCtorCall)  # inline
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_record_rvalue_arg_into_ctor_outer_const_inlines(self):
        # The const-slot sibling: the prvalue binds the `const A&` directly,
        # so the expansion inlines with no temp (`SinkC(A(2))`).
        thir = _lower_ctx(
            _PRELUDE
            + "class SinkC:\n    c: Int32\n"
            + "    def __init__(self, a: A):\n        self.c = a.x\n"
            + "def use() -> Int32:\n"
            + "    s = SinkC(A(2))\n    return s.c\n")
        fn = _fn(thir, "use")
        assert fn is not None
        init = fn.body[0].init
        assert isinstance(init, THIRCtorCall)
        assert isinstance(init.args[0], THIRCtorCall)

    def test_call_rvalue_arg_into_ctor_outer_const_inlines(self):
        # A by-value record-returning free call into a const ctor slot binds
        # inline too (`SinkC(make_a(3))`).
        thir = _lower_ctx(
            _PRELUDE
            + "def make_a(k: Int32) -> Own[A]:\n    return A(k)\n"
            + "class SinkC:\n    c: Int32\n"
            + "    def __init__(self, a: A):\n        self.c = a.x\n"
            + "def use() -> Int32:\n"
            + "    s = SinkC(make_a(3))\n    return s.c\n")
        fn = _fn(thir, "use")
        assert fn is not None
        init = fn.body[0].init
        assert isinstance(init, THIRCtorCall)
        assert isinstance(init.args[0], THIRCall)

    def test_ctor_mutated_slot_rvalue_nested_stays_ast(self):
        # The mutated-slot temp needs a statement flush; as a NESTED arg
        # (`WrapM(Sink(A(1)))`) there is none -> the whole body stays AST.
        thir = _lower_ctx(
            _PRELUDE
            + "class Sink:\n    c: Int32\n"
            + "    def __init__(self, a: A):\n        a.bump()\n        self.c = a.x\n"
            + "class WrapM:\n    w: Int32\n"
            + "    def __init__(self, s: Sink):\n        self.w = s.c\n"
            + "def use() -> Int32:\n"
            + "    o = WrapM(Sink(A(1)))\n    return o.w\n")
        assert _fn(thir, "use") is None

    def test_ctor_const_chain_nested_routes(self):
        # Const chains inline at any depth: `Wrap(SinkC(A(5)))`.
        thir = _lower_ctx(
            _PRELUDE
            + "class SinkC:\n    c: Int32\n"
            + "    def __init__(self, a: A):\n        self.c = a.x\n"
            + "class Wrap:\n    w: Int32\n"
            + "    def __init__(self, s: SinkC):\n        self.w = s.c\n"
            + "def use() -> Int32:\n"
            + "    o = Wrap(SinkC(A(5)))\n    return o.w\n")
        fn = _fn(thir, "use")
        assert fn is not None
        init = fn.body[0].init
        assert isinstance(init, THIRCtorCall)
        inner = init.args[0]
        assert isinstance(inner, THIRCtorCall)
        assert isinstance(inner.args[0], THIRCtorCall)

    def test_own_record_slot_routes_via_move(self):
        # An Own[A] slot auto-moves its arg at last use (`sink(std::move(a))`)
        # -- the Own-slot cascade rows (see TestOwnSlotArgs).
        thir = _lower_ctx(
            _PRELUDE
            + "def sink(a: Own[A]) -> Int32:\n    return a.x\n"
            + "def use(a: Own[A]) -> Int32:\n    return sink(a)\n")
        assert _fn(thir, "use") is not None

    def test_readonly_record_slot_stays_ast(self):
        # A readonly-wrapped slot is the deep-const frontier -- deferred.
        thir = _lower_ctx(
            _PRELUDE
            + "def take_ro(a: readonly[A]) -> Int32:\n    return a.x\n"
            + "def use(a: A) -> Int32:\n    return take_ro(a)\n")
        assert _fn(thir, "use") is None

    def test_readonly_slot_ctor_rvalue_routes_bare(self):
        # A ctor rvalue into a readonly-ANNOTATED slot binds the const ref
        # directly -- bare on both paths (the ref-param temp arm keys on
        # is_ref_param, which the readonly wrapper defeats).
        src = (
            _PRELUDE
            + "def take_ro(a: readonly[A]) -> Int32:\n    return a.x\n"
            + "def use() -> Int32:\n    return take_ro(A(7))\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "return take_ro(A(7));" in out
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert "own.readonly_ctor" in w

    def test_upcast_name_arg_routes(self):
        # A Child NAME into a Parent ref slot emits the bare name on both
        # paths (C++ implicit derived-to-base binding) -- const and mutated
        # slots alike.
        thir = _lower_ctx(
            _UPCAST_PRELUDE
            + "def use(c: Child) -> Int32:\n"
            + "    bump_base(c)\n"
            + "    return take_base(c)\n")
        fn = _fn(thir, "use")
        assert fn is not None
        stmt = fn.body[0]
        assert isinstance(stmt, THIRExprStmt) and isinstance(stmt.expr, THIRCall)
        arg = stmt.expr.args[0]
        assert isinstance(arg, THIRName) and arg.name == "c" and not arg.deref

    def test_upcast_rvalue_arg_stays_ast(self):
        # A ctor-rvalue upcast hoists a temp typed at the CHILD on the AST
        # path (`Child __tmp_N = Child(7); take_base(__tmp_N)`) -> AST.
        thir = _lower_ctx(
            _UPCAST_PRELUDE
            + "def use() -> Int32:\n    return take_base(Child(7))\n")
        assert _fn(thir, "use") is None

    def test_upcast_readonly_slot_stays_ast(self):
        # The deep-const frontier rejects readonly slots for upcasts too.
        thir = _lower_ctx(
            _UPCAST_PRELUDE
            + "def take_ro(b: readonly[Base]) -> Int32:\n    return b.x\n"
            + "def use(c: Child) -> Int32:\n    return take_ro(c)\n")
        assert _fn(thir, "use") is None

    def test_optional_record_slot_routes_via_addr_of(self):
        # An `A | None` slot takes the `_gen_optional_ptr_arg` lift (`&(a)`)
        # -- the optional-ptr faces (see TestOptionalPtrArgs).
        thir = _lower_ctx(
            _PRELUDE
            + "def maybe(a: A | None) -> Int32:\n"
            + "    if a is not None:\n        return a.x\n"
            + "    return 0\n"
            + "def use(a: A) -> Int32:\n    return maybe(a)\n")
        assert _fn(thir, "use") is not None

    def test_union_record_slot_routes_via_member_lift(self):
        # An `A | B` slot lifts the member arg into the variant -- routed by
        # the member lift (THIRUnionArgLift pins the render in
        # test_thir_unions.py); here we only assert the body routes.
        thir = _lower_ctx(
            _PRELUDE
            + "def take_u(v: A | B) -> Int32:\n    return 0\n"
            + "def use(a: A) -> Int32:\n    return take_u(a)\n")
        assert _fn(thir, "use") is not None

    def test_self_as_arg_routes(self):
        # `take_rec(self)` renders `take_rec((*this))` via the record-name
        # arg row, byte-identically.
        src = _src("", extra_a="    def through(self) -> Int32:\n"
                               "        return take_rec(self)\n")
        assert _fn(_lower_ctx(src), "through") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestRecordMethodCalls:
    def test_method_on_record_param_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(a: A, b: A) -> Int32:\n    return a.combine(b)\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret, THIRReturn)
        mc = ret.value
        assert isinstance(mc, THIRMethodCall)
        assert mc.method_cpp == "combine" and not mc.is_arrow
        assert mc.cpp_template is None and mc.native_function_name is None
        arg = mc.args[0]
        assert isinstance(arg, THIRName) and arg.name == "b" and not arg.deref

    def test_void_method_stmt_position_routes(self):
        thir = _lower_ctx(_PRELUDE + "def use(a: A):\n    a.bump()\n")
        stmt = _fn(thir, "use").body[0]
        assert isinstance(stmt, THIRExprStmt)
        assert isinstance(stmt.expr, THIRMethodCall)
        assert stmt.expr.method_cpp == "bump"

    def test_void_method_value_position_stays_ast(self):
        # A void result is discard-only: admitted as a bare statement, not in
        # value position (`x = a.bump()` is a sema error anyway; this pins the
        # gate's stmt_position flag).
        thir = _lower_ctx(
            _PRELUDE + "def use(a: A) -> Int32:\n    a.bump()\n    return a.get()\n")
        assert _fn(thir, "use") is not None

    def test_pointer_local_receiver_arrow(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(h: H, flag: bool) -> Int32:\n"
            + "    p = h.a\n"
            + "    if flag:\n        p = h.b\n"
            + "    return p.get()\n")
        ret = _fn(thir, "use").body[-1]
        mc = ret.value
        assert isinstance(mc, THIRMethodCall) and mc.is_arrow
        recv = mc.receiver
        # The receiver renders bare (`p->get()`), not `(*p).get()`.
        assert isinstance(recv, THIRName) and recv.name == "p" and not recv.deref

    def test_narrowed_alias_receiver_routes(self):
        thir = _lower_ctx(
            _PRELUDE
            + "def use(v: A | B) -> Int32:\n"
            + "    if isinstance(v, A):\n        return v.get()\n"
            + "    return 0\n")
        fn = _fn(thir, "use")
        assert fn is not None
        ret = fn.body[0].then_body[-1]
        mc = ret.value
        assert isinstance(mc, THIRMethodCall) and not mc.is_arrow
        recv = mc.receiver
        assert isinstance(recv, THIRName) and recv.name == "__v"

    def test_inherited_single_overload_method_routes(self):
        # An inherited method resolves through the MRO and emits the same
        # `recv.method(args)` member call for the admitted arg shapes.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Base:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "    def addb(self, k: Int32) -> Int32:\n        return self.x + k\n"
            "class Child(Base):\n"
            "    def __init__(self, x: Int32):\n        super().__init__(x)\n"
            "def use(c: Child, k: Int32) -> Int32:\n    return c.addb(k)\n")
        ret = _fn(thir, "use").body[0]
        assert isinstance(ret.value, THIRMethodCall)
        assert ret.value.method_cpp == "addb"

    def test_method_ctor_rvalue_const_slot_routes(self):
        # `a.combine(A(9))` -- the method arg loop inlines the ctor expansion
        # into the const `A&` slot (no rvalue-temp arm, unlike free calls).
        thir = _lower_ctx(_src(
            "def use_const(a: A) -> Int32:\n    return a.combine(A(9))\n"))
        fn = _fn(thir, "use_const")
        assert fn is not None
        mc = fn.body[0].value
        assert isinstance(mc, THIRMethodCall)
        arg = mc.args[0]
        assert isinstance(arg, THIRCtorCall) and arg.type_cpp == "A"

    def test_method_ctor_rvalue_mutated_slot_stays_ast(self):
        # The mutated-ref-param shape (`a.absorb(A(4))`) is the AST
        # miscompile tracked in BUGS.md ("method-call record rvalue into a
        # mutated ref param never temps") -- it must stay gate-rejected.
        thir = _lower_ctx(_src(
            "def use_mut(a: A):\n    a.absorb(A(4))\n",
            extra_a="    def absorb(self, other: A):\n"
                    "        other.x += 1\n        self.x += other.x\n"))
        assert _fn(thir, "use_mut") is None

    def test_method_upcast_name_arg_routes(self):
        # A Child NAME into a Parent method slot passes bare, like the free
        # call; the receiver mutates through it (aliasing observable).
        thir = _lower_ctx(
            _UPCAST_PRELUDE
            + "class Sink:\n"
            + "    total: Int32\n"
            + "    def __init__(self):\n        self.total = 0\n"
            + "    def add(self, b: Base):\n        self.total += b.x\n"
            + "def use(s: Sink, c: Child) -> Int32:\n"
            + "    s.add(c)\n"
            + "    return s.total\n")
        fn = _fn(thir, "use")
        assert fn is not None
        mc = fn.body[0].expr
        assert isinstance(mc, THIRMethodCall)
        arg = mc.args[0]
        assert isinstance(arg, THIRName) and arg.name == "c"

    def test_self_receiver_routes_via_arrow(self):
        # `self.get()` renders `this->get()` -- the THIRSelf receiver rides
        # the same indirect (`is_arrow`) render as a pointer-local's.
        src = _src(
            "", extra_a="    def twice(self) -> Int32:\n"
                        "        return self.get() * 2\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "return (::tpy::mul_check<int32_t>(this->get(), 2));" in out
        assert _fn(_lower_ctx(src), "twice") is not None

    def test_auto_readonly_clone_pair_call_routes(self):
        # @auto_readonly clones a method into a same-name mutable + const
        # pair; the call renders the same plain `p.get_x()` whichever
        # member sema resolved (C++ dispatches on receiver const-ness), so
        # the clone-pair carve-out routes the CALLER. Genuine @overload
        # stub sets keep rejecting (see TestAutoCloneOverloadCarveout).
        src = (
            "from tpy import Int32, auto_readonly\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "    @auto_readonly\n"
            "    def get_x(self) -> Int32:\n        return self.x\n"
            "def use(p: P) -> Int32:\n    return p.get_x()\n"
            "def main():\n    print(use(P(3)))\nmain()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp_t = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        compiler2, modules2 = _compile(src)
        _, cpp_a = compiler2.generate_code_to_strings(
            _entry(modules2), options=CodeGenOptions(emit_source_comments=False,
                                                     thir_codegen=False))
        assert cpp_t == cpp_a

    def test_own_scalar_method_slot_stays_ast(self):
        # A user method is not an inline template: gen_call_arg copies an
        # Own[scalar] arg into a temp and moves it -- rejected, unlike the
        # builtin-container Own[scalar] slots.
        thir = _lower_ctx(_src(
            "def use(a: A, n: Int32) -> Int32:\n    return a.own_scalar(n)\n",
            extra_a="    def own_scalar(self, v: Own[Int32]) -> Int32:\n"
                    "        return self.x + v\n"))
        assert _fn(thir, "use") is None

    def test_record_returning_method_routes_as_field_receiver(self):
        # A record result routes at the FIELD-RECEIVER position
        # (field.call_recv): `a.pick(b).x` renders the bare postfix member.
        # In a plain value position (no member access) it stays outside the
        # admitted set.
        src = _src(
            "def use(a: A, b: A) -> Int32:\n    return a.pick(b).x\n",
            extra_a="    def pick(self, other: A) -> A:\n        return other\n")
        assert _fn(_lower_ctx(src), "use") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert "return a.pick(b).x;" in _cpp(src, thir=True)


class TestCallArgEmit:
    SRC = (
        _PRELUDE
        + "class D(A):\n"
        + "    def __init__(self, x: Int32):\n        super().__init__(x)\n"
        + "def use(a: A, b: A) -> Int32:\n"
        + "    mutate_rec(b)\n"
        + "    a.bump()\n"
        + "    return take_rec(a) + a.combine(b)\n"
        + "def use_ptr(h: H, flag: bool) -> Int32:\n"
        + "    p = h.a\n"
        + "    if flag:\n        p = h.b\n"
        + "    return take_rec(p) + p.get()\n"
        + "def use_narrow(v: A | B) -> Int32:\n"
        + "    if isinstance(v, A):\n        return take_rec(v) + v.get()\n"
        + "    return v.y\n"
        + "def use_upcast(d: D) -> Int32:\n"
        + "    mutate_rec(d)\n"
        + "    return take_rec(d)\n"
        + "def use_ctor_arg(a: A) -> Int32:\n"
        + "    return a.combine(A(9))\n"
        + "def main():\n"
        + "    h = H()\n"
        + "    print(use(h.a, h.b))\n"
        + "    print(use_ptr(h, True))\n"
        + "    print(use_narrow(A(5)))\n"
        + "    d = D(6)\n"
        + "    print(use_upcast(d))\n"
        + "    print(d.x)\n"
        + "    print(use_ctor_arg(h.a))\n"
        + "main()\n"
    )

    def _emit(self, thir: bool):
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return hpp + cpp

    def test_byte_identical(self):
        assert self._emit(thir=True) == self._emit(thir=False)

    def test_emitted_shapes(self):
        out = self._emit(thir=True)
        assert "mutate_rec(b);" in out
        assert "return (::tpy::add_check<int32_t>(take_rec(a), a.combine(b)));" in out
        assert "return (::tpy::add_check<int32_t>(take_rec((*p)), p->get()));" in out
        assert "return (::tpy::add_check<int32_t>(take_rec(__v), __v.get()));" in out
        assert "mutate_rec(d);" in out  # upcast name: bare on both paths
        assert "return take_rec(d);" in out
        assert "return a.combine(A(9));" in out  # method ctor arg inlines


# --- The arg-temp rows (THIRArgTemp / TempSink) ---

_VU_PRELUDE = (
    "from tpy import Int32, Float64\n"
    "def take_vu(v: Int32 | Float64) -> Int32:\n"
    "    if isinstance(v, Int32):\n        return v\n"
    "    return 0\n"
    "def two(a: Int32 | Float64, b: Int32 | Float64) -> Int32:\n"
    "    return take_vu(a) + take_vu(b)\n"
)


def _cpp(src: str, thir: bool, extra_lib_dirs=None) -> str:
    compiler, modules = _compile(src, extra_lib_dirs)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


class TestArgTempEmit:
    # All four flushable positions plus the two-temps-in-one-statement shape.
    SRC = (
        _VU_PRELUDE
        + "def use_decl(k: Int32) -> Int32:\n"
        + "    r = take_vu(k)\n    return r\n"
        + "def use_assign(k: Int32) -> Int32:\n"
        + "    r = 0\n    r = take_vu(k)\n    return r\n"
        + "def use_ret(k: Int32) -> Int32:\n    return take_vu(k)\n"
        + "def use_stmt(k: Int32):\n    take_vu(k)\n"
        + "def use_two(k: Int32, f: Float64) -> Int32:\n    return two(k, f)\n"
        + "def use_float_lit() -> Int32:\n    return take_vu(2.5)\n"
        + "def use_binop(k: Int32) -> Int32:\n    return take_vu(k + 1)\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_routing_is_non_vacuous(self):
        thir = _lower(self.SRC)
        for name in ("use_decl", "use_assign", "use_ret", "use_stmt",
                     "use_two", "use_float_lit", "use_binop"):
            assert _fn(thir, name) is not None, name

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        assert "std::variant<int32_t, double> __tmp_1 = k;\n    int32_t r = take_vu(__tmp_1);" in out
        # Two temps in one statement number left-to-right.
        assert ("std::variant<int32_t, double> __tmp_5 = k;\n"
                "    std::variant<int32_t, double> __tmp_6 = f;\n"
                "    return two(__tmp_5, __tmp_6);") in out
        assert "__tmp_7 = 2.5;" in out
        assert "__tmp_8 = (::tpy::add_check<int32_t>(k, 1));" in out

    def test_record_rvalue_temp_emits(self):
        src = (
            _PRELUDE
            + "def use_ret() -> Int32:\n    return take_rec(A(7))\n"
            + "def use_mut():\n    mutate_rec(A(9))\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "A __tmp_1 = A(7);\n    return take_rec(__tmp_1);" in out
        assert "A __tmp_2 = A(9);\n    mutate_rec(__tmp_2);" in out
        thir = _lower_ctx(src)
        assert _fn(thir, "use_ret") is not None
        assert _fn(thir, "use_mut") is not None

    def test_ctor_outer_record_rvalue_emits(self):
        # The ctor-face record-rvalue arms, emit-pinned: a MUTATED slot
        # hoists the named temp, a const slot binds the inline prvalue.
        src = (
            _PRELUDE
            + "class Sink:\n    c: Int32\n"
            + "    def __init__(self, a: A):\n        a.bump()\n        self.c = a.x\n"
            + "class SinkC:\n    c: Int32\n"
            + "    def __init__(self, a: A):\n        self.c = a.x\n"
            + "def use_mut() -> Int32:\n    s = Sink(A(1))\n    return s.c\n"
            + "def use_const() -> Int32:\n    s = SinkC(A(2))\n    return s.c\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "A __tmp_1 = A(1);\n    Sink s = Sink(__tmp_1);" in out
        assert "SinkC s = SinkC(A(2));" in out
        thir = _lower_ctx(src)
        assert _fn(thir, "use_mut") is not None
        assert _fn(thir, "use_const") is not None

    def test_field_write_position_flushes(self):
        # The fifth flushable statement position: a scalar-field write's value
        # call hoists its temp before the assign line (the AST's single
        # gen_stmt flush point covers every assign target shape).
        src = (
            _VU_PRELUDE
            + "class K:\n"
            + "    n: Int32\n"
            + "    def __init__(self):\n        self.n = 0\n"
            + "def use(k: K, v: Int32) -> Int32:\n"
            + "    k.n = take_vu(v)\n    return k.n\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert ("std::variant<int32_t, double> __tmp_1 = v;\n"
                "    k.n = take_vu(__tmp_1);") in out
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert "flush.field_write" in w

    def test_mixed_thir_ast_numbering_stays_continuous(self):
        # The load-bearing seam test: fn1 routes (its temp draws __tmp_1 from
        # the module-cumulative ctx.temps via CtxTempSink), fn2 stays AST (a
        # while-condition temp hoist) and must continue at __tmp_2 exactly as
        # the all-AST emit numbers it.
        src = (
            _VU_PRELUDE
            + "def routed(k: Int32) -> Int32:\n    return take_vu(k)\n"
            + "def unrouted(k: Int32) -> Int32:\n"
            + "    n = 0\n"
            + "    while take_vu(k) > n:\n        n += 1\n"
            + "    return n\n"
        )
        thir = _lower(src)
        assert _fn(thir, "routed") is not None
        assert _fn(thir, "unrouted") is None  # while-cond temp -> AST
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert "__tmp_1 = k;\n    return take_vu(__tmp_1);" in out
        assert "__tmp_2 = k;\n    while ((take_vu(__tmp_2) > n))" in out

    def test_ctor_demotion_body_temp_routes(self):
        # A ctor body statement is the same flushable machinery: the demoted
        # tail flushes the temp at body indent through the shared sink.
        src = (
            _VU_PRELUDE
            + "class R:\n"
            + "    x: Int32\n"
            + "    def __init__(self, k: Int32):\n"
            + "        take_vu(k)\n"
            + "        self.x = k\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert ("std::variant<int32_t, double> __tmp_1 = k;\n"
                "    take_vu(__tmp_1);") in out
        from .testutil import _lower_ctor
        assert _lower_ctor(src, "R") is not None

    def test_dump_is_number_free(self):
        from .dump import dump_thir
        thir = _lower(_VU_PRELUDE
                      + "def f(k: Int32) -> Int32:\n    return take_vu(k)\n")
        text = dump_thir(thir)
        assert "%argtmp(std::variant<int32_t, double>){%k}" in text
        assert "__tmp" not in text


class TestOwnSlotArgs:
    # gen_call_arg's ownership cascade (the copy+move arms): an lvalue into a
    # plain Own[T] slot hoists `auto __tmp_N = <arg>;` and moves the temp; a
    # movable name (an Own param) at its last use moves temp-free
    # (`std::move(o)`); rvalues bind the slot bare. Record args are params --
    # a single-assignment record LOCAL is itself outside the slice.
    SRC = (
        _PRELUDE
        + "def take_own(o: Own[A]) -> Int32:\n    return o.x\n"
        + "def take_own_s(o: Own[Int32]) -> Int32:\n    return o\n"
        + "def mk() -> Own[A]:\n    return A(4)\n"
        + "def copy_arm(a: A) -> Int32:\n"
        + "    r = take_own(a)\n    return r + a.x\n"
        + "def scalar_name_arm(n: Int32) -> Int32:\n    return take_own_s(n)\n"
        + "def field_arm(a: A) -> Int32:\n    return take_own_s(a.x)\n"
        + "def move_arm(o: Own[A]) -> Int32:\n    return take_own(o)\n"
        + "def rvalue_arm() -> Int32:\n    return take_own(A(3))\n"
        + "def call_rvalue_arm() -> Int32:\n    return take_own(mk())\n"
        + "def scalar_lit_arm() -> Int32:\n    return take_own_s(5)\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("copy_arm", "scalar_name_arm", "field_arm", "move_arm",
                     "rvalue_arm", "call_rvalue_arm", "scalar_lit_arm"):
            assert _fn(thir, name) is not None, name

    def test_emitted_shapes(self):
        thir_out = _cpp(self.SRC, thir=True)
        assert thir_out == _cpp(self.SRC, thir=False)
        # The copy+move temps (record param, scalar name, scalar field read).
        assert ("auto __tmp_1 = a;\n    "
                "int32_t r = take_own(std::move(__tmp_1));") in thir_out
        assert ("auto __tmp_2 = n;\n    "
                "return take_own_s(std::move(__tmp_2));") in thir_out
        assert ("auto __tmp_3 = a.x;\n    "
                "return take_own_s(std::move(__tmp_3));") in thir_out
        # The temp-free last-use move of an Own param.
        assert "return take_own(std::move(o));" in thir_out
        # The bare rvalue binds (ctor, by-value call, coerced literal).
        assert "return take_own(A(3));" in thir_out
        assert "return take_own(mk());" in thir_out
        assert "return take_own_s(5);" in thir_out

    def test_pointer_local_arg_copies_through_deref_temp(self):
        # A pointer-local is a non-owning borrow -- never movable, so it
        # always copies, with the indirect deref in the temp init.
        src = (
            _PRELUDE
            + "def take_own(o: Own[A]) -> Int32:\n    return o.x\n"
            + "def use(h: H, flag: bool) -> Int32:\n"
            + "    p = h.a\n"
            + "    if flag:\n        p = h.b\n"
            + "    return take_own(p)\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "auto __tmp_1 = (*p);\n    return take_own(std::move(__tmp_1));" in out
        assert _fn(_lower_ctx(src), "use") is not None

    def test_own_param_not_at_last_use_copies(self):
        # Movable but NOT the last use: the copy temp fires, not the move.
        src = (
            _PRELUDE
            + "def take_own(o: Own[A]) -> Int32:\n    return o.x\n"
            + "def use(o: Own[A]) -> Int32:\n"
            + "    r = take_own(o)\n    return r + o.x\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "auto __tmp_1 = o;\n    int32_t r = take_own(std::move(__tmp_1));" in out
        assert _fn(_lower_ctx(src), "use") is not None

    def test_dump_shapes(self):
        from .dump import dump_thir
        text = dump_thir(_lower_ctx(self.SRC))
        assert "move(%o)" in text                     # the temp-free move
        assert "%argtmp(auto move){%a}" in text       # the copy+move temp
        assert "__tmp" not in text                    # number-free by design

    def test_witnesses_own_slot_faces(self):
        # The copy+move temp fires per lvalue arm (copy_arm /
        # scalar_name_arm / field_arm); the bare-rvalue gate admission fires
        # for the coerced-literal arm (scalar_lit_arm).
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("argtemp.own_copy", 0) == 3
        assert w.get("own.scalar_rvalue", 0) == 1
        assert "move.own_last_use" in w               # move_arm


class TestOwnSlotGateRejects:
    # Own-slot shapes whose AST render the slice does not reproduce -- pinned
    # unrouted. Each pairs with a byte-identity assertion so a future gate
    # widening that forgets the emit half fails here first.

    def test_own_arg_in_non_flushable_position_stays_ast(self):
        # A nested call arg has no flush point -- the copy temp cannot hoist.
        src = (
            _PRELUDE
            + "def take_own_s(o: Own[Int32]) -> Int32:\n    return o\n"
            + "def g(x: Int32) -> Int32:\n    return x\n"
            + "def use(n: Int32) -> Int32:\n    return g(take_own_s(n))\n")
        assert _fn(_lower_ctx(src), "use") is None

    def test_optional_own_slot_stays_ast(self):
        # An `Own[A] | None` slot takes the ptr_to_optional wrap arms.
        src = (
            _PRELUDE
            + "def take_opt(o: Own[A] | None) -> Int32:\n"
            + "    if o is None:\n        return 0\n"
            + "    return o.x\n"
            + "def use(a: A) -> Int32:\n"
            + "    r = take_opt(a)\n    return r + a.x\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "use") is None

    def test_record_field_arg_stays_ast(self):
        # A RECORD-typed field read into an Own slot copies through the temp
        # on the AST path (`auto __tmp = h.a;`), but a record field read in
        # arbitrary expression position rejects during lowering -- deferred.
        src = (
            _PRELUDE
            + "def take_own(o: Own[A]) -> Int32:\n    return o.x\n"
            + "def use(h: H) -> Int32:\n    return take_own(h.a)\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "use") is None

    def test_borrow_returning_callee_arg_stays_ast(self):
        # A borrow-returning call is not an rvalue source: the AST copies it
        # through a temp (`auto __tmp = get_ref(h); std::move(__tmp)`), which
        # is neither the bare-rvalue nor the lvalue row.
        src = (
            _PRELUDE
            + "def take_own(o: Own[A]) -> Int32:\n    return o.x\n"
            + "def get_ref(h: H) -> A:\n    return h.a\n"
            + "def use(h: H) -> Int32:\n    return take_own(get_ref(h))\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "use") is None

    def test_narrowed_subject_own_arg_stays_ast(self):
        # A narrowed union subject into an Own slot: the copy temp would
        # need the extraction-alias init -- the row is temps_ok-gated, and
        # every temps_ok site threads `narrowed`, so the reject holds on
        # both the gate and lowering sides.
        src = (
            _PRELUDE
            + "def take_own(o: Own[A]) -> Int32:\n    return o.x\n"
            + "def use(u: A | B) -> Int32:\n"
            + "    if isinstance(u, A):\n        return take_own(u)\n"
            + "    return 0\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "use") is None

    def test_coerced_lvalue_into_own_slot_stays_ast(self):
        # A coerce-WRAPPED lvalue splits on the AST's rendered-identity
        # `needs_copy` check (identity coercion temps, real conversion binds
        # bare) -- not mirrored, both faces stay AST.
        src = ("from tpy import Int32, Int64, Own\n"
               "def take64(o: Own[Int64]) -> Int64:\n    return o\n"
               "def use(n: Int32) -> Int64:\n    return take64(n)\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "use") is None

    def test_f2d_own_ctor_arg_stays_ast(self):
        # The F2d rebind-slot ctor face shares the slot-blindness fix: an
        # `Own[scalar]` __init__ slot temps the bare-name arg on the AST path
        # (`_gen_record_ctor_args` shares the copy+move shape).
        src = ("from tpy import Int32, Own\n"
               "class W:\n    v: Int32\n"
               "    def __init__(self, v: Own[Int32]):\n        self.v = v\n"
               "def f(n: Int32) -> Int32:\n"
               "    p = W(n)\n    p = W(n)\n    return p.v\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "f") is None


class TestMethodUnionAndSelfArgs:
    # Method value-union args (the free-call temp/pass-through rows through
    # the method arg loop -- value variants are const-blind, so own-record
    # and inherited methods render identically) and self-receiver method
    # calls (`self.helper()` -> `this->helper()`).
    SRC = (
        "from tpy import Int32, Float64\n"
        "class A:\n"
        "    x: Int32\n"
        "    u: Int32 | Float64\n"
        "    def __init__(self, x: Int32):\n"
        "        self.x = x\n        self.u = 0\n"
        "    def tag(self, v: Int32 | Float64) -> Int32:\n        return self.x\n"
        "    def helper(self) -> Int32:\n        return self.x + 1\n"
        "    def outer(self) -> Int32:\n        return self.helper()\n"
        "class Child(A):\n"
        "    def __init__(self, x: Int32):\n        super().__init__(x)\n"
        "def use(a: A, k: Int32, f: Float64) -> Int32:\n"
        "    r = a.tag(k)\n"
        "    r = a.tag(f)\n"
        "    return r\n"
        "def use_inherited(c: Child, k: Int32) -> Int32:\n"
        "    return c.tag(k)\n"
        "def use_lit(a: A) -> Int32:\n"
        "    return a.tag(5)\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("use", "use_inherited", "use_lit", "outer", "helper"):
            assert _fn(thir, name) is not None, name

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        assert out == _cpp(self.SRC, thir=False)
        # Member-valued scalars hoist the variant temp at the method arg
        # (decl-init, reassign, and return positions; inherited callee too).
        assert ("std::variant<int32_t, double> __tmp_1 = k;\n"
                "    int32_t r = a.tag(__tmp_1);") in out
        assert ("std::variant<int32_t, double> __tmp_2 = f;\n"
                "    r = a.tag(__tmp_2);") in out
        assert ("std::variant<int32_t, double> __tmp_3 = k;\n"
                "    return c.tag(__tmp_3);") in out
        # A coerced int literal passes bare (sema coerces it to the union).
        assert "return a.tag(5);" in out
        # The self receiver renders arrow.
        assert "return this->helper();" in out

    def test_witnesses_method_union_temp(self):
        # One method-side variant temp per member-valued arg (use's two
        # assigns + use_inherited's return); the free-call row is a separate
        # face (argtemp.value_union) and must not absorb these.
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("argtemp.value_union_method", 0) == 3

    def test_method_union_temp_in_non_flushable_position_stays_ast(self):
        # A nested method-call arg has no flush point for the variant temp.
        src = (
            self.SRC
            + "def g(x: Int32) -> Int32:\n    return x\n"
            + "def nested(a: A, k: Int32) -> Int32:\n    return g(a.tag(k))\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "nested") is None


class TestOptionalPtrArgs:
    # _gen_optional_ptr_arg's non-protocol faces: a pointer-repr Optional
    # slot (`const A*` / `A*`) takes nullptr for None, `&(name)` for a plain
    # record lvalue, the bare name for an already-pointer source (an F2
    # pointer-local, a pointer-repr Optional binding), the optional_to_ptr
    # lift for a storage Optional field, and the `&(__tmp_N)` temp for a
    # ctor rvalue. Self-contained prelude: the optional-field face needs an
    # `A | None` field on the receiver record.
    SRC = (
        "from tpy import Int32\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n        self.x = x\n"
        "class H:\n"
        "    a: A\n"
        "    b: A\n"
        "    opt: A | None\n"
        "    def __init__(self):\n"
        "        self.a = A(1)\n        self.b = A(2)\n        self.opt = None\n"
        + "def take_opt(o: A | None) -> Int32:\n"
        + "    if o is None:\n        return 0\n"
        + "    return o.x\n"
        + "def none_arm() -> Int32:\n    return take_opt(None)\n"
        + "def name_arm(a: A) -> Int32:\n    return take_opt(a)\n"
        + "def rvalue_arm() -> Int32:\n    return take_opt(A(7))\n"
        + "def opt_local_arm(h: H) -> Int32:\n"
        + "    o = h.opt\n    return take_opt(o)\n"
        + "def field_arm(h: H) -> Int32:\n    return take_opt(h.opt)\n"
        + "def ptr_local_arm(h: H, flag: bool) -> Int32:\n"
        + "    p = h.a\n"
        + "    if flag:\n        p = h.b\n"
        + "    return take_opt(p)\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("none_arm", "name_arm", "rvalue_arm", "opt_local_arm",
                     "field_arm", "ptr_local_arm"):
            assert _fn(thir, name) is not None, name

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        assert out == _cpp(self.SRC, thir=False)
        assert "return take_opt(nullptr);" in out
        assert "return take_opt(&(a));" in out
        assert "A __tmp_1 = A(7);\n    return take_opt(&(__tmp_1));" in out
        assert "return take_opt(o);" in out                    # bare pass
        assert "return take_opt(::tpy::optional_to_ptr(h.opt));" in out
        assert "return take_opt(p);" in out                    # bare pointer

    def test_dump_shapes(self):
        from .dump import dump_thir
        text = dump_thir(_lower_ctx(self.SRC))
        assert "optptr{nullptr}" in text
        assert "optptr{&(%a)}" in text
        assert "optptr{optional_to_ptr(" in text
        assert "%argtmp(A addr){ctor(A, " in text

    def test_witnesses_optptr_faces(self):
        # 'lift' fires for the storage-Optional field read (field_arm);
        # 'pass' fires for the already-`T*` bindings (opt_local_arm's
        # OPTIONAL_TO_PTR local + ptr_local_arm's F2 pointer-local, which
        # classifies 'name' but splits to the bare pass at lowering).
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("optptr.lift", 0) == 1
        assert w.get("optptr.pass", 0) == 2
        assert "optptr.none" in w
        assert "optptr.name" in w
        assert "optptr.ctor_rvalue" in w


class TestOptionalPtrNarrowedArgs:
    def test_narrowed_subject_arg_routes_via_alias_addr(self):
        # A narrowed union subject into an `A | None` slot classifies 'name':
        # the read renames to the extraction alias and both paths wrap the
        # address-of (`take_opt(&(__u))`). The temp-free face lowers directly
        # (no narrowed threading), so the render MIRRORS rather than rejects --
        # a reject only at lowering was the drift this test pins against.
        src = (
            _PRELUDE
            + "def take_opt(o: A | None) -> Int32:\n"
            + "    if o is None:\n        return 0\n"
            + "    return o.x\n"
            + "def use(u: A | B) -> Int32:\n"
            + "    if isinstance(u, A):\n        return take_opt(u)\n"
            + "    return 0\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "return take_opt(&(__u));" in out
        assert _fn(_lower_ctx(src), "use") is not None

    def test_inline_narrowed_subject_arg_routes(self):
        # The compound-condition twin: the inline get renders inside the
        # same `&(...)` wrap on both paths.
        src = (
            _PRELUDE
            + "def take_opt(o: A | None) -> Int32:\n"
            + "    if o is None:\n        return 0\n"
            + "    return o.x\n"
            + "def use(u: A | B) -> Int32:\n"
            + "    if isinstance(u, A) and take_opt(u) > 0:\n"
            + "        return 1\n"
            + "    return 0\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "take_opt(&((*std::get<A*>(u))))" in out
        assert _fn(_lower_ctx(src), "use") is not None


class TestOptionalPtrGateRejects:
    def test_self_arg_stays_ast(self):
        # `self` as an optional-ptr arg is classifier-rejected (its render
        # is `this`, not an addressable record lvalue).
        src = _src(
            "def take_opt(o: A | None) -> Int32:\n"
            "    if o is None:\n        return 0\n"
            "    return o.x\n",
            extra_a="    def m(self) -> Int32:\n"
                    "        return take_opt(self)\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "m") is None

    def test_optional_param_routes_bare_pass(self):
        # A pointer-repr Optional PARAM is admitted: `use` passes the
        # already-`T*` name bare into the Optional slot, and `take_opt`'s
        # None-narrowing body routes too.
        src = (
            _PRELUDE
            + "def take_opt(o: A | None) -> Int32:\n"
            + "    if o is None:\n        return 0\n"
            + "    return o.x\n"
            + "def use(o: A | None) -> Int32:\n    return take_opt(o)\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert _fn(thir, "take_opt") is not None

    def test_ctor_rvalue_in_non_flushable_position_stays_ast(self):
        # The &(__tmp_N) hoist needs a flush point -- a nested call arg has
        # none.
        src = (
            _PRELUDE
            + "def take_opt(o: A | None) -> Int32:\n"
            + "    if o is None:\n        return 0\n"
            + "    return o.x\n"
            + "def g(x: Int32) -> Int32:\n    return x\n"
            + "def use() -> Int32:\n    return g(take_opt(A(7)))\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert _fn(_lower_ctx(src), "use") is None


class TestArgTempGateRejects:
    # Each shape hoists a temp at a position with no (safe) flush point on
    # the AST path -- pinned unrouted so the AST behavior (incl. the BUGS.md
    # while-condition stale-snapshot hoist) is never mirrored.

    def test_while_condition_stays_ast(self):
        thir = _lower(_VU_PRELUDE
                      + "def f(k: Int32) -> Int32:\n"
                      + "    n = 0\n"
                      + "    while take_vu(k) > n:\n        n += 1\n"
                      + "    return n\n")
        assert _fn(thir, "f") is None

    def test_if_and_elif_conditions_stay_ast(self):
        # An elif temp makes the AST abandon the flat `else if` chain and
        # nest -- the deferred chain-abandon relocation shape.
        thir = _lower(_VU_PRELUDE
                      + "def f(k: Int32) -> Int32:\n"
                      + "    if take_vu(k) == 1:\n        return 1\n"
                      + "    return 0\n"
                      + "def g(k: Int32) -> Int32:\n"
                      + "    if k == 0:\n        return 0\n"
                      + "    elif take_vu(k) == 1:\n        return 1\n"
                      + "    return 2\n")
        assert _fn(thir, "f") is None
        assert _fn(thir, "g") is None

    def test_for_iterable_stays_ast(self):
        thir = _lower(_VU_PRELUDE
                      + "from tpy import Own\n"
                      + "def make(v: Int32 | Float64) -> Own[list[Int32]]:\n"
                      + "    return [1, 2]\n"
                      + "def f(k: Int32) -> Int32:\n"
                      + "    total = 0\n"
                      + "    for x in make(k):\n        total += x\n"
                      + "    return total\n")
        assert _fn(thir, "f") is None

    def test_nested_call_positions_stay_ast(self):
        # A print arg is a flush position (temps hoist before the cout
        # chain), but a binop operand is not the direct statement value --
        # temps never propagate inward there.
        src = (_VU_PRELUDE
               + "def f(k: Int32):\n    print(take_vu(k))\n"
               + "def g(k: Int32) -> Int32:\n"
               + "    return take_vu(k) + 1\n")
        thir = _lower(src)
        assert _fn(thir, "f") is not None
        assert _fn(thir, "g") is None
        # The print-arg temp must FLUSH before the cout line, not just route.
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_scalar_field_write_value_routes(self):
        # A field-write assign is the fifth flushable position (see
        # TestArgTempEmit.test_field_write_position_flushes for the emit pin).
        thir = _lower_ctx(_PRELUDE
                          + "from tpy import Float64\n"
                          + "def take_vu(v: Int32 | Float64) -> Int32:\n"
                          + "    return 0\n"
                          + "def f(a: A, k: Int32):\n    a.x = take_vu(k)\n")
        assert _fn(thir, "f") is not None

    def test_narrowed_subject_value_union_arg_stays_ast(self):
        # A narrowed subject's C++ binding is still the variant: the AST's
        # `already_union` verdict renders it bare (no temp), so the temp row
        # must not fire on the retyped read.
        thir = _lower(_VU_PRELUDE
                      + "def f(v: Int32 | Float64) -> Int32:\n"
                      + "    if isinstance(v, Int32):\n"
                      + "        return take_vu(v)\n"
                      + "    return 0\n")
        assert _fn(thir, "f") is None

    def test_upcast_ctor_arg_stays_ast(self):
        # A Child-typed ctor into a Parent slot declares the CHILD's type
        # (the anti-slicing upcast temp) -- same-nominal only.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Base:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "class Child(Base):\n"
            "    def __init__(self, x: Int32):\n        super().__init__(x)\n"
            "def take_base(b: Base) -> Int32:\n    return b.x\n"
            "def f() -> Int32:\n    return take_base(Child(7))\n")
        assert _fn(thir, "f") is None

    def test_own_union_slot_name_still_moves_ast(self):
        # The temp rows must not swallow the Own[union] auto-move shape.
        thir = _lower_ctx(_PRELUDE
                          + "def take_own(v: Own[A | B]) -> Int32:\n"
                          + "    return 0\n"
                          + "def f(v: Own[A | B]) -> Int32:\n"
                          + "    return take_own(v)\n")
        assert _fn(thir, "f") is None

    def test_method_call_rvalue_args_never_temp(self):
        # Method-call args never temp through this facility: the AST inlines
        # them. The CONST-slot face routes via the inline THIRCtorCall
        # (no temp -- pinned by test_emitted_shapes); the MUTATED-slot face
        # is the BUGS.md mutated-ref miscompile and must stay AST.
        thir = _lower_ctx(_src(
            "def f(a: A) -> Int32:\n"
            "    a.absorb(A(4))\n"
            "    return a.x\n",
            extra_a="    def absorb(self, other: A):\n"
                    "        other.x += 1\n"
                    "        self.x += other.x\n"))
        assert _fn(thir, "f") is None


class TestMacroNameAssignFlush:
    """flush.assign -- the name-target TpyAssign flush position. The parser
    emits TpyVarDecl for every ordinary `name = expr` (reassignments
    included), so a TpyAssign with a NAME target only arises from
    macro-authored / frontend-IR ASTs. A @function_macro builds the shape
    here (ast.assign over a live body slot) so the face has a witness: its
    value call hoists the variant temp at the assign's own flush point."""

    _MACRO_MOD = (
        "# tpy: macro_module\n"
        "from tpyc.macro_api import function_macro, FunctionMacroContext, ast\n"
        "\n"
        "\n"
        "@function_macro\n"
        "def rebind_via_assign(ctx: FunctionMacroContext) -> None:\n"
        "    # ctx.body is the live statement list; swap the marker rebind\n"
        "    # (`r = k`, a TpyVarDecl) for a raw name-target TpyAssign.\n"
        "    ctx.body[1] = ast.assign(ast.name('r'),\n"
        "                             ast.call('take_vu', [ast.name('k')]))\n"
    )

    _MAIN = (
        "from thir_faces_macromod import rebind_via_assign\n"
        "from tpy import Int32, Float64\n"
        "def take_vu(v: Int32 | Float64) -> Int32:\n"
        "    if isinstance(v, Int32):\n        return v\n"
        "    return 0\n"
        "@rebind_via_assign\n"
        "def use(k: Int32) -> Int32:\n"
        "    r = 0\n"
        "    r = k\n"
        "    return r\n"
    )

    def _dirs(self, tmp_path):
        (tmp_path / "thir_faces_macromod.py").write_text(self._MACRO_MOD)
        return [tmp_path]

    def test_macro_name_assign_routes_and_witnesses(self, tmp_path):
        thir, w = _lower_ctx_witnessed(self._MAIN,
                                       extra_lib_dirs=self._dirs(tmp_path))
        assert _fn(thir, "use") is not None
        assert "flush.assign" in w
        assert "argtemp.value_union" in w

    def test_byte_identical(self, tmp_path):
        dirs = self._dirs(tmp_path)
        out = _cpp(self._MAIN, thir=True, extra_lib_dirs=dirs)
        assert out == _cpp(self._MAIN, thir=False, extra_lib_dirs=dirs)
        assert ("std::variant<int32_t, double> __tmp_1 = k;\n"
                "    r = take_vu(__tmp_1);") in out


class TestCtorShapeGateRejects:
    """Reject arms of the record-ctor shape core (`_ctor_shape_ok`) and the
    scalar-slot arg loop (`_record_ctor_arg_supported`), pinned through the
    record-rvalue arg-temp row (`take_x(X(...))` in return position). Arms
    NOT expressible in this single-module harness: the same-name free-fn
    collision (sema resolves the call to the free fn, so a record-slot
    program is a sema type error before the gate is consulted). Cross-module
    ctors ROUTE (the qualified spelling) -- see
    TestCrossModuleRecordFrontier in test_thir_methods.py."""

    def test_plain_scalar_ctor_routes(self):
        # The route baseline the reject arms are paired against.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class R:\n    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "def take_r(r: R) -> Int32:\n    return r.x\n"
            "def use() -> Int32:\n    return take_r(R(5))\n")
        fn = _fn(thir, "use")
        assert fn is not None
        arg = fn.body[0].value.args[0]
        assert isinstance(arg, THIRArgTemp)
        assert isinstance(arg.init, THIRCtorCall) and arg.init.type_cpp == "R"

    def test_omitted_default_arity_routes(self):
        # An omitted TRAILING default rides the C++ ctor signature (records.py
        # emits it via emit_defaults), so the call passes only the provided args
        # -- byte-identical to the full-arity `Name(args)` emit.
        src = (
            "from tpy import Int32\n"
            "class R:\n    x: Int32\n"
            "    def __init__(self, x: Int32 = 0):\n        self.x = x\n"
            "def take_r(r: R) -> Int32:\n    return r.x\n"
            "def use() -> Int32:\n    return take_r(R())\n"
            "def use_full() -> Int32:\n    return take_r(R(5))\n")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert _fn(thir, "use_full") is not None

    def test_generic_record_ctor_routes_instantiation(self):
        # A generic ctor rvalue spells the rendered instantiation
        # (`G<int32_t>(5)`) -- the wave-7 _ctor_instantiation_ok face.
        thir = _lower_ctx(
            "from tpy import Int32, ValueType\n"
            "class G[T: ValueType]:\n    v: T\n"
            "    def __init__(self, v: T):\n        self.v = v\n"
            "def take_g(g: G[Int32]) -> Int32:\n    return g.v\n"
            "def use() -> Int32:\n    return take_g(G(5))\n")
        fn = _fn(thir, "use")
        assert fn is not None
        arg = fn.body[0].value.args[0]
        assert isinstance(arg, THIRArgTemp) and arg.cpp_type == "G<int32_t>"
        assert (isinstance(arg.init, THIRCtorCall)
                and arg.init.type_cpp == "G<int32_t>")

    def test_multi_overload_init_stays_ast(self):
        # _gen_record_ctor_args reads the record's init_info params, which
        # may disagree with the resolved stub for an overload set.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "from typing import overload\n"
            "class V:\n    x: Int32\n"
            "    @overload\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "    @overload\n"
            "    def __init__(self):\n        self.x = 0\n"
            "def take_v(v: V) -> Int32:\n    return v.x\n"
            "def use() -> Int32:\n    return take_v(V(5))\n")
        assert _fn(thir, "use") is None

    def test_native_record_ctor_stays_ast(self):
        # A @native record CTOR-RVALUE arg (`NR(5)`) stays AST: the native
        # ctor's construction/temp semantics aren't in the ctor-rvalue arg
        # slice. The NR slot type itself IS now in-slice (`_f1_record` admits
        # native records), so a NAME arg `take_nr(r)` routes -- only the ctor
        # rvalue keeps `use` on the AST path.
        thir = _lower_ctx(
            "from tpy.extern import native\n"
            "from tpy import Int32\n"
            "@native\n"
            "class NR:\n    x: Int32\n"
            "    def __init__(self, x: Int32): ...\n"
            "def take_nr(r: NR) -> Int32:\n    return r.x\n"
            "def use() -> Int32:\n    return take_nr(NR(5))\n")
        assert _fn(thir, "use") is None

    def test_own_scalar_ctor_param_nested_temp_routes(self):
        # An Own[scalar] __init__ slot copy+moves a NAME arg through a temp.
        # When that ctor is itself a record-rvalue temp at a flushing statement
        # (`take_o(O(k))`), the nested copy temp flushes at the SAME statement
        # point (innermost-first), byte-identical to the AST's statement-level
        # flush: `auto __tmp_1 = k; O __tmp_2 = O(std::move(__tmp_1)); ...`.
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            "class O:\n    x: Int32\n"
            "    def __init__(self, x: Own[Int32]):\n        self.x = x\n"
            "def take_o(o: O) -> Int32:\n    return o.x\n"
            "def use(k: Int32) -> Int32:\n    return take_o(O(k))\n")
        fn = _fn(thir, "use")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRCall)
        outer = ret.value.args[0]
        assert isinstance(outer, THIRArgTemp)          # O __tmp_2 = O(...)
        assert isinstance(outer.init, THIRCtorCall)
        inner = outer.init.args[0]
        assert isinstance(inner, THIRArgTemp) and inner.move  # __tmp_1 = k

    def test_union_ctor_param_stays_ast(self):
        # A member-valued scalar into a union-typed __init__ slot hoists a
        # variant temp on the AST path (`_member_valued_union_slot`). A VALUE
        # union (scalar members) is not a pointer-variant, so the member-lift
        # arms don't fire -- it stays AST.
        thir = _lower_ctx(
            "from tpy import Int32, Float64\n"
            "class W:\n    u: Int32 | Float64\n"
            "    def __init__(self, v: Int32 | Float64):\n        self.u = v\n"
            "def take_w(w: W) -> Int32:\n    return 0\n"
            "def use(k: Int32) -> Int32:\n    return take_w(W(k))\n")
        assert _fn(thir, "use") is None


class TestCtorArgUnionOptionalProtocol:
    """Ctor-arg extensions mirrored from the free-call plain-arg loop
    into `_record_ctor_arg_supported` and lowered through the shared
    `_lower_call_arg`: the pointer-variant union lift (member NAME + member
    ctor RVALUE temp), the `Own[record | None]` rvalue / None rows, and the
    @dynamic-protocol conformer arg. Each must byte-mirror the AST oracle."""

    _UNION = (
        "from tpy import Int32\n"
        "class Circle:\n    r: Int32\n"
        "    def __init__(self, r: Int32) -> None:\n        self.r = r\n"
        "class Square:\n    s: Int32\n"
        "    def __init__(self, s: Int32) -> None:\n        self.s = s\n"
        "class Canvas:\n    shape: Circle | Square\n"
        "    def __init__(self, s: Circle | Square) -> None:\n        self.shape = s\n"
        "def take(c: Canvas) -> Int32:\n    return 0\n"
        "def use_union() -> Int32:\n    return take(Canvas(Circle(5)))\n"
    )

    def test_union_ctor_arg_routes(self):
        assert _fn(_lower_ctx(self._UNION), "use_union") is not None

    def test_union_ctor_arg_byte_identical(self):
        _assert_byte_identical(self._UNION)

    def test_union_ctor_arg_nested_temp_shape(self):
        # The nested member ctor rvalue hoists its own temp at the SAME
        # statement flush, innermost-first, then lifts its address into the
        # pointer variant.
        out = _cpp(self._UNION, thir=True)
        assert "Circle __tmp_1 = Circle(5);" in out
        assert ("Canvas __tmp_2 = Canvas(std::variant<Circle*, Square*>"
                "{&__tmp_1});") in out

    _OWNOPT = (
        "from tpy import Int32\n"
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class Inner:\n    x: Int32\n"
        "@dataclass\n"
        "class Outer:\n    name: str\n    inner: Inner | None = None\n"
        "def take(o: Outer) -> Int32:\n    return 0\n"
        "def use_optrec() -> None:\n"
        "    take(Outer(\"a\", Inner(42)))\n"
        "    take(Outer(\"c\", None))\n"
    )

    def test_own_optional_record_arg_routes(self):
        assert _fn(_lower_ctx(self._OWNOPT), "use_optrec") is not None

    def test_own_optional_record_arg_shape(self):
        # An `Own[record | None]` slot binds a record RVALUE bare (prvalue ->
        # optional<Inner>) and a None literal as std::nullopt (storage-form).
        out = _cpp(self._OWNOPT, thir=True)
        assert 'Outer __tmp_1 = Outer("a", Inner(42));' in out
        assert 'Outer __tmp_2 = Outer("c", std::nullopt);' in out

    _PROTO = (
        "from typing import Protocol\n"
        "from tpy import Int32, Ptr, dynamic, nocopy\n"
        "@dynamic\n"
        "class Awaker(Protocol):\n    def mark(self, t: Int32) -> None: ...\n"
        "@nocopy\n"
        "class Holder:\n    awaker: Ptr[Awaker]\n"
        "    def __init__(self, h: Awaker) -> None:\n        self.awaker = h\n"
        "class Exec(Awaker):\n    v: Int32\n"
        "    def __init__(self) -> None:\n        self.v = 0\n"
        "    def mark(self, t: Int32) -> None:\n        self.v = t\n"
        "def use_proto() -> None:\n"
        "    e = Exec()\n    h = Holder(e)\n    h.awaker.mark(1)\n"
    )

    def test_protocol_conformer_ctor_arg_routes(self):
        assert _fn(_lower_ctx(self._PROTO), "use_proto") is not None

    def test_protocol_conformer_ctor_arg_bare(self):
        # A base-class-conforming lvalue passes bare into the @dynamic
        # protocol ctor slot (no adapter temp).
        out = _cpp(self._PROTO, thir=True)
        assert "Holder h = Holder(e);" in out
        assert _cpp(self._PROTO, thir=True) == _cpp(self._PROTO, thir=False)


def _find_call(node, name):
    """First TpyCall whose callee bare-name is `name`, found by a shallow walk
    over the parse-tree dataclass fields."""
    from dataclasses import fields, is_dataclass
    from ..parse.nodes import TpyCall
    stack = [node]
    while stack:
        n = stack.pop()
        if (isinstance(n, TpyCall)
                and getattr(getattr(n, "func", None), "name", None) == name):
            return n
        if is_dataclass(n) and not isinstance(n, type):
            for f in fields(n):
                v = getattr(n, f.name, None)
                if isinstance(v, (list, tuple)):
                    stack.extend(v)
                elif is_dataclass(v) and not isinstance(v, type):
                    stack.append(v)
    return None


def test_native_iterable_call_arg_admits_container_call():
    """`call.native_arg.call_rvalue`: a container-returning CALL rvalue
    into a native builtin's Iterable slot (`zip(get_names(), get_scores())`) is
    admitted by the classifier. The whole body is interlocked on the
    container-return value-position gate (return-side track), so it does not
    fully route yet -- but the arg itself is no longer the reject."""
    from .lower.predicates import _native_iterable_call_arg
    from ..compilation_context import activate_compiler
    src = (
        "from tpy import Own, Int32\n"
        "def get_names() -> Own[list[str]]:\n    return [\"a\"]\n"
        "def get_scores() -> Own[list[Int32]]:\n    return [1]\n"
        "def main() -> None:\n"
        "    for n, s in zip(get_names(), get_scores()):\n        print(n, s)\n")
    compiler, modules = _compile(src)
    entry = _entry(modules)
    with activate_compiler(compiler):
        analyzer = entry.analyzer
        main_fn = next(f for f in entry.ast.functions if f.name == "main")
        zip_call = _find_call(main_fn, "zip")
        assert zip_call is not None
        ptype = zip_call.resolved_function_info.params[0].type
        # a container-returning call rvalue into the Iterable slot: admitted
        assert _native_iterable_call_arg(zip_call.args[0], ptype, analyzer)
        # the zip call itself returns an Iterator (not a container): rejected
        assert not _native_iterable_call_arg(zip_call, ptype, analyzer)


class TestCtorStrArgSlots:
    """The str-family arm of `_record_ctor_arg_supported`'s arg loop -- the
    free-call pass-through rule applied to ctor slots (`ctor.str_arg`)."""

    _STR_R = (
        "class R:\n"
        "    name: str\n"
        "    def __init__(self, name: str) -> None:\n"
        "        self.name = name\n"
        "def take_r(r: R) -> int:\n"
        "    return 1\n"
    )

    def test_str_sources_route_byte_identical(self):
        # Literal, view-param, and owned-local sources into a `str` slot all
        # land bare, exactly like the free-call pass-through.
        src = (
            self._STR_R
            + "def use_lit() -> int:\n"
            + '    return take_r(R("a"))\n'
            + "def use_param(s: str) -> int:\n"
            + "    return take_r(R(s))\n"
            + "def use_owned() -> int:\n"
            + '    ow = "x" + "y"\n'
            + "    return take_r(R(ow))\n"
            + "print(use_lit())\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        for name in ("use_lit", "use_param", "use_owned"):
            assert _fn(thir, name) is not None, name
        assert w.get("ctor.str_arg", 0) >= 3
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_string_slot_routes_byte_identical(self):
        # A non-mutated `String` slot is `const std::string&`; the literal
        # arrives through the str_to_string coerce arm on both paths.
        src = (
            "from tpy import String\n"
            "class S:\n"
            "    name: String\n"
            "    def __init__(self, name: String) -> None:\n"
            "        self.name = name\n"
            "def take_s(s: S) -> int:\n"
            "    return 1\n"
            "def use() -> int:\n"
            '    return take_s(S("a"))\n'
            "print(use())\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("ctor.str_arg", 0) > 0
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_string_slot_view_source_routes_byte_identical(self):
        # A view source (str param) into a String slot arrives through the
        # strview_to_string coerce arm on both paths.
        src = (
            "from tpy import String\n"
            "class S:\n"
            "    name: String\n"
            "    def __init__(self, name: String) -> None:\n"
            "        self.name = name\n"
            "def take_s(s: S) -> int:\n"
            "    return 1\n"
            "def use(s: str) -> int:\n"
            "    return take_s(S(s))\n"
            'print(use("v"))\n'
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("ctor.str_arg", 0) > 0
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_method_ctor_rvalue_str_arg_routes(self):
        # The widened arg loop applies at the method-ctor-rvalue face too
        # (a str-arg ctor rvalue into a const same-record method slot).
        src = (
            "class R:\n"
            "    name: str\n"
            "    def __init__(self, name: str) -> None:\n"
            "        self.name = name\n"
            '    def eat(self, other: "R") -> int:\n'
            "        return 1\n"
            "def use(a: R) -> int:\n"
            '    return a.eat(R("z"))\n'
            'print(use(R("a")))\n'
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("ctor.str_arg", 0) > 0
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_mutated_string_slot_stays_ast(self):
        # A MUTATED String slot lowers `std::string&`, where the AST's
        # ctor_mutated rvalue-temp arm can fire. (Today the AST emit for the
        # mutating ctor body is itself ill-formed C++ -- see the BUGS.md
        # mutated-String-param entry -- so the shape must never route.)
        src = (
            "from tpy import String\n"
            "class S:\n"
            "    name: String\n"
            "    def __init__(self, name: String) -> None:\n"
            '        name += "!"\n'
            "        self.name = name\n"
            "def take_s(s: S) -> int:\n"
            "    return 1\n"
            "def use() -> int:\n"
            '    return take_s(S("a"))\n'
            "print(use())\n"
        )
        assert _fn(_lower_ctx(src), "use") is None

    def test_own_str_slot_stays_ast(self):
        # An Own[str] slot materializes an owned copy (the auto-move
        # cascade); `_str_pass_through_arg` rejects Own.
        src = (
            "from tpy import Own\n"
            "class O:\n"
            "    name: str\n"
            "    def __init__(self, name: Own[str]) -> None:\n"
            "        self.name = name\n"
            "def take_o(o: O) -> int:\n"
            "    return 1\n"
            "def use(s: str) -> int:\n"
            "    return take_o(O(s))\n"
            'print(use("a"))\n'
        )
        assert _fn(_lower_ctx(src), "use") is None

    def test_record_rvalue_return_const_string_slot_routes(self):
        # `return S("a")` at an Own[S] record slot with a NON-mutated String
        # slot: `_is_record_rvalue_source`'s ctor face shares the free-call
        # cascade for const slots, so the str-literal arg routes bare.
        src = (
            "from tpy import String, Own\n"
            "class S:\n"
            "    name: String\n"
            "    def __init__(self, name: String) -> None:\n"
            "        self.name = name\n"
            "def use() -> Own[S]:\n"
            '    return S("a")\n'
            "print(use().name)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_record_rvalue_return_mutated_string_slot_stays_ast(self):
        # The mutated-slot guard on `_is_record_rvalue_source`'s ctor face: a
        # MUTATED String slot (`std::string&`) rejects the str-literal arg (a
        # temp into a non-const ref is the mutated-String-param miscompile),
        # keeping the whole `return S("a")` body on the AST path.
        src = (
            "from tpy import String, Own\n"
            "class S:\n"
            "    name: String\n"
            "    def __init__(self, name: String) -> None:\n"
            '        name += "!"\n'
            "        self.name = name\n"
            "def use() -> Own[S]:\n"
            '    return S("a")\n'
            "print(use().name)\n"
        )
        assert _fn(_lower_ctx(src), "use") is None


class TestCtorMutatedSlotArgs:
    """The mutated-slot half of `_is_record_rvalue_source`'s ctor face:
    `_shared_pass_through_arg(mutated=True)` admits the by-value and
    lvalue-NAME rows into a `T&` slot and keeps the temp rows off."""

    def test_record_name_into_mutated_ctor_slot_routes(self):
        # An lvalue record NAME binds the mutated `A&` slot legally -- the
        # bare-name emit on both paths.
        src = (
            "from tpy import Int32, Own\n"
            "class A:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n"
            "        self.x = x\n"
            "class W:\n"
            "    total: Int32\n"
            "    def __init__(self, a: A):\n"
            "        a.x += 1\n"
            "        self.total = a.x\n"
            "def use(a: A) -> Own[W]:\n"
            "    return W(a)\n"
            "print(use(A(1)).total)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_container_name_into_mutated_ctor_slot_routes(self):
        # An lvalue container NAME binds the mutated `std::vector<T>&` slot
        # legally -- the bare-name emit on both paths.
        src = (
            "from tpy import Int32, Own\n"
            "class W:\n"
            "    n: Int32\n"
            "    def __init__(self, xs: list[Int32]):\n"
            "        xs.append(9)\n"
            "        self.n = len(xs)\n"
            "def use(xs: list[Int32]) -> Own[W]:\n"
            "    return W(xs)\n"
            "print(use([1, 2]).n)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_view_str_source_into_mutated_string_slot_stays_ast(self):
        # A str (view) source into a MUTATED String slot arrives through the
        # strview_to_string coerce -- a temp into `std::string&` -- so the
        # coerce half stays gate-rejected under mutated.
        src = (
            "from tpy import String, Own\n"
            "class S:\n"
            "    name: String\n"
            "    def __init__(self, name: String) -> None:\n"
            '        name += "!"\n'
            "        self.name = name\n"
            "def use(s: str) -> Own[S]:\n"
            "    return S(s)\n"
            'print(use("a").name)\n'
        )
        assert _fn(_lower_ctx(src), "use") is None

    def test_int_literal_into_mutated_bigint_slot_routes(self):
        # A BigInt slot stays by value regardless of the mutation fact
        # (mutation is callee-local), so the slot-typed literal render
        # (`::tpy::BigInt(5)`) is safe -- the row the old hand-rolled
        # mutated arm omitted.
        src = (
            "from tpy import Own\n"
            "class S:\n"
            "    n: int\n"
            "    def __init__(self, n: int) -> None:\n"
            "        n += 1\n"
            "        self.n = n\n"
            "def use() -> Own[S]:\n"
            "    return S(5)\n"
            "print(use().n)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_value_rows_alongside_mutated_slot_route(self):
        # Value-family args (enum member, float literal) ride their by-value
        # rows while a SIBLING mutated container slot exercises the mutated
        # arm -- value slots never lower `T&` (mutation is callee-local), so
        # the cascade admits them mutation-blind.
        src = (
            "from tpy import Int32, Own, Float64\n"
            "from enum import Enum\n"
            "class Color(Enum):\n"
            "    RED = 1\n"
            "    BLUE = 2\n"
            "class W:\n"
            "    n: Int32\n"
            "    c: Color\n"
            "    f: Float64\n"
            "    def __init__(self, xs: list[Int32], c: Color, f: Float64):\n"
            "        xs.append(9)\n"
            "        self.n = len(xs)\n"
            "        self.c = c\n"
            "        self.f = f\n"
            "def use(xs: list[Int32], c: Color) -> Own[W]:\n"
            "    return W(xs, c, 1.5)\n"
            "print(use([1], Color.RED).n)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestArgTempValidator:
    def _temp(self):
        from ..typesys import FLOAT, INT32, UnionType
        ut = UnionType(members=(INT32, FLOAT))
        return THIRArgTemp(
            result_type=ut, cpp_type="std::variant<int32_t, double>",
            init=THIRName(result_type=INT32, name="k"))

    def _call(self, arg):
        from ..typesys import INT32
        return THIRCall(result_type=INT32, callee="take_vu", args=(arg,))

    def _method_call(self, arg):
        from ..typesys import INT32
        return THIRMethodCall(
            result_type=INT32, method_cpp="tag", args=(arg,),
            receiver=THIRName(result_type=INT32, name="a"))

    def test_temp_under_method_call_flushable_passes(self):
        from ..typesys import INT32
        from .nodes import THIRFunction, THIRFunctionLayout
        from .validate import validate_function
        fn = THIRFunction(
            name="t", params=(), return_type=INT32,
            body=(THIRReturn(value=self._method_call(self._temp())),),
            layout=THIRFunctionLayout())
        validate_function(fn)

    def test_temp_under_method_call_condition_raises(self):
        import pytest
        from ..typesys import VoidType
        from .nodes import THIRFunction, THIRFunctionLayout, THIRWhile
        from .validate import THIRValidationError, validate_function
        loop = THIRWhile(condition=self._method_call(self._temp()), body=())
        fn = THIRFunction(name="t", params=(), return_type=VoidType(),
                          body=(loop,), layout=THIRFunctionLayout())
        with pytest.raises(THIRValidationError,
                           match="non-flushable statement position"):
            validate_function(fn)

    def test_temp_under_flushable_positions_passes(self):
        from ..typesys import INT32, VoidType
        from .nodes import THIRFunction, THIRFunctionLayout, THIRVarDecl
        from .validate import validate_function
        fn = THIRFunction(
            name="t", params=(), return_type=INT32,
            body=(THIRVarDecl(name="r", resolved_type=INT32,
                              init=self._call(self._temp())),
                  THIRExprStmt(expr=self._call(self._temp())),
                  THIRReturn(value=self._call(self._temp()))),
            layout=THIRFunctionLayout())
        validate_function(fn)

    def test_temp_under_condition_raises(self):
        import pytest
        from ..typesys import INT32, VoidType
        from .nodes import THIRFunction, THIRFunctionLayout, THIRWhile
        from .validate import THIRValidationError, validate_function
        loop = THIRWhile(condition=self._call(self._temp()), body=())
        fn = THIRFunction(name="t", params=(), return_type=VoidType(),
                          body=(loop,), layout=THIRFunctionLayout())
        with pytest.raises(THIRValidationError,
                           match="non-flushable statement position"):
            validate_function(fn)

    def test_temp_outside_call_arg_raises(self):
        import pytest
        from ..typesys import INT32
        from .nodes import THIRFunction, THIRFunctionLayout
        from .validate import THIRValidationError, validate_function
        fn = THIRFunction(name="t", params=(), return_type=INT32,
                          body=(THIRReturn(value=self._temp()),),
                          layout=THIRFunctionLayout())
        with pytest.raises(THIRValidationError,
                           match="outside a call arg position"):
            validate_function(fn)


class TestNoneValueOptArg:
    # A bare `None` into a value-repr Optional param slot -> `f(std::nullopt)`,
    # for every non-pointer-repr inner (scalar / Char / float / BigInt / str /
    # bytes-view / value-tuple). The value-optional twin of the pointer-repr
    # `nullptr` None arg (which already routed via the optional-ptr face).
    _OPT = (
        "from tpy import Int32, Float32, Char, BytesView\n"
        "class Box:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n        self.x = x\n"
        "def use_scalar(n: Int32 | None):\n    print(1)\n"
        "def use_str(s: str | None):\n    print(1)\n"
        "def use_char(c: Char | None):\n    print(1)\n"
        "def use_bigint(n: int | None):\n    print(1)\n"
        "def use_tuple(t: tuple[Int32, Box] | None):\n    print(1)\n"
    )
    SRC = (
        _OPT
        + "def scalar_none():\n    use_scalar(None)\n"
        + "def str_none():\n    use_str(None)\n"
        + "def char_none():\n    use_char(None)\n"
        + "def bigint_none():\n    use_bigint(None)\n"
        + "def tuple_none():\n    use_tuple(None)\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emits_nullopt(self):
        out = _cpp(self.SRC, thir=True)
        assert "use_scalar(std::nullopt);" in out
        assert "use_str(std::nullopt);" in out
        assert "use_char(std::nullopt);" in out
        assert "use_bigint(std::nullopt);" in out
        assert "use_tuple(std::nullopt);" in out

    def test_lowers_to_storage_none_literal(self):
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        for name in ("scalar_none", "str_none", "char_none", "bigint_none",
                     "tuple_none"):
            fn = _fn(thir, name)
            assert fn is not None, name
            arg = fn.body[0].expr.args[0]
            assert isinstance(arg, THIRLiteral) and arg.value is None
            assert arg.form is Form.STORAGE
        assert witnesses.get("call.none_value_opt", 0) >= 5

    def test_pointer_repr_optional_none_unchanged(self):
        # `Box | None` is pointer-repr -> `nullptr` (the optional-ptr None
        # face), NOT `std::nullopt`; the new value-repr row must not claim it.
        src = (
            self._OPT
            + "def use_rec(b: Box | None):\n    print(1)\n"
            + "def rec_none():\n    use_rec(None)\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        out = _cpp(src, thir=True)
        assert "use_rec(nullptr);" in out


class TestCtorValueOptNoneArg:
    # The CTOR face of the None-into-value-Optional row: `P(None)` with a
    # `Int32 | None` __init__ slot renders `P(std::nullopt)` -- the same
    # `_none_value_opt_arg` row the free-call cascade admits, consumed by
    # constructor argument lowering for the owned-record declaration init.
    SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32 | None = None):\n"
        "        self.n = 0 if n is None else n\n"
        "def make() -> None:\n    p = P(None)\n    print(p.n)\n"
    )

    def test_routes_with_none_witness(self):
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "make") is not None
        assert witnesses.get("call.none_value_opt", 0) >= 1

    def test_byte_identical_emits_nullopt(self):
        out = _cpp(self.SRC, thir=True)
        assert out == _cpp(self.SRC, thir=False)
        assert "P p = P(std::nullopt);" in out

    def test_coerced_scalar_into_optional_slot_routes(self):
        # `P(1)` -- sema types the arg as the WHOLE optional; the coerced-scalar
        # into value-repr optional slot now routes (wave-8 none_safety), byte-
        # identical to the AST's implicit `T -> std::optional<T>` conversion.
        src = self.SRC.replace("P(None)", "P(1)")
        thir = _lower_ctx(src)
        assert _fn(thir, "make") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestOwnSlotCtorArgs:
    # The Own-slot cascade rows mirrored onto the record-ctor arg loop
    # (`_record_ctor_arg_supported`): a movable name at last use moves
    # temp-free into the `Own[T]` __init__ slot; a still-live lvalue hoists
    # the copy+move `__tmp_N` at the flushable decl; rvalues ride the
    # pre-existing shared row.
    SRC = (
        "from tpy import Int32, Own\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n        self.x = x\n"
        "class W:\n"
        "    a: A\n"
        "    def __init__(self, a: Own[A]):\n        self.a = a\n"
        "    def get(self) -> Int32:\n        return self.a.x\n"
        "def move_local() -> Int32:\n"
        "    t = A(9)\n    w = W(t)\n    return w.get()\n"
        "def copy_arm(a: A) -> Int32:\n"
        "    w = W(a)\n    return w.get() + a.x\n"
        "def own_param_move(o: Own[A]) -> Int32:\n"
        "    w = W(o)\n    return w.get()\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_routes_with_witness(self):
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        for name in ("move_local", "copy_arm", "own_param_move"):
            assert _fn(thir, name) is not None, name
        assert witnesses.get("ctor.own_arg", 0) >= 3

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        # Body-movable local at last use: the temp-free move.
        assert "W w = W(std::move(t));" in out
        # Still-live param: the copy+move temp at the decl flush point.
        assert "auto __tmp_1 = a;\n    W w = W(std::move(__tmp_1));" in out
        # Own param at last use: temp-free move.
        assert "W w = W(std::move(o));" in out

    def test_nested_ctor_own_arg_stays_ast(self):
        # W(a) as a NESTED ctor arg (`take_w(W(a))`): the NESTED_ARG branch
        # keeps its narrower row set -- the own-lvalue copy needs a flush
        # point the nested position does not have.
        src = (self.SRC
               + "def take_w(w: Own[W]) -> Int32:\n    return w.get()\n"
               + "def nested(a: A) -> Int32:\n    return take_w(W(a))\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "nested") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestQualcallRecordStorageRet:
    # An F1-record RVALUE returned by a marker (static / module-qualified)
    # call, consumed at the owned-record decl (`A a = F.make(2);`): the
    # record_ret widening admits BORROW_BIND/STORAGE uses for rvalue
    # sources, mirroring the free-call value-position set.
    SRC = (
        "from tpy import Int32, Own\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n        self.x = x\n"
        "class F:\n"
        "    @staticmethod\n"
        "    def make(x: Int32) -> Own[A]:\n        return A(x)\n"
        "def use() -> Int32:\n"
        "    a = F.make(2)\n    return a.x\n"
    )

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "use") is not None
        out = _cpp(self.SRC, thir=True)
        assert "A a = F::make(2);" in out


class TestCtorListLiteralArg:
    # A list literal into a ctor's list slot renders the bare brace-init in
    # place (`Numbers({1, 2, 3});`) -- probe-verified against the AST for
    # scalar/str/record elements. Dict literals take a spelled
    # `::tpy::ordered_map<...>({{...}})` arg render and stay AST; free-call
    # literal args hoist a `__tmp_N` on the AST path and stay AST.
    SRC = (
        "from tpy import Int32\n"
        "class Numbers:\n"
        "    xs: list[Int32]\n"
        "    def __init__(self, xs: list[Int32]):\n        self.xs = xs\n"
        "def use() -> Int32:\n"
        "    n = Numbers([1, 2, 3])\n    return len(n.xs)\n"
    )

    def test_byte_identical_and_routes(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)
        thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert _fn(thir, "use") is not None
        assert witnesses.get("ctor.container_literal_arg", 0) >= 1
        assert "Numbers n = Numbers({1, 2, 3});" in _cpp(self.SRC, thir=True)

    def test_dict_literal_ctor_arg_stays_ast(self):
        src = (
            "from tpy import Int32\n"
            "class Table:\n"
            "    m: dict[str, Int32]\n"
            "    def __init__(self, m: dict[str, Int32]):\n        self.m = m\n"
            "def use() -> Int32:\n"
            "    t = Table({\"a\": 1})\n    return len(t.m)\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_free_call_list_literal_arg_stays_ast(self):
        src = (
            "from tpy import Int32\n"
            "def total(xs: list[Int32]) -> Int32:\n"
            "    t = 0\n"
            "    for x in xs:\n        t += x\n"
            "    return t\n"
            "def use() -> Int32:\n    return total([1, 2])\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestSelfRecordArg:
    # `self` as a free-call record arg renders the receiver deref
    # `on_init((*this))` -- the `_record_pass_through_arg` self arm + the
    # `_lower_call_arg` tail retag (mirrors the F2 pointer-local deref).
    SRC = (
        "from tpy import Int32\n"
        "class M:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32):\n        self.n = n\n"
        "    def fire(self):\n        on_init(self)\n"
        "    def calc(self) -> Int32:\n        return read_of(self)\n"
        "def on_init(m: M):\n    m.n += 1\n"
        "def read_of(m: M) -> Int32:\n    return m.n\n"
    )

    def test_byte_identical_and_routes(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "fire") is not None
        assert _fn(thir, "calc") is not None
        out = _cpp(self.SRC, thir=True)
        assert "on_init((*this));" in out
        assert "return read_of((*this));" in out


class TestQualcallOmittedDefaults:
    # A zero-arg marker call whose params all carry defaults omits them --
    # the defaults ride the C++ signature (`F::make()`), the free-call
    # `_call_arity_ok` rule mirrored onto the marker gate.
    SRC = (
        "from tpy import Int32, Own\n"
        "class A:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32):\n        self.x = x\n"
        "class F:\n"
        "    @staticmethod\n"
        "    def make(x: Int32 = 5) -> Own[A]:\n        return A(x)\n"
        "def use() -> Int32:\n"
        "    a = F.make()\n    return a.x\n"
    )

    def test_byte_identical_and_routes(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "use") is not None
        assert "A a = F::make();" in _cpp(self.SRC, thir=True)

    def test_partial_defaults_route(self):
        # Partial omission (one of two defaulted params): the omitted TRAILING
        # default rides the C++ signature, so the call passes only the
        # provided arg -- `F::make(1)`.
        src = self.SRC.replace(
            "def make(x: Int32 = 5) -> Own[A]:",
            "def make(x: Int32 = 5, y: Int32 = 2) -> Own[A]:"
        ).replace("return A(x)", "return A(x + y)").replace(
            "F.make()", "F.make(1)")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert "A a = F::make(1);" in _cpp(src, thir=True)


class TestPartialOmittedDefaults:
    # Omitted TRAILING defaults on plain free calls and user-record method
    # calls: the defaults ride the emitted C++ signature (`emit_defaults`),
    # both paths pass only the provided args, and the provided args pair the
    # LEADING params (the AST arg loops zip-truncate).

    def test_free_call_partial_defaults_route(self):
        src = (
            "from tpy import Int32\n"
            "def f(a: Int32, b: Int32 = 2) -> Int32:\n    return a + b\n"
            "def use() -> Int32:\n    return f(1)\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert "return f(1);" in _cpp(src, thir=True)

    def test_free_call_pairing_threads_leading_slot(self):
        # The provided arg must lower against ITS param slot, not slot-less:
        # a float literal into a Float32 slot takes the `f` suffix only when
        # the pairing threads params[0] through the truncated call.
        src = (
            "from tpy import Int32, Float32\n"
            "def f(a: Float32, b: Int32 = 2) -> Float32:\n"
            "    return a * Float32(b)\n"
            "def use() -> Float32:\n    return f(1.5)\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert "return f(1.5f);" in _cpp(src, thir=True)

    def test_method_call_partial_defaults_route(self):
        src = (
            "from tpy import Int32\n"
            "class R:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "    def scale(self, k: Int32, extra: Int32 = 0) -> Int32:\n"
            "        return self.x * k + extra\n"
            "def use(r: R) -> Int32:\n    return r.scale(3)\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert "return r.scale(3);" in _cpp(src, thir=True)

    def test_variadic_empty_pack_routes(self):
        # An empty `*args` pack takes the nullary `::tpy::varargs<E>()` ctor
        # (no array temp) -- routes, no flushable position needed.
        src = (
            "from tpy import Int32\n"
            "def f(*args: Int32) -> Int32:\n"
            "    t = 0\n"
            "    for a in args:\n        t += a\n"
            "    return t\n"
            "def use() -> Int32:\n    return f()\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is not None
        assert "return f(::tpy::varargs<const int32_t>());" in _cpp(src, thir=True)


class TestVarargPack:
    # `*args` call-site packs (THIRVarargPack / _gen_vararg_pack): value and
    # reference element arrays, the empty pack, and *expr unpacking.

    def test_value_element_pack(self):
        src = (
            "from tpy import Int32\n"
            "def f(*args: Int32) -> Int32:\n"
            "    t = 0\n"
            "    for a in args:\n        t += a\n"
            "    return t\n"
            "def use() -> None:\n    print(f(1, 2, 3))\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        cpp = _cpp(src, thir=True)
        assert "std::array<const int32_t, 3> __tmp_1{1, 2, 3};" in cpp
        assert "f(::tpy::varargs<const int32_t>(__tmp_1))" in cpp

    def test_ref_element_lvalue_pack(self):
        # Reference elements become `T*` in the array; lvalue names are
        # address-taken in place (`&a`), a mutable slot spells `varargs<T>`.
        src = (
            "from tpy import Int32\n"
            "class C:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32):\n        self.v = v\n"
            "def sink(*args: C) -> None:\n"
            "    for c in args:\n        c.v += 1\n"
            "def use() -> None:\n"
            "    a = C(0)\n    b = C(0)\n    sink(a, b)\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        cpp = _cpp(src, thir=True)
        assert "std::array<C*, 2> __tmp_1{&a, &b};" in cpp
        assert "sink(::tpy::varargs<C>(__tmp_1))" in cpp

    def test_star_unpack_list_indirect(self):
        # `*list` into a readonly slot borrows a const span (indirect mode).
        src = (
            "from tpy import Int32\n"
            "def f(*args: Int32) -> Int32:\n"
            "    t = 0\n"
            "    for a in args:\n        t += a\n"
            "    return t\n"
            "def use() -> None:\n"
            "    xs: list[Int32] = [1, 2, 3]\n    print(f(*xs))\n"
        )
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert ("f(::tpy::varargs<const int32_t>(::tpy::as_span(xs)))"
                in _cpp(src, thir=True))
