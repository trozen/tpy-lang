"""The call-arg cascade rows: record names as free-function call args,
user-record method calls on bare-name receivers, and the arg-temp rows
(THIRArgTemp) -- routed emits plus the gate rejects that must never route."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import (
    THIRArgTemp, THIRCall, THIRCtorCall, THIRExprStmt, THIRMethodCall,
    THIRName, THIRReturn,
)
from .testutil import _compile, _entry, _lower, _lower_ctx, _fn

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

    def test_own_record_slot_stays_ast(self):
        # An Own[A] slot auto-moves its arg at last use (`sink(std::move(a))`).
        thir = _lower_ctx(
            _PRELUDE
            + "def sink(a: Own[A]) -> Int32:\n    return a.x\n"
            + "def use(a: Own[A]) -> Int32:\n    return sink(a)\n")
        assert _fn(thir, "use") is None

    def test_readonly_record_slot_stays_ast(self):
        # A readonly-wrapped slot is the deep-const frontier -- deferred.
        thir = _lower_ctx(
            _PRELUDE
            + "def take_ro(a: readonly[A]) -> Int32:\n    return a.x\n"
            + "def use(a: A) -> Int32:\n    return take_ro(a)\n")
        assert _fn(thir, "use") is None

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

    def test_optional_record_slot_stays_ast(self):
        # An `A | None` slot takes the `_gen_optional_ptr_arg` lift (`&(a)`).
        thir = _lower_ctx(
            _PRELUDE
            + "def maybe(a: A | None) -> Int32:\n"
            + "    if a is not None:\n        return a.x\n"
            + "    return 0\n"
            + "def use(a: A) -> Int32:\n    return maybe(a)\n")
        assert _fn(thir, "use") is None

    def test_union_record_slot_routes_via_member_lift(self):
        # An `A | B` slot lifts the member arg into the variant -- routed by
        # the member lift (THIRUnionArgLift pins the render in
        # test_thir_unions.py); here we only assert the body routes.
        thir = _lower_ctx(
            _PRELUDE
            + "def take_u(v: A | B) -> Int32:\n    return 0\n"
            + "def use(a: A) -> Int32:\n    return take_u(a)\n")
        assert _fn(thir, "use") is not None

    def test_self_as_arg_stays_ast(self):
        # `take_rec(self)` renders `take_rec((*this))` -- rejected.
        thir = _lower_ctx(_src(
            "", extra_a="    def through(self) -> Int32:\n"
                        "        return take_rec(self)\n"))
        assert _fn(thir, "through") is None


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

    def test_self_receiver_stays_ast(self):
        # `self.get()` renders `this->get()` -- the self-receiver emit is a
        # different row (rejected here).
        thir = _lower_ctx(_src(
            "", extra_a="    def twice(self) -> Int32:\n"
                        "        return self.get() * 2\n"))
        assert _fn(thir, "twice") is None

    def test_multi_overload_method_stays_ast(self):
        # @auto_readonly clones a method into a same-name mutable + const
        # overload pair; multi-overload sets are rejected wholesale.
        thir = _lower_ctx(
            "from tpy import Int32, auto_readonly\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32):\n        self.x = x\n"
            "    @auto_readonly\n"
            "    def get_x(self) -> Int32:\n        return self.x\n"
            "def use(p: P) -> Int32:\n    return p.get_x()\n")
        assert _fn(thir, "use") is None

    def test_own_scalar_method_slot_stays_ast(self):
        # A user method is not an inline template: gen_call_arg copies an
        # Own[scalar] arg into a temp and moves it -- rejected, unlike the
        # builtin-container Own[scalar] slots.
        thir = _lower_ctx(_src(
            "def use(a: A, n: Int32) -> Int32:\n    return a.own_scalar(n)\n",
            extra_a="    def own_scalar(self, v: Own[Int32]) -> Int32:\n"
                    "        return self.x + v\n"))
        assert _fn(thir, "use") is None

    def test_record_returning_method_stays_ast(self):
        # A record result is outside the admitted value set (scalar / Char /
        # str / void-stmt).
        thir = _lower_ctx(_src(
            "def use(a: A, b: A) -> Int32:\n    return a.pick(b).x\n",
            extra_a="    def pick(self, other: A) -> A:\n        return other\n"))
        assert _fn(thir, "use") is None


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


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
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
                "        take_vu(__tmp_1);") in out
        from .testutil import _lower_ctor
        assert _lower_ctor(src, "R") is not None

    def test_dump_is_number_free(self):
        from .dump import dump_thir
        thir = _lower(_VU_PRELUDE
                      + "def f(k: Int32) -> Int32:\n    return take_vu(k)\n")
        text = dump_thir(thir)
        assert "%argtmp(std::variant<int32_t, double>){%k}" in text
        assert "__tmp" not in text


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
        # A temp-needing call nested under a print arg or a binop operand is
        # not the direct statement value -- temps never propagate inward.
        thir = _lower(_VU_PRELUDE
                      + "def f(k: Int32):\n    print(take_vu(k))\n"
                      + "def g(k: Int32) -> Int32:\n"
                      + "    return take_vu(k) + 1\n")
        assert _fn(thir, "f") is None
        assert _fn(thir, "g") is None

    def test_scalar_field_write_value_stays_ast(self):
        # A field-write assign is not one of the four flushable positions
        # (deferred, not unsafe -- the AST flushes there too).
        thir = _lower_ctx(_PRELUDE
                          + "from tpy import Float64\n"
                          + "def take_vu(v: Int32 | Float64) -> Int32:\n"
                          + "    return 0\n"
                          + "def f(a: A, k: Int32):\n    a.x = take_vu(k)\n")
        assert _fn(thir, "f") is None

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

    def test_readonly_record_slot_ctor_stays_ast(self):
        # A readonly slot's temp declares `const A` -- deferred with the
        # deep-const frontier.
        thir = _lower_ctx(_PRELUDE
                          + "def take_ro(a: readonly[A]) -> Int32:\n"
                          + "    return a.x\n"
                          + "def f() -> Int32:\n    return take_ro(A(3))\n")
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


class TestCtorShapeGateRejects:
    """Reject arms of the record-ctor shape core (`_ctor_shape_ok`) and the
    scalar-slot arg loop (`_record_ctor_call_eligible`), pinned through the
    record-rvalue arg-temp row (`take_x(X(...))` in return position). Arms
    NOT expressible in this single-module harness: the same-name free-fn
    collision (sema resolves the call to the free fn, so a record-slot
    program is a sema type error before the gate is consulted) and the
    imported-name / cross-module-qualification arms (need a second module;
    also pre-rejected by `_f1_record` on the slot)."""

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

    def test_omitted_default_arity_stays_ast(self):
        # An omitted default is synthesized by the AST arg emit, which the
        # bare THIRCtorCall does not do; the full-arity call still routes.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class R:\n    x: Int32\n"
            "    def __init__(self, x: Int32 = 0):\n        self.x = x\n"
            "def take_r(r: R) -> Int32:\n    return r.x\n"
            "def use() -> Int32:\n    return take_r(R())\n"
            "def use_full() -> Int32:\n    return take_r(R(5))\n")
        assert _fn(thir, "use") is None
        assert _fn(thir, "use_full") is not None

    def test_generic_record_ctor_stays_ast(self):
        # A generic ctor spells substituted type args on the AST path.
        thir = _lower_ctx(
            "from tpy import Int32, ValueType\n"
            "class G[T: ValueType]:\n    v: T\n"
            "    def __init__(self, v: T):\n        self.v = v\n"
            "def take_g(g: G[Int32]) -> Int32:\n    return g.v\n"
            "def use() -> Int32:\n    return take_g(G(5))\n")
        assert _fn(thir, "use") is None

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
        # A @native record ctor renders the native C++ name, not the raw
        # source name (also pre-rejected at the slot by `_f1_record`).
        thir = _lower_ctx(
            "from tpy.extern import native\n"
            "from tpy import Int32\n"
            "@native\n"
            "class NR:\n    x: Int32\n"
            "    def __init__(self, x: Int32): ...\n"
            "def take_nr(r: NR) -> Int32:\n    return r.x\n"
            "def use() -> Int32:\n    return take_nr(NR(5))\n")
        assert _fn(thir, "use") is None

    def test_own_scalar_ctor_param_stays_ast(self):
        # An Own[scalar] __init__ slot copy+moves a NAME arg through a temp.
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            "class O:\n    x: Int32\n"
            "    def __init__(self, x: Own[Int32]):\n        self.x = x\n"
            "def take_o(o: O) -> Int32:\n    return o.x\n"
            "def use(k: Int32) -> Int32:\n    return take_o(O(k))\n")
        assert _fn(thir, "use") is None

    def test_union_ctor_param_stays_ast(self):
        # A member-valued scalar into a union-typed __init__ slot hoists a
        # variant temp on the AST path (`_member_valued_union_slot`).
        thir = _lower_ctx(
            "from tpy import Int32, Float64\n"
            "class W:\n    u: Int32 | Float64\n"
            "    def __init__(self, v: Int32 | Float64):\n        self.u = v\n"
            "def take_w(w: W) -> Int32:\n    return 0\n"
            "def use(k: Int32) -> Int32:\n    return take_w(W(k))\n")
        assert _fn(thir, "use") is None


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
                           match="outside a free-call arg position"):
            validate_function(fn)
