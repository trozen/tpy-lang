"""THIR F3 tuple rungs (tuple_to_pointer / tuple_to_storage) + tuple
subscript reads/writes."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..codegen_cpp.forms import LocalBinding
from .dump import dump_thir
from .nodes import (
    Form, THIRAssign, THIRBinOp, THIRFieldAccess, THIRFormConvert, THIRName,
    THIRReturn, THIRSubscript, THIRTupleLiteral, THIRTupleMembership,
    THIRTupleUnpack, THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn, _lower_ctor, _ctor_tail,
    _lower_ctx_witnessed, _PRELUDE,
)

# --- F3 form rung: storage->borrow tuple read (tuple_to_pointer) ---

# A record with a pointer-repr tuple field: storage form `std::tuple<int32_t,
# Leaf>`, borrow form `std::tuple<int32_t, Leaf*>`.
_F3_RECORDS = (
    "from tpy import Int32, readonly\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "class Holder:\n"
    "    pair: tuple[Int32, Leaf]\n"
    "    def __init__(self, b: Leaf):\n        self.pair = (1, b)\n"
)


class TestF3TupleReturn:
    def test_borrow_tuple_return_routes(self):
        # `return h.pair` lifts the storage tuple field into the borrow-form tuple
        # return via a STORAGE->BORROW convert (the tuple_to_pointer family).
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_field(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n")
        fn = _fn(thir, "ret_field")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRFormConvert) and ret.value.form is Form.BORROW
        assert ret.value.is_const is False  # mutable receiver -> mutable Leaf* elements
        assert ret.value.value.form is Form.STORAGE  # the h.pair storage read

    def test_value_tuple_return_takes_literal_not_lift(self):
        # An all-value-scalar tuple has no pointer-repr element (borrow ==
        # storage), so no tuple_to_pointer lift applies -- it routes via the
        # value-tuple rung's spelled literal render instead.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_pair(h: Holder) -> tuple[Int32, Int32]:\n    return (1, 2)\n")
        ret = _fn(thir, "ret_pair").body[0]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRTupleLiteral)
        assert not isinstance(ret.value, THIRFormConvert)

    def test_tuple_field_init_ctor_routes_mil_literal(self):
        # `Holder.__init__` does `self.pair = (1, b)` -- a leading own-field
        # init of an F3+ tuple the AST hoists into the member-init-list; the
        # mil.ptr_tuple_literal arm mirrors it (`pair(::tpy::tuple_to_storage<
        # S>(S{1, b}))`), matching the hoist rather than demoting to the body.
        ctor = _lower_ctor(_F3_RECORDS, "Holder")
        assert ctor is not None
        assert "::tpy::tuple_to_storage<std::tuple<int32_t, Leaf>>(" \
            "std::tuple<int32_t, Leaf>{1, b})" in _ctor_tail(ctor)

    def test_storage_tuple_alias_local_routes(self):
        # A storage-tuple alias local (`t = h.pair`) binds `auto&&` (a STORAGE-form
        # alias) and a `return t` lifts it via tuple_to_pointer like a direct field
        # source.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def ret_alias(h: Holder) -> tuple[Int32, Leaf]:\n"
            + "    t = h.pair\n    return t\n")
        fn = _fn(thir, "ret_alias")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.STORAGE_TUPLE_ALIAS
        assert decl.form is Form.STORAGE
        ret = fn.body[1]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.BORROW
        assert ret.value.value.form is Form.STORAGE  # the `t` alias read



class TestF3TupleReturnEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_RECORDS
        + "def ret_field(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n"
        + "def main():\n    h = Holder(Leaf(5))\n    t = ret_field(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_f3_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_tuple_to_pointer(self):
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, Leaf*>>(h.pair);"
                in self._cpp(self.SRC, thir=True))

    ALIAS_SRC = (
        _F3_RECORDS
        + "def ret_alias(h: Holder) -> tuple[Int32, Leaf]:\n"
        + "    t = h.pair\n    return t\n"
        + "def main():\n    h = Holder(Leaf(5))\n    t = ret_alias(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_alias_byte_identical(self):
        assert self._cpp(self.ALIAS_SRC, thir=True) == self._cpp(self.ALIAS_SRC, thir=False)

    def test_alias_emits_auto_ref(self):
        cpp = self._cpp(self.ALIAS_SRC, thir=True)
        assert "auto&& t = h.pair;" in cpp
        assert "return ::tpy::tuple_to_pointer<std::tuple<int32_t, Leaf*>>(t);" in cpp

    # A const (readonly) receiver makes the borrow tuple's element pointers const,
    # exercising the `to_cpp_return_const()` arm of the tuple_to_pointer lift -- for
    # both a direct field return and a storage-tuple alias local.
    CONST_SRC = (
        _F3_RECORDS
        + "def f(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
        + "    return h.pair\n"
        + "def g(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
        + "    t = h.pair\n    return t\n"
        + "def main():\n    h = Holder(Leaf(5))\n    a = f(h)\n    b = g(h)\n    print(0)\n"
        + "main()\n"
    )

    def test_const_byte_identical(self):
        assert self._cpp(self.CONST_SRC, thir=True) == self._cpp(self.CONST_SRC, thir=False)

    def test_const_receiver_emits_const_tuple_to_pointer(self):
        cpp = self._cpp(self.CONST_SRC, thir=True)
        # direct field return + alias local both lift with const element pointers.
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, const Leaf*>>(h.pair);"
                in cpp)
        assert ("return ::tpy::tuple_to_pointer<std::tuple<int32_t, const Leaf*>>(t);"
                in cpp)

    def test_const_receiver_lowers_const_convert(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(h: readonly[Holder]) -> tuple[Int32, readonly[Leaf]]:\n"
            + "    return h.pair\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFormConvert)
        assert ret.value.is_const is True



# --- F3 form rung: borrow->storage tuple field write (tuple_to_storage) ---

# Records with a pointer-repr Optional-element tuple field: storage form
# `std::tuple<std::optional<T>, ...>`, borrow form `std::tuple<T*, ...>`.
_F3_OPT_RECORDS = (
    "from tpy import Int32\n"
    "class T:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
    "class Holder:\n"
    "    pair: tuple[T | None, T | None]\n"
    "    def __init__(self) -> None:\n        self.pair = (None, None)\n"
)


class TestF3TupleFieldWrite:
    def test_borrow_tuple_field_write_routes(self):
        # `self.pair = p` where p is a borrow tuple param lifts borrow->storage via
        # a STORAGE-form convert (the tuple_to_storage family); the receiver becomes
        # a written (non-const) self.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "class Setter:\n"
            + "    pair: tuple[T | None, T | None]\n"
            + "    def __init__(self) -> None:\n        self.pair = (None, None)\n"
            + "    def update(self, p: tuple[T | None, T | None]) -> None:\n"
            + "        self.pair = p\n")
        fn = _fn(thir, "update")
        assert fn is not None
        write = fn.body[0]
        assert isinstance(write, THIRAssign)
        assert isinstance(write.target, THIRFieldAccess) and write.target.field_cpp == "pair"
        assert isinstance(write.value, THIRFormConvert)
        assert write.value.form is Form.STORAGE and write.value.move is False
        assert write.value.value.form is Form.BORROW  # the borrow tuple param p

    def test_storage_source_field_write_is_ineligible(self):
        # A storage-form source (`other.pair`, a field read) is a direct copy with
        # no tuple_to_storage wrap -- a later F3 cell, so it stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def copy_from(h: Holder, other: Holder) -> None:\n"
            + "    h.pair = other.pair\n")
        assert _fn(thir, "copy_from") is None



class TestF3TupleFieldWriteEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_OPT_RECORDS
        + "def upd(h: Holder, p: tuple[T | None, T | None]) -> None:\n"
        + "    h.pair = p\n"
        + "def main():\n    h = Holder()\n    t = T(1)\n    upd(h, (t, None))\n    print(0)\n"
        + "main()\n"
    )

    def test_f3_write_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_tuple_to_storage(self):
        assert ("h.pair = ::tpy::tuple_to_storage<std::tuple<std::optional<T>, "
                "std::optional<T>>>(p);" in self._cpp(self.SRC, thir=True))



# --- Statement-shape axis: value-result tuple subscript reads (std::get<N>) ---

# The first cell of the statement-shape axis. A value-scalar tuple param
# (`const std::tuple<...>&`, its signature emitted by the AST path) read by
# subscript routes its body; the value-scalar slot of an already-routed
# pointer-repr tuple reads the same way. Borrow-result (record / Optional) element
# reads stay on the AST path.
class TestTupleSubscriptRead:
    def test_value_tuple_param_subscript_routes(self):
        # `tuple[Int32, Int32]` is admitted as a param; `p[0]` / `p[1]` lower to
        # value-form THIRSubscript reads off the param name.
        thir = _lower(
            _PRELUDE
            + "def consume(p: tuple[Int32, Int32]) -> Int32:\n    return p[0] + p[1]\n")
        fn = _fn(thir, "consume")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRBinOp)
        left = ret.value.left
        assert isinstance(left, THIRSubscript) and left.index.value == 0
        assert left.form is Form.VALUE
        assert isinstance(left.receiver, THIRName) and left.receiver.name == "p"
        assert ret.value.right.index.value == 1

    def test_negative_index_normalized(self):
        # `p[-3]` on a 3-tuple folds to index 0; `p[-1]` to index 2 -- the AST's
        # _extract_compile_time_index normalization.
        thir = _lower(
            _PRELUDE
            + "def f(p: tuple[Int32, Int32, Int32]) -> Int32:\n    return p[-3] + p[-1]\n")
        ret = _fn(thir, "f").body[0]
        assert ret.value.left.index.value == 0
        assert ret.value.right.index.value == 2

    def test_value_scalar_slot_of_pointer_repr_tuple_routes(self):
        # The Int32 slot of a pointer-repr tuple `tuple[Int32, Leaf]` reads as a
        # plain std::get (value form), reusing the already-routed borrow receiver.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def scalar_slot(t: tuple[Int32, Leaf]) -> Int32:\n    return t[0]\n")
        fn = _fn(thir, "scalar_slot")
        assert fn is not None
        sub = fn.body[0].value
        assert isinstance(sub, THIRSubscript) and sub.index.value == 0
        assert sub.form is Form.VALUE

    def test_value_scalar_tuple_local_routes(self):
        # A value-tuple literal local (`t = (1, 2)`) routes via the tuple-rung
        # decl arm: the spelled `std::tuple<...>{1, 2}` init, the local
        # entering `declared` so the subscript read lights up.
        thir = _lower(
            _PRELUDE
            + "def f() -> Int32:\n    t = (1, 2)\n    return t[0]\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0].init, THIRTupleLiteral)

    def test_value_tuple_name_copy_local_routes(self):
        # A value-tuple NAME-copy local (`u = t`) is a plain spelled copy
        # (`std::tuple<...> u = t;`) -- borrow and storage coincide, so no
        # lift arises on either path.
        thir = _lower(
            _PRELUDE
            + "def f(t: tuple[Int32, Int32]) -> Int32:\n"
            + "    u = t\n"
            + "    return u[0]\n")
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[0]
        assert isinstance(decl, THIRVarDecl) and isinstance(decl.init, THIRName)

    def test_value_tuple_method_call_local_routes(self):
        # A value-tuple METHOD-call local (`t = h.pair()`) rides the
        # storage-call decl arm like the free-call form.
        thir = _lower_ctx(
            _PRELUDE
            + "class H:\n"
            + "    def pair(self) -> tuple[Int32, Int32]:\n"
            + "        return (1, 2)\n"
            + "def f(h: H) -> Int32:\n"
            + "    t = h.pair()\n"
            + "    return t[0]\n")
        assert _fn(thir, "f") is not None

    def test_tuple_locals_byte_identical(self):
        src = (
            _PRELUDE
            + "class H:\n"
            + "    def pair(self) -> tuple[Int32, Int32]:\n"
            + "        return (1, 2)\n"
            + "def f(t: tuple[Int32, Int32]) -> Int32:\n"
            + "    u = t\n"
            + "    return u[0]\n"
            + "def g(h: H) -> Int32:\n"
            + "    t = h.pair()\n"
            + "    return t[1]\n"
            + "def main() -> None:\n"
            + "    print(f((3, 4)), g(H()))\n"
            + "main()\n")
        compiler, modules = _compile(src)
        entry = _entry(modules)

        def cpp(thir: bool):
            _, out = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir))
            return out

        thir_cpp = cpp(True)
        assert thir_cpp == cpp(False)
        assert "std::tuple<int32_t, int32_t> u = t;" in thir_cpp
        assert "std::tuple<int32_t, int32_t> t = h.pair();" in thir_cpp



class TestTupleSubscriptReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def consume(p: tuple[Int32, Int32]) -> Int32:\n    return p[0] + p[1]\n"
        + "def main():\n    print(consume((1, 2)))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_std_get(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<0>(p)" in cpp and "std::get<1>(p)" in cpp


class TestTupleSubscriptCallReceiver:
    """A value-tuple-returning CALL receiver (`pair(n)[1]` / `s.name()[1]`,
    the `getsockname()[1]` shape): the read renders `std::get<N>(<call>)`
    with the call emitted in place; the call itself still gates in
    _lower_expr, so a non-routable call rejects the body there (safe
    fallback, never a mis-render)."""

    def _gen(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return compiler, cpp

    def _identical(self, src: str):
        _, cpp_ast = self._gen(src, thir=False)
        c, cpp_thir = self._gen(src, thir=True)
        assert cpp_ast == cpp_thir
        return c, cpp_thir

    def test_free_call_receiver_routes(self):
        src = (_PRELUDE
               + "def pair(n: Int32) -> tuple[Int32, Int32]:\n"
               + "    return (n, n + 1)\n"
               + "def f(n: Int32) -> Int32:\n    return pair(n)[1]\n"
               + "def main():\n    print(f(1))\nmain()\n")
        c, cpp = self._identical(src)
        assert "std::get<1>(pair(n))" in cpp
        assert not any(k.startswith("body:") for k in c._thir_fallback)

    def test_method_call_receiver_routes(self):
        # The record-method flavor also exercises the widened method
        # return gate (a value-tuple result emits the bare member call).
        src = (_PRELUDE
               + "class Sock:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32):\n        self.n = n\n"
               + "    def name(self) -> tuple[str, Int32]:\n"
               + "        return (\"x\", self.n)\n"
               + "def f(s: Sock) -> Int32:\n    return s.name()[1]\n"
               + "def main():\n    print(f(Sock(7)))\nmain()\n")
        c, cpp = self._identical(src)
        assert "std::get<1>(s.name())" in cpp

    def test_decl_sink_routes(self):
        # The widened method return at a DECL sink: the value-tuple local
        # decl arm consumes the bare call init, then the subscript reads
        # off the registered local.
        src = (_PRELUDE
               + "class Sock:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32):\n        self.n = n\n"
               + "    def name(self) -> tuple[str, Int32]:\n"
               + "        return (\"x\", self.n)\n"
               + "def f(s: Sock) -> Int32:\n"
               + "    t = s.name()\n"
               + "    return t[1]\n"
               + "def main():\n    print(f(Sock(7)))\nmain()\n")
        c, _cpp = self._identical(src)
        assert not any(k.startswith("body:") for k in c._thir_fallback)

    def test_arg_sink_routes(self):
        # The widened method return at an ARG sink: the value-tuple
        # pass-through arg row consumes the call result.
        src = (_PRELUDE
               + "class Sock:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32):\n        self.n = n\n"
               + "    def name(self) -> tuple[str, Int32]:\n"
               + "        return (\"x\", self.n)\n"
               + "def g(t: tuple[str, Int32]) -> Int32:\n    return t[1]\n"
               + "def f(s: Sock) -> Int32:\n    return g(s.name())\n"
               + "def main():\n    print(f(Sock(7)))\nmain()\n")
        c, _cpp = self._identical(src)
        assert not any(k.startswith("body:") for k in c._thir_fallback)

    def test_nonvalue_element_call_receiver_defers(self):
        # A pointer-repr-element result keeps the tuple outside
        # _value_tuple_nested -- the receiver stays unadmitted and f's
        # body falls back at the subscript (the composed reject key
        # isolates the intended construct; make's own borrow-tuple
        # return falls back separately as bare stmt.return).
        src = (_F3_RECORDS
               + "def make(b: Leaf) -> tuple[Int32, Leaf]:\n"
               + "    return (1, b)\n"
               + "def f(b: Leaf) -> Int32:\n    return make(b)[0]\n"
               + "def main():\n    f(Leaf(1))\nmain()\n")
        c, _cpp = self._identical(src)
        assert "body:stmt.return:subscript.tuple_shape" in c._thir_fallback


# --- Nested value-tuple subscript reads: `t[i]` yielding a whole (recursively
# value) tuple, and the chained `t[i][j]` off that inner tuple read. Both stay
# bare value reads (`std::get<j>(std::get<i>(t))`), printed via TuplePrinter. ---
class TestNestedTupleSubscript:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def main() -> None:\n"
        + "    t = ((1, 2), (3, 4))\n"
        + "    print(t[0])\n"
        + "    inner = t[1]\n"
        + "    print(inner[0])\n"
        + "    print(t[1][1])\n"
        + "main()\n"
    )

    def test_nested_element_read_routes_value_form(self):
        # `t[1]` reads a whole nested value tuple (VALUE form, no lift); the
        # decl-init local `inner` enters `declared` so `inner[0]` lights up.
        thir = _lower(self.SRC)
        fn = _fn(thir, "main")
        assert fn is not None
        decl = next(s for s in fn.body
                    if isinstance(s, THIRVarDecl) and s.name == "inner")
        assert isinstance(decl.init, THIRSubscript)
        assert decl.init.index.value == 1 and decl.init.form is Form.VALUE

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_chained_and_tupleprinter(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<1>(std::get<1>(t))" in cpp
        assert "::tpy::TuplePrinter(std::get<0>(t))" in cpp


# --- Tuple-literal membership: `x in (a, b, ...)` / `not in` -> the `==`
# OR-chain, with the `__in_lhs` statement-expression temp for a non-trivial
# needle. A dict/set `in` keeps the `.contains` arm. ---
class TestTupleLiteralMembership:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_scalar_membership_routes_node(self):
        thir = _lower(
            _PRELUDE
            + "def f(x: Int32) -> bool:\n    return x in (1, 17, 42)\n")
        ret = _fn(thir, "f").body[0]
        assert isinstance(ret, THIRReturn)
        m = ret.value
        assert isinstance(m, THIRTupleMembership)
        assert len(m.elements) == 3 and not m.negate and not m.need_temp

    def test_not_in_single_element_negates(self):
        thir = _lower(
            _PRELUDE
            + "def f(x: Int32) -> bool:\n    return x not in (3,)\n")
        m = _fn(thir, "f").body[0].value
        assert isinstance(m, THIRTupleMembership)
        assert m.negate and len(m.elements) == 1

    def test_call_needle_needs_temp(self):
        thir = _lower(
            _PRELUDE
            + "def g() -> Int32:\n    return 17\n"
            + "def f() -> bool:\n    return g() in (1, 17, 42)\n")
        m = _fn(thir, "f").body[0].value
        assert isinstance(m, THIRTupleMembership) and m.need_temp

    SRC = (
        _PRELUDE
        + "def get_val() -> Int32:\n    return 17\n"
        + "def main() -> None:\n"
        + "    x: Int32 = 17\n"
        + "    s = \"hi\"\n"
        + "    if x in (1, 17, 42):\n        print(\"a\")\n"
        + "    if x not in (1, 2, 3):\n        print(\"b\")\n"
        + "    if s in (\"hi\", \"yo\"):\n        print(\"c\")\n"
        + "    if get_val() in (1, 17):\n        print(\"d\")\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_or_chain_and_stmtexpr(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "(x == 1) || (x == 17) || (x == 42)" in cpp
        assert "(!((x == 1) || (x == 2) || (x == 3)))" in cpp
        assert 'auto&& __in_lhs = get_val();' in cpp


# The routing-heavy subscript cell: a record-element read `t[N].field`. The subscript
# yields a borrow -- `std::get<N>(t)->field` off a borrow-form tuple param, or
# `std::get<N>(t).field` off a storage `auto&&` alias. Optional-element member access
# (null-check path), standalone binds, and writes stay on the AST path.
class TestTupleSubscriptRecordRead:
    def test_record_element_via_borrow_param_routes(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def via_param(t: tuple[Int32, Leaf]) -> Int32:\n    return t[1].n\n")
        fn = _fn(thir, "via_param")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn) and isinstance(ret.value, THIRFieldAccess)
        assert ret.value.is_arrow  # borrow-tuple param -> std::get<1>(t) is a T*
        sub = ret.value.receiver
        assert isinstance(sub, THIRSubscript) and sub.index.value == 1
        assert sub.form is Form.BORROW

    def test_record_element_via_storage_alias_reads_dot(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def via_alias(h: Holder) -> Int32:\n    a = h.pair\n    return a[1].n\n")
        fn = _fn(thir, "via_alias")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret.value, THIRFieldAccess)
        assert ret.value.is_arrow is False  # storage auto&& alias -> T&, dot access
        assert isinstance(ret.value.receiver, THIRSubscript)

    def test_element_form_drives_arrow_vs_dot(self):
        # Element form, not the receiver alone, decides `->` vs `.`: an Own element is
        # held by value (`std::get<0>(p).n`, dot), a bare-reference element is a borrow
        # pointer (`std::get<1>(p)->n`, arrow). Regression for the mixed owned+borrow
        # tuple (_tuple_subscript_yields_borrow_ptr mirror).
        thir = _lower_ctx(
            "from tpy import Own, Int32\n"
            "class A:\n    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
            "def f(p: tuple[Own[A], A]) -> Int32:\n    return p[0].n + p[1].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        add = fn.body[0].value
        assert isinstance(add.left, THIRFieldAccess) and add.left.is_arrow is False
        assert isinstance(add.right, THIRFieldAccess) and add.right.is_arrow is True

    def test_negative_index_and_multi_element_record_read(self):
        # A negative index on a record element, and a record at index 2 of a 3-tuple:
        # both normalize to std::get<2> and arrow-decide correctly.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Int32, Leaf]) -> Int32:\n"
            + "    return t[-1].n + t[2].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        add = fn.body[0].value
        assert isinstance(add.left, THIRFieldAccess) and add.left.is_arrow
        assert isinstance(add.left.receiver, THIRSubscript) and add.left.receiver.index.value == 2
        assert add.right.receiver.index.value == 2

    def test_readonly_tuple_record_read_routes(self):
        # A readonly[tuple[...]] receiver still reads a record element via `->` (the
        # const is carried in the param type, not the access) -- byte-identical.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: readonly[tuple[Int32, Leaf]]) -> Int32:\n    return t[1].n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.is_arrow
        assert isinstance(ret.value.receiver, THIRSubscript)

    def test_standalone_record_element_read_is_ineligible(self):
        # `b = t[1]` binds a record borrow local from a subscript -- the borrow-local
        # binding source path keeps its name-receiver gate, so this stays on AST.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Leaf]) -> Int32:\n    b = t[1]\n    return b.n\n")
        assert _fn(thir, "f") is None



class TestTupleSubscriptRecordReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_RECORDS
        + "def via_param(t: tuple[Int32, Leaf]) -> Int32:\n    return t[1].n\n"
        + "def via_alias(h: Holder) -> Int32:\n    a = h.pair\n    return a[1].n\n"
        + "def main():\n    h = Holder(Leaf(5))\n"
        + "    print(via_param(h.pair) + via_alias(h))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_arrow_and_dot(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "return std::get<1>(t)->n;" in cpp   # borrow param
        assert "return std::get<1>(a).n;" in cpp     # storage alias



# The read frontier's tail: an unproven `Optional[record]`-element member access
# `t[N].field` -> `deref_check(...).field`. Off a borrow tuple the element is a nullable
# `T*`; off a storage alias it is `std::optional<T>` lifted to `T*` via optional_to_ptr.
class TestTupleSubscriptOptionalRead:
    def test_optional_element_via_borrow_param_routes(self):
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check
        assert ret.value.is_arrow is False  # deref_check reads `.` after the checked deref
        assert isinstance(ret.value.receiver, THIRSubscript)  # already a T*, no lift

    def test_optional_element_via_storage_alias_lifts(self):
        # Off a storage auto&& alias the element is std::optional<T>, lifted to T* via a
        # STORAGE->BORROW optional_to_ptr convert before the deref_check.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(h: Holder) -> Int32:\n    a = h.pair\n    return a[0].x\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[1]
        assert isinstance(ret.value, THIRFieldAccess) and ret.value.deref_check
        conv = ret.value.receiver
        assert isinstance(conv, THIRFormConvert) and conv.form is Form.BORROW
        assert isinstance(conv.value, THIRSubscript) and conv.value.form is Form.STORAGE

    def test_index_1_and_readonly_optional_route(self):
        # An Optional element at index 1, and a readonly[tuple] receiver, both route
        # (index normalization + the const deref_check/optional_to_ptr overloads are
        # shared, unmodified machinery).
        thir = _lower_ctx(
            "from tpy import readonly\n" + _F3_OPT_RECORDS
            + "def i1(t: tuple[T | None, T | None]) -> Int32:\n    return t[1].x\n"
            + "def ro(t: readonly[tuple[T | None, T | None]]) -> Int32:\n    return t[0].x\n")
        assert _fn(thir, "i1") is not None and _fn(thir, "ro") is not None
        assert _fn(thir, "i1").body[0].value.receiver.index.value == 1

    def test_optional_element_write_is_ineligible(self):
        # A write through an Optional-element subscript (`t[0].x = 5`) keeps the
        # name-receiver gate on the write path -> stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> None:\n    t[0].x = 5\n")
        assert _fn(thir, "f") is None

    def test_optional_element_bind_is_ineligible(self):
        # A standalone bind of an Optional element (`e = t[0]`) is a borrow-local
        # binding source, which keeps the name-receiver gate -> stays on the AST path.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    e = t[0]\n    return e.x\n")
        assert _fn(thir, "f") is None

    def test_dump_renders_deref_check(self):
        # The --dump-thir rendering of the runtime-null-checked member access (the one
        # test exercising dump.py's deref_check branch).
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def f(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n")
        assert "deref_check(%t[0] [borrow]).x" in dump_thir(thir)



class TestTupleSubscriptOptionalReadEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _F3_OPT_RECORDS
        + "def viap(t: tuple[T | None, T | None]) -> Int32:\n    return t[0].x\n"
        + "def viaa(h: Holder) -> Int32:\n    a = h.pair\n    return a[0].x\n"
        + "def main():\n    h = Holder()\n    print(0)\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_deref_check(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "::tpy::deref_check(std::get<0>(t)).x" in cpp
        assert "::tpy::deref_check(::tpy::optional_to_ptr(std::get<0>(a))).x" in cpp



# The write position that closes the tuple-subscript family: a scalar field write to a
# record element, `t[N].field = <scalar>` (and `+= <scalar>`) -> `std::get<N>(t)->field
# = ...`. The target renders the same as the record-element read; only the write/aug-write
# eligibility gates are extended to the subscript target.
class TestTupleSubscriptWrite:
    def test_record_element_field_write_routes(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n = 5\n")
        fn = _fn(thir, "w")
        assert fn is not None
        st = fn.body[0]
        assert isinstance(st, THIRAssign)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow
        assert isinstance(st.target.receiver, THIRSubscript)

    def test_record_element_field_aug_write_routes(self):
        # `t[N].field += y` lowers to `target = (target OP value)`; the subscript target
        # renders identically on both sides.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n += 3\n")
        st = _fn(thir, "w").body[0]
        assert isinstance(st, THIRAssign) and isinstance(st.value, THIRBinOp)
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow

    def test_write_via_storage_alias_dots(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def w(h: Holder) -> None:\n    a = h.pair\n    a[1].n = 9\n")
        st = _fn(thir, "w").body[1]
        assert isinstance(st.target, THIRFieldAccess) and st.target.is_arrow is False

    def test_optional_element_write_still_ineligible(self):
        # Extending the scalar-field-write gate to subscript targets must NOT admit an
        # Optional-element write (needs a null-checked write) -- markers reject it.
        thir = _lower_ctx(
            _F3_OPT_RECORDS
            + "def w(t: tuple[T | None, T | None]) -> None:\n    t[0].x = 5\n")
        assert _fn(thir, "w") is None



class TestTupleSubscriptWriteEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    # Both target forms are byte-diffed and emit-checked: the borrow-param arrow write
    # (`w`) and the storage-alias dot write (`wa`), each with a plain and a `+=` variant.
    SRC = (
        "from tpy import Int32\n"
        "class Leaf:\n    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
        "class Holder:\n    pair: tuple[Int32, Leaf]\n"
        "    def __init__(self, b: Leaf) -> None:\n        self.pair = (1, b)\n"
        "def w(t: tuple[Int32, Leaf]) -> None:\n    t[1].n = 5\n    t[1].n += 3\n"
        "def wa(h: Holder) -> None:\n    a = h.pair\n    a[1].n = 9\n    a[1].n += 2\n"
        "def main():\n    leaf = Leaf(1)\n    w((5, leaf))\n    h = Holder(leaf)\n"
        "    wa(h)\n    print(leaf.n)\n"
        "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_writes(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::get<1>(t)->n = 5;" in cpp                      # borrow param -> arrow
        assert ("std::get<1>(t)->n = ::tpy::add_check<int32_t>(std::get<1>(t)->n, 3);"
                in cpp)
        assert "std::get<1>(a).n = 9;" in cpp                       # storage alias -> dot
        assert ("std::get<1>(a).n = ::tpy::add_check<int32_t>(std::get<1>(a).n, 2);"
                in cpp)


# --- Value-tuple slots: spelled literal renders + returns (the tuple rung) ---


class TestValueTupleSlots:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    def test_literal_return_routes(self):
        thir = _lower(
            _PRELUDE
            + "def two(a: Int32, b: Int32) -> tuple[Int32, Int32]:\n"
            + "    return (a, b)\n")
        fn = _fn(thir, "two")
        assert fn is not None
        assert isinstance(fn.body[0].value, THIRTupleLiteral)

    def test_bare_name_return_routes(self):
        thir = _lower(
            _PRELUDE
            + "def echo(t: tuple[Int32, Int32]) -> tuple[Int32, Int32]:\n"
            + "    return t\n")
        fn = _fn(thir, "echo")
        assert fn is not None
        assert isinstance(fn.body[0].value, THIRName)

    def test_str_element_param_and_return(self):
        # tuple[str, Int32]: the str element reads as an owned lvalue (bare in
        # every sink); a view-form element source wraps std::string(s).
        thir = _lower(
            _PRELUDE
            + "def pick(t: tuple[str, Int32]) -> str:\n    return t[0]\n"
            + "def make(s: str, n: Int32) -> tuple[str, Int32]:\n"
            + "    return (s, n)\n")
        assert _fn(thir, "pick") is not None
        assert _fn(thir, "make") is not None

    def test_view_element_tuple_ineligible(self):
        # A StrView element keeps the tuple outside the value family (the
        # literal render pins static storage for view slots).
        thir = _lower(
            _PRELUDE
            + "from tpy import StrView\n"
            + "def f(t: tuple[StrView, Int32]) -> Int32:\n    return t[1]\n")
        assert _fn(thir, "f") is None

    def test_nested_value_tuple_scalar_element_read_routes(self):
        # Reading a SCALAR element (`t[0]`) off a nested value-tuple receiver is
        # a plain value read (`std::get<0>(t)`); the receiver being nested no
        # longer keeps the read on AST.
        src = (
            _PRELUDE
            + "def f(t: tuple[Int32, tuple[Int32, Int32]]) -> Int32:\n"
            + "    return t[0]\n"
            + "def main():\n    print(f((1, (2, 3))))\n"
            + "main()\n")
        thir = _lower(src)
        sub = _fn(thir, "f").body[0].value
        assert isinstance(sub, THIRSubscript)
        assert sub.index.value == 0 and sub.form is Form.VALUE
        assert self._cpp(src, thir=True) == self._cpp(src, thir=False)

    def test_byte_identical(self):
        src = (
            _PRELUDE
            + "from tpy import Float32\n"
            + "def two(a: Int32, b: Int32) -> tuple[Int32, Int32]:\n"
            + "    return (a, b)\n"
            + "def big() -> tuple[int, int]:\n    return (1, 2)\n"
            + "def f32() -> tuple[Float32, Float32]:\n    return (1.5, 2.5)\n"
            + "def mix(s: str, n: Int32) -> tuple[str, Int32]:\n"
            + "    return (s, n)\n"
            + "def pick(t: tuple[str, Int32]) -> str:\n    return t[0]\n"
            + "def echo(t: tuple[Int32, Int32]) -> tuple[Int32, Int32]:\n"
            + "    return t\n"
            + "def locals_() -> Int32:\n"
            + "    t = (1, 2)\n    t = (3, 4)\n    return t[0]\n"
            + "def main():\n"
            + "    print(two(1, 2)[1], big()[0], f32()[1], pick(mix('x', 3)),\n"
            + "          echo((8, 9))[0], locals_())\n"
            + "main()\n")
        thir_cpp = self._cpp(src, thir=True)
        assert thir_cpp == self._cpp(src, thir=False)
        assert "return t;" in thir_cpp                  # bare value-tuple name
        assert "return std::tuple<int32_t, int32_t>{a, b};" in thir_cpp
        assert ("return std::tuple<::tpy::BigInt, ::tpy::BigInt>"
                "{::tpy::BigInt(1), ::tpy::BigInt(2)};") in thir_cpp
        assert "return std::tuple<float, float>{1.5f, 2.5f};" in thir_cpp
        assert ("return std::tuple<std::string, int32_t>{std::string(s), n};"
                in thir_cpp)
        assert "t = std::tuple<int32_t, int32_t>{3, 4};" in thir_cpp

    def test_tuple_call_args_route(self):
        # A bare value-tuple name and a tuple literal both pass into a
        # value-tuple param slot (the shared pass-through row -- gen_call_arg
        # renders the bare name / the spelled brace-init on both paths).
        src = (
            _PRELUDE
            + "def take(t: tuple[Int32, Int32]) -> Int32:\n"
            + "    return t[0] + t[1]\n"
            + "def pass_along(t: tuple[Int32, Int32]) -> Int32:\n"
            + "    return take(t)\n"
            + "def lit() -> Int32:\n    return take((3, 4))\n"
            + "def main():\n    print(pass_along((1, 2)), lit())\nmain()\n")
        thir = _lower(src)
        for name in ("take", "pass_along", "lit"):
            assert _fn(thir, name) is not None, name
        cpp = self._cpp(src, thir=True)
        assert cpp == self._cpp(src, thir=False)
        assert "return take(t);" in cpp
        assert "return take(std::tuple<int32_t, int32_t>{3, 4});" in cpp


# --- Value-tuple-returning calls at the decl-init / return sinks ---

# A value-tuple-returning free call renders bare in both storage sinks:
# `std::tuple<int32_t, int32_t> t = make(n);` at the decl (a plain
# `t = make(n);` on a reassign -- tuples are value types, no pointer-local
# machinery arises) and `return make(n);` at the tuple return slot.
class TestTupleCallSlots:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def make(n: Int32) -> tuple[Int32, Int32]:\n    return (n, n + 1)\n"
        + "def fwd(n: Int32) -> tuple[Int32, Int32]:\n    return make(n)\n"
        + "def use(n: Int32) -> Int32:\n"
        + "    t = make(n)\n"
        + "    t = make(n + 1)\n"
        + "    return t[0]\n"
        + "def main():\n    print(use(2))\nmain()\n"
    )

    def test_decl_reassign_and_return_route(self):
        from .testutil import _lower_ctx_witnessed
        thir, faces = _lower_ctx_witnessed(self.SRC)
        fn = _fn(thir, "use")
        assert fn is not None
        assert isinstance(fn.body[0], THIRVarDecl)      # first decl
        assert isinstance(fn.body[1], THIRAssign)       # plain value reassign
        assert _fn(thir, "fwd") is not None
        assert faces.get("decl.storage_call", 0) >= 1
        assert faces.get("ret.tuple_call", 0) == 1

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_bare_call(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "std::tuple<int32_t, int32_t> t = make(n);" in cpp
        assert "t = make((::tpy::add_check<int32_t>(n, 1)));" in cpp
        assert "return make(n);" in cpp

    def test_pointer_repr_tuple_call_ineligible(self):
        # A pointer-repr tuple (record element) return is outside the
        # value-tuple family -- decl init and return slot both reject.
        src = (
            _F3_RECORDS
            + "def pick(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n"
            + "def use(h: Holder) -> Int32:\n"
            + "    t = pick(h)\n"
            + "    return t[0]\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "use") is None


# --- Widened value-tuple RETURN elements: nested value-tuple / value-Optional ---

# The value-tuple return slot admits, beyond scalar / owned-str elements, a
# NESTED value-tuple element (spelled recursively), a value-`Optional[scalar]`
# element (`None`->`std::nullopt` / a scalar value bare), and a value-
# `Optional[str]` element (a view source wraps `std::string(view)`). Return-slot
# only: the param / decl / subscript-read / bare-name-return sinks stay on the
# narrow value-tuple family (no bare-copy read arm for a widened-element receiver).
class TestWidenedValueTupleReturn:
    def test_nested_tuple_literal_return_routes(self):
        thir = _lower(
            _PRELUDE
            + "def f(a: Int32, b: Int32, s: str) -> tuple[Int32, tuple[Int32, str]]:\n"
            + "    return (a, (b, s))\n")
        fn = _fn(thir, "f")
        assert fn is not None
        ret = fn.body[0]
        assert isinstance(ret, THIRReturn)
        outer = ret.value
        assert isinstance(outer, THIRTupleLiteral) and len(outer.elements) == 2
        assert isinstance(outer.elements[1], THIRTupleLiteral)  # nested spell

    def test_value_optional_scalar_element_routes(self):
        # A scalar value and a bare None both land in the Optional[scalar] slot.
        thir = _lower(
            _PRELUDE
            + "def val(a: Int32, b: Int32) -> tuple[Int32, Int32 | None]:\n"
            + "    return (a, b)\n"
            + "def none(a: Int32) -> tuple[Int32, Int32 | None]:\n"
            + "    return (a, None)\n")
        assert _fn(thir, "val") is not None
        assert _fn(thir, "none") is not None

    def test_deep_nesting_and_optional_inner_route(self):
        # Three-level nesting, and a nested tuple carrying a value-Optional inner:
        # both resolve their own slot recursively (the widened `_value_tuple_
        # return` at the recursive lowering site).
        thir = _lower(
            _PRELUDE
            + "def deep(a: Int32) -> tuple[Int32, tuple[Int32, tuple[Int32, str]]]:\n"
            + "    return (a, (a, (a, 'x')))\n"
            + "def nopt(a: Int32, b: Int32) -> tuple[Int32, tuple[Int32, Int32 | None]]:\n"
            + "    return (a, (b, None))\n")
        assert _fn(thir, "deep") is not None
        assert _fn(thir, "nopt") is not None

    def test_faces_witnessed(self):
        from .testutil import _lower_ctx_witnessed
        _thir, faces = _lower_ctx_witnessed(
            _PRELUDE
            + "def n(a: Int32, b: Int32) -> tuple[Int32, tuple[Int32, Int32]]:\n"
            + "    return (a, (b, b))\n"
            + "def o(a: Int32, b: Int32) -> tuple[Int32, Int32 | None]:\n"
            + "    return (a, b)\n")
        assert faces.get("ret.tuple_nested_elem", 0) >= 1
        assert faces.get("ret.tuple_opt_elem", 0) >= 1

    def test_nested_element_read_off_param_routes(self):
        # Reading the NESTED-tuple element (`t[1]`) off a nested value-tuple
        # param is a bare whole-tuple value read (`std::get<1>(t)`), copied into
        # the local -- byte-identical to AST.
        def _cpp(src, thir):
            compiler, modules = _compile(src)
            _, cpp = compiler.generate_code_to_strings(
                _entry(modules),
                options=CodeGenOptions(emit_source_comments=False,
                                       thir_codegen=thir))
            return cpp
        src = (
            _PRELUDE
            + "def f(t: tuple[Int32, tuple[Int32, Int32]]) -> Int32:\n"
            + "    inner = t[1]\n    return inner[0]\n"
            + "def main():\n    print(f((1, (2, 3))))\n"
            + "main()\n")
        assert _fn(_lower(src), "f") is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)
        assert "std::get<1>(t)" in cpp

    def test_widened_bare_name_return_ineligible(self):
        # A bare-name return of a widened tuple keys on the narrow value-tuple
        # name arm -> rejects (the param is ineligible anyway).
        thir = _lower(
            _PRELUDE
            + "def f(t: tuple[Int32, tuple[Int32, Int32]])"
            + " -> tuple[Int32, tuple[Int32, Int32]]:\n    return t\n")
        assert _fn(thir, "f") is None

    def test_optional_str_element_routes(self):
        # An `Optional[str]` element: the str-view source takes the
        # `std::string(view)` wrap threaded through the Optional slot (the tuple
        # brace-init's implicit `std::string -> std::optional<std::string>`),
        # `None` renders `std::nullopt`, a str literal lands bare.
        thir = _lower(
            _PRELUDE
            + "def f(a: Int32, s: str) -> tuple[Int32, str | None]:\n"
            + "    return (a, s)\n"
            + "def g(a: Int32) -> tuple[Int32, str | None]:\n"
            + "    return (a, None)\n"
            + "def h(a: Int32) -> tuple[Int32, str | None]:\n"
            + "    return (a, 'x')\n")
        assert _fn(thir, "f") is not None
        assert _fn(thir, "g") is not None
        assert _fn(thir, "h") is not None

    def test_optional_str_view_annotated_element_routes(self):
        # A `StrView | None` element renders `std::optional<std::string_view>`; a
        # view source lands bare (no owned wrap -- `is_str_type` filters to the
        # owned slot), matching the AST.
        thir = _lower(
            _PRELUDE
            + "from tpy import StrView\n"
            + "def f(a: Int32, s: str) -> tuple[Int32, StrView | None]:\n"
            + "    return (a, s)\n")
        assert _fn(thir, "f") is not None

    def test_optional_str_element_faces_witnessed(self):
        from .testutil import _lower_ctx_witnessed
        _thir, faces = _lower_ctx_witnessed(
            _PRELUDE
            + "def f(a: Int32, s: str) -> tuple[Int32, str | None]:\n"
            + "    return (a, s)\n")
        assert faces.get("ret.tuple_opt_str_elem", 0) >= 1

    def test_optional_str_param_ineligible(self):
        # Return-slot only: an `Optional[str]` tuple PARAM has no bare-copy read
        # arm (the narrow `_value_tuple` param gate rejects), so it stays on AST.
        thir = _lower(
            _PRELUDE
            + "def f(t: tuple[Int32, str | None]) -> Int32:\n"
            + "    return t[0]\n")
        assert _fn(thir, "f") is None

    def test_view_nested_element_deferred(self):
        # A StrView (view) element keeps the nested tuple outside the value family
        # (static-storage literal pin) -- deferred.
        thir = _lower(
            _PRELUDE
            + "from tpy import StrView\n"
            + "def f(a: Int32, s: StrView) -> tuple[Int32, tuple[Int32, StrView]]:\n"
            + "    return (a, (a, s))\n")
        assert _fn(thir, "f") is None

    def test_optional_record_element_deferred(self):
        # An `Optional[record]` element is pointer-repr (not a value scalar), so
        # `_value_opt_scalar` rejects it -- the tuple stays on the AST path.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class R:\n    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
            "def f(a: Int32) -> tuple[Int32, R | None]:\n    return (a, None)\n")
        assert _fn(thir, "f") is None


class TestWidenedValueTupleReturnEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def nested(a: Int32, b: Int32, s: str) -> tuple[Int32, tuple[Int32, str]]:\n"
        + "    return (a, (b, s))\n"
        + "def deep(a: Int32) -> tuple[Int32, tuple[Int32, tuple[Int32, str]]]:\n"
        + "    return (a, (a, (a, 'x')))\n"
        + "def val(a: Int32, b: Int32) -> tuple[Int32, Int32 | None]:\n"
        + "    return (a, b)\n"
        + "def none(a: Int32) -> tuple[Int32, Int32 | None]:\n"
        + "    return (a, None)\n"
        + "def lit(a: Int32) -> tuple[Int32, Int32 | None]:\n"
        + "    return (a, 5)\n"
        + "def nopt(a: Int32, b: Int32) -> tuple[Int32, tuple[Int32, Int32 | None]]:\n"
        + "    return (a, (b, None))\n"
        + "def ostr(a: Int32, s: str) -> tuple[Int32, str | None]:\n"
        + "    return (a, s)\n"
        + "def ostr_none(a: Int32) -> tuple[Int32, str | None]:\n"
        + "    return (a, None)\n"
        + "def ostr_lit(a: Int32) -> tuple[Int32, str | None]:\n"
        + "    return (a, 'x')\n"
        + "def main():\n    print(0)\nmain()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_nested_spell(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert ("return std::tuple<int32_t, std::tuple<int32_t, std::string>>"
                "{a, std::tuple<int32_t, std::string>{b, std::string(s)}};" in cpp)
        assert ("return std::tuple<int32_t, std::optional<int32_t>>{a, std::nullopt};"
                in cpp)
        assert ("return std::tuple<int32_t, std::optional<int32_t>>{a, b};" in cpp)

    def test_emits_optional_str_wrap(self):
        cpp = self._cpp(self.SRC, thir=True)
        # A view source wraps `std::string(s)` (implicit optional conversion),
        # `None`->`std::nullopt`, a str literal bare.
        assert ("return std::tuple<int32_t, std::optional<std::string>>"
                "{a, std::string(s)};" in cpp)
        assert ("return std::tuple<int32_t, std::optional<std::string>>"
                "{a, std::nullopt};" in cpp)
        assert ("return std::tuple<int32_t, std::optional<std::string>>"
                "{a, \"x\"};" in cpp)


# --- Standalone tuple-unpack assignment: `a, b = <value-scalar-tuple name>` ---

# The statement form (not the for-loop head): `a, b = t` unpacking a value-scalar
# tuple param / local reuses the THIRTupleUnpack node -- `const auto& __tup_N = t;`
# then a fresh `T a = std::get<i>(__tup_N);` per non-discard target. A value-tuple
# call-result (`a, b = mk(n)`) or field read (`a, b = h.pair`) captures the rvalue
# by value (`auto __tup_N = <expr>;`). Swap / literal-parallel / nested / starred /
# record-element / Optional-element / str / reused-target forms take other arms.
class TestStandaloneTupleUnpack:
    def test_param_source_routes(self):
        thir = _lower(
            _PRELUDE
            + "def g(t: tuple[Int32, Int32]) -> Int32:\n    a, b = t\n    return a + b\n")
        fn = _fn(thir, "g")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.source == "t"
        assert up.targets == ("a", "b")
        assert up.target_cpps == ("int32_t", "int32_t")

    def test_local_tuple_source_routes(self):
        # The source is a value-tuple LOCAL (`p = (3, 4)`), itself an already-routed
        # value-tuple decl; the unpack binds `const auto& __tup = p`.
        thir = _lower(
            _PRELUDE
            + "def h() -> Int32:\n    p = (3, 4)\n    a, b = p\n    return a - b\n")
        fn = _fn(thir, "h")
        assert fn is not None
        assert isinstance(fn.body[0], THIRVarDecl)      # the tuple local
        assert isinstance(fn.body[1], THIRTupleUnpack)  # the unpack

    def test_str_target_routes_view_form(self):
        # A str tuple-unpack target binds view-form (`std::string_view a =
        # std::get<0>(...)`, a view into the source tuple element) -- the scalar
        # arm renders it via the same `render_type`, so it routes.
        thir = _lower(
            _PRELUDE
            + "def f(t: tuple[str, Int32]) -> Int32:\n"
            + "    a, b = t\n    print(a)\n    return b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert isinstance(fn.body[0], THIRTupleUnpack)
        assert fn.body[0].target_cpps[0] == "std::string_view"

    def test_str_target_for_each_routes(self):
        # The for-each sibling: `for name, count in pairs:` over a
        # list[tuple[str, Int32]] -- same view-form target.
        thir = _lower(
            _PRELUDE
            + "def f(pairs: list[tuple[str, Int32]]) -> None:\n"
            + "    for name, count in pairs:\n        print(name, count)\n")
        assert _fn(thir, "f") is not None

    def test_record_target_still_ineligible(self):
        # The deferred rung: a record (borrow) target takes the `&std::get<i>`
        # alias arm -- stays AST.
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Leaf:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "def f(t: tuple[Leaf, Int32]) -> Int32:\n"
            "    a, b = t\n    return b\n")
        assert _fn(thir, "f") is None

    def test_discard_slot_skips(self):
        # A `_` discard slot carries None through targets/target_cpps (its
        # std::get emits nothing).
        thir = _lower(
            _PRELUDE
            + "def f(t: tuple[Int32, Int32, Int32]) -> Int32:\n"
            + "    a, _, c = t\n    return a + c\n")
        up = _fn(thir, "f").body[0]
        assert up.targets == ("a", None, "c")
        assert up.target_cpps == ("int32_t", None, "int32_t")

    def test_readonly_tuple_source_routes(self):
        thir = _lower(
            _PRELUDE
            + "from tpy import readonly\n"
            + "def f(t: readonly[tuple[Int32, Int32]]) -> Int32:\n"
            + "    a, b = t\n    return a + b\n")
        assert _fn(thir, "f") is not None

    def test_target_reassigned_after_unpack(self):
        # A same-name re-`decl` of an unpack target (sema keeps `a = a + 1` a
        # TpyVarDecl) must lower as a reassign -- the unpack seeded `a` into scope.
        thir = _lower(
            _PRELUDE
            + "def f(t: tuple[Int32, Int32]) -> Int32:\n"
            + "    a, b = t\n    a = a + 100\n    return a - b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        reassign = fn.body[1]
        assert isinstance(reassign, THIRAssign)  # NOT a second THIRVarDecl

    def test_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(
            _PRELUDE
            + "def g(t: tuple[Int32, Int32]) -> Int32:\n    a, b = t\n    return a + b\n")
        assert faces.get("stmt.tuple_unpack", 0) >= 1

    def test_call_result_source_routes(self):
        # `a, b = mk(n)` -- a value-scalar-tuple call result captures by value
        # (`auto __tup = mk(n)`) via `source_expr`; the targets are fresh scalars.
        thir = _lower(
            _PRELUDE
            + "def mk(n: Int32) -> tuple[Int32, Int32]:\n    return (n, n)\n"
            + "def f(n: Int32) -> Int32:\n    a, b = mk(n)\n    return a + b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.source == "" and up.source_expr is not None
        assert up.targets == ("a", "b")
        assert up.target_cpps == ("int32_t", "int32_t")

    def test_field_source_routes(self):
        # `a, b = h.pair` -- a value-scalar-tuple field read off an F1-record
        # receiver captures by value (`auto __tup = h.pair`) via `source_expr`.
        # `_lower_ctx`: an F1-record receiver resolves through the live registry.
        thir = _lower_ctx(
            _PRELUDE
            + "class H:\n    pair: tuple[Int32, Int32]\n"
            + "    def __init__(self):\n        self.pair = (3, 4)\n"
            + "def f(h: H) -> Int32:\n    a, b = h.pair\n    return a + b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.source == "" and up.source_expr is not None

    def test_rvalue_source_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(
            _PRELUDE
            + "def mk(n: Int32) -> tuple[Int32, Int32]:\n    return (n, n)\n"
            + "def f(n: Int32) -> Int32:\n    a, b = mk(n)\n    return a + b\n")
        assert faces.get("stmt.tuple_unpack.rvalue_source", 0) >= 1

    def test_reused_target_assign_routes(self):
        # A reused plain scalar target takes the AST's declared-name assign
        # tail: `a = std::get<0>(__tup_N);` (no decl; `b` stays a fresh decl).
        thir = _lower(
            _PRELUDE
            + "def f(t: tuple[Int32, Int32]) -> Int32:\n"
            + "    a = 0\n    a, b = t\n    return a + b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        up = fn.body[1]
        assert isinstance(up, THIRTupleUnpack)
        assert up.binds == ("assign", "value")
        assert up.target_cpps == (None, "int32_t")

    def test_reused_target_assign_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(
            _PRELUDE
            + "def f(t: tuple[Int32, Int32]) -> Int32:\n"
            + "    a = 0\n    b = 0\n    a, b = t\n    return a + b\n")
        assert faces.get("stmt.tuple_unpack.assign_target", 0) >= 2

    def test_reused_str_target_byte_identical(self):
        # The reassigned-owns shape: a str target reused across a loop
        # (`head, tail = split2(head)`) assigns into the owned local; the
        # declared entry keeps its original type for later reads.
        def cpp(src: str, thir: bool):
            compiler, modules = _compile(src)
            _, out = compiler.generate_code_to_strings(
                _entry(modules),
                options=CodeGenOptions(emit_source_comments=False,
                                       thir_codegen=thir))
            return out
        src = (
            "def split2(s: str) -> tuple[str, str]:\n"
            "    n = len(s) // 2\n"
            "    return (s[:n], s[n:])\n"
            "def f() -> None:\n"
            "    head = \"abcdefgh\"\n"
            "    while len(head) > 1:\n"
            "        head, tail = split2(head)\n"
            "    print(head)\n"
            "f()\n")
        thir_cpp = cpp(src, thir=True)
        assert thir_cpp == cpp(src, thir=False)
        assert "head = std::get<0>(__tup_1);" in thir_cpp


    def test_record_element_source_ineligible(self):
        # A pointer-repr (record-element) tuple source is not a value-scalar
        # tuple -- the borrow/std::move unpack arms are deferred.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Leaf]) -> Int32:\n    a, b = t\n    return a\n")
        assert _fn(thir, "f") is None


class TestStandaloneTupleUnpackEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        _PRELUDE
        + "def g(t: tuple[Int32, Int32]) -> Int32:\n    a, b = t\n    return a + b\n"
        + "def h() -> Int32:\n    p = (10, 20)\n    a, b = p\n    return a - b\n"
        + "def disc(t: tuple[Int32, Int32, Int32]) -> Int32:\n"
        + "    a, _, c = t\n    return a + c\n"
        + "def twice(t: tuple[Int32, Int32], u: tuple[Int32, Int32]) -> Int32:\n"
        + "    a, b = t\n    c, d = u\n    return a + b + c + d\n"
        + "def main():\n"
        + "    print(g((1, 2)), h(), disc((4, 5, 6)), twice((1, 2), (3, 4)))\n"
        + "main()\n"
    )

    def test_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emits_const_ref_bind_and_gets(self):
        cpp = self._cpp(self.SRC, thir=True)
        assert "const auto& __tup_1 = t;" in cpp
        assert "int32_t a = std::get<0>(__tup_1);" in cpp
        assert "int32_t b = std::get<1>(__tup_1);" in cpp

    def test_per_function_counter_continuous(self):
        # Two unpacks in one body -> __tup_1 then __tup_2 (the counter mirrors
        # the AST's per-function ctx.unpack_counter).
        cpp = self._cpp(self.SRC, thir=True)
        assert "const auto& __tup_2 = u;" in cpp

    RVALUE_SRC = (
        _PRELUDE
        + "class H:\n    pair: tuple[Int32, Int32]\n"
        + "    def __init__(self):\n        self.pair = (3, 4)\n"
        + "def mk(n: Int32) -> tuple[Int32, Int32]:\n    return (n, n)\n"
        + "def fromcall(n: Int32) -> Int32:\n    a, b = mk(n)\n    return a + b\n"
        + "def fromfield(h: H) -> Int32:\n    a, b = h.pair\n    return a + b\n"
        + "def main():\n    print(fromcall(2), fromfield(H()))\n"
        + "main()\n"
    )

    def test_rvalue_source_byte_identical(self):
        assert (self._cpp(self.RVALUE_SRC, thir=True)
                == self._cpp(self.RVALUE_SRC, thir=False))

    def test_rvalue_source_emits_auto_value_bind(self):
        # A call / field rvalue source materializes by value (`auto __tup_N =
        # <expr>;`), not the name arm's `const auto&`.
        cpp = self._cpp(self.RVALUE_SRC, thir=True)
        assert "auto __tup_1 = mk(n);" in cpp
        assert "auto __tup_1 = h.pair;" in cpp
        assert "const auto& __tup_1 = mk(n);" not in cpp


# --- Standalone unpack target rungs: is_const_ref + Own move-out ---

# Sema flags an expensive-copy value target (BigInt) is_const_ref (zero-copy
# `const T& a = std::get<i>(...)`) and an Own[record] element is_owned (moved
# out: `Rec a = std::move(std::get<i>(...))`, the target an owned movable
# local). Both are standalone-only rungs; the for-each head keeps the narrow
# all-value gate.
_OWN_PAIR = (
    "from tpy import Int32, Own\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n        self.n = n\n"
    "def mk() -> tuple[Own[Leaf], Own[Leaf]]:\n"
    "    return (Leaf(1), Leaf(2))\n"
)


class TestStandaloneUnpackTargetRungs:
    def test_const_ref_target_routes(self):
        # BigInt elements: fresh expensive-copy value targets bind const-ref.
        thir = _lower(
            _PRELUDE
            + "def f(t: tuple[int, int]) -> int:\n    a, b = t\n    return a + b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.binds == ("cref", "cref")

    def test_const_ref_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(
            _PRELUDE
            + "def f(t: tuple[int, int]) -> int:\n    a, b = t\n    return a + b\n")
        assert faces.get("stmt.tuple_unpack.cref_target", 0) >= 1

    def test_own_call_source_routes(self):
        # `a, b = mk()` over a tuple[Own[Leaf], Own[Leaf]] call result: the
        # rvalue capture + per-element move-out decls.
        thir = _lower_ctx(
            _OWN_PAIR
            + "def f() -> Int32:\n    a, b = mk()\n    return a.n + b.n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.source == "" and up.source_expr is not None
        assert up.binds == ("move", "move")

    def test_own_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(
            _OWN_PAIR
            + "def f() -> Int32:\n    a, b = mk()\n    return a.n + b.n\n")
        assert faces.get("stmt.tuple_unpack.own_target", 0) >= 1

    def test_own_static_method_source_routes(self):
        # The module-qualified/static marker-call sibling: an Own-tuple
        # returning static factory is admitted at the unpack source
        # (owned_tuple_ret threads through _marker_call_supported).
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            "class Leaf:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "    @staticmethod\n"
            "    def make() -> tuple[Own[Leaf], Own[Leaf]]:\n"
            "        return (Leaf(1), Leaf(2))\n"
            "def f() -> Int32:\n    a, b = Leaf.make()\n    return a.n + b.n\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].binds == ("move", "move")

    def test_own_name_source_ineligible(self):
        # A NAME source with Own elements takes the AST's one-shot /
        # last-use-move / copy bind arms -- deferred.
        thir = _lower_ctx(
            _OWN_PAIR
            + "def f(t: tuple[Own[Leaf], Own[Leaf]]) -> Int32:\n"
            + "    a, b = t\n    return a.n + b.n\n")
        assert _fn(thir, "f") is None

    def test_own_str_element_ineligible(self):
        # Own[str] elements stay out (only Own[F1-record] moves are mirrored).
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            "def mk() -> tuple[Own[str], Int32]:\n    return (\"x\", 1)\n"
            "def f() -> Int32:\n    s, n = mk()\n    print(s)\n    return n\n")
        assert _fn(thir, "f") is None

    def test_own_str_element_return_ineligible(self):
        # The producer side of the same boundary: mk's OWN return statement
        # (a tuple literal with an Own[str] element) rejects at the
        # return-element gate, not just at the caller's unpack.
        thir = _lower_ctx(
            "from tpy import Int32, Own\n"
            "def mk() -> tuple[Own[str], Int32]:\n    return (\"x\", 1)\n")
        assert _fn(thir, "mk") is None

    def test_subscript_source_ineligible(self):
        # A subscript unpack source (`a, b = xs[i]`) stays AST -- the source
        # widening admits calls/globals/class-consts only (the container
        # tuple-element read is its own rung).
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "def f(xs: list[tuple[Int32, Int32]]) -> Int32:\n"
            "    a, b = xs[0]\n"
            "    return a + b\n")
        assert _fn(thir, "f") is None

    def test_reused_scalar_beside_own_target_routes(self):
        # A reused scalar target beside a fresh Own element: the Own moves
        # into its fresh local, the reused scalar takes the assign tail.
        src = (
            _OWN_PAIR
            + "def f() -> Int32:\n"
            + "    n = 0\n    a, n = mk2()\n    return a.n + n\n"
            + "def mk2() -> tuple[Own[Leaf], Int32]:\n    return (Leaf(1), 2)\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        up = fn.body[1]
        assert isinstance(up, THIRTupleUnpack)
        assert up.binds == ("move", "assign")
        compiler, modules = _compile(src)
        opts_thir = CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True)
        _, cpp_t = compiler.generate_code_to_strings(_entry(modules),
                                                     options=opts_thir)
        compiler, modules = _compile(src)
        opts_ast = CodeGenOptions(emit_source_comments=False,
                                  thir_codegen=False)
        _, cpp_a = compiler.generate_code_to_strings(_entry(modules),
                                                     options=opts_ast)
        assert cpp_t == cpp_a
        assert "n = std::get<1>(__tup_1);" in cpp_t


class TestStandaloneUnpackTargetRungsEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    CREF_SRC = (
        _PRELUDE
        + "def f(t: tuple[int, int]) -> int:\n    a, b = t\n    return a + b\n"
        + "def main():\n    print(f((10, 3)))\n"
        + "main()\n"
    )

    def test_cref_byte_identical(self):
        assert self._cpp(self.CREF_SRC, thir=True) == self._cpp(
            self.CREF_SRC, thir=False)

    def test_cref_emit(self):
        cpp = self._cpp(self.CREF_SRC, thir=True)
        assert "const ::tpy::BigInt& a = std::get<0>(__tup_1);" in cpp
        assert "const ::tpy::BigInt& b = std::get<1>(__tup_1);" in cpp

    OWN_SRC = (
        _OWN_PAIR
        + "def f() -> Int32:\n    a, b = mk()\n    return a.n + b.n\n"
        + "def main():\n    print(f())\n"
        + "main()\n"
    )

    def test_own_byte_identical(self):
        assert self._cpp(self.OWN_SRC, thir=True) == self._cpp(
            self.OWN_SRC, thir=False)

    def test_own_emit_moves_elements(self):
        cpp = self._cpp(self.OWN_SRC, thir=True)
        assert "auto __tup_1 = mk();" in cpp
        assert "Leaf a = std::move(std::get<0>(__tup_1));" in cpp
        assert "Leaf b = std::move(std::get<1>(__tup_1));" in cpp


# --- Own-tuple RETURN slot: `-> tuple[Own[Rec], ...]` literal sources ---

class TestOwnTupleReturn:
    def test_ctor_rvalue_elements_route(self):
        # `return (Leaf(1), Leaf(2))` -> `return std::tuple<Leaf, Leaf>{Leaf(1),
        # Leaf(2)};` (the value-tuple literal return arm over Own slots).
        thir = _lower_ctx(_OWN_PAIR)
        fn = _fn(thir, "mk")
        assert fn is not None
        ret = fn.body[-1]
        assert isinstance(ret, THIRReturn)
        assert isinstance(ret.value, THIRTupleLiteral)

    def test_own_param_elements_move(self):
        # Own params at last use move into the element slots.
        thir = _lower_ctx(
            _OWN_PAIR
            + "def mk2(a: Own[Leaf], b: Own[Leaf]) -> tuple[Own[Leaf], Own[Leaf]]:\n"
            + "    return (a, b)\n")
        fn = _fn(thir, "mk2")
        assert fn is not None

    def test_own_elem_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(_OWN_PAIR)
        assert faces.get("ret.tuple_own_elem", 0) >= 1

    def test_emit_byte_identical_and_moves(self):
        src = (
            _OWN_PAIR
            + "def mk2(a: Own[Leaf], b: Own[Leaf]) -> tuple[Own[Leaf], Own[Leaf]]:\n"
            + "    return (a, b)\n"
            + "def main():\n    a, b = mk()\n    c, d = mk2(a, b)\n"
            + "    print(c.n + d.n)\n"
            + "main()\n"
        )
        compiler, modules = _compile(src)
        entry = _entry(modules)

        def cpp(thir: bool):
            _, out = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir))
            return out

        thir_cpp = cpp(True)
        assert thir_cpp == cpp(False)
        assert ("return std::tuple<Leaf, Leaf>{Leaf(1), Leaf(2)};"
                in thir_cpp)
        assert ("return std::tuple<Leaf, Leaf>{std::move(a), std::move(b)};"
                in thir_cpp)


# --- Final[tuple[...]] unpack sources: module globals + class constants ---

class TestFinalTupleUnpackSources:
    def test_global_tuple_source_routes(self):
        # A readonly module-global value tuple seeds like the scalar globals;
        # the unpack binds `const auto& __tup_N = VERSION;`.
        thir = _lower(
            "from typing import Final\n"
            + _PRELUDE
            + "VERSION: Final[tuple[Int32, Int32]] = (1, 2)\n"
            + "def f() -> Int32:\n    a, b = VERSION\n    return a + b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.source == "VERSION" and up.source_cpp is None

    def test_nested_tuple_target_routes(self):
        # A nested value-tuple element is a plain value-copy target
        # (`std::tuple<...> inner = std::get<0>(__tup_N);`).
        thir = _lower(
            "from typing import Final\n"
            + _PRELUDE
            + "NESTED: Final[tuple[tuple[str, Int32], str]] = ((\"i\", 42), \"o\")\n"
            + "def f() -> Int32:\n"
            + "    inner, outer = NESTED\n"
            + "    name, val = inner\n"
            + "    print(name, outer)\n    return val\n")
        assert _fn(thir, "f") is not None

    def test_class_const_tuple_source_routes(self):
        # `major, minor = Version.SEMVER` -- the rvalue capture over the bare
        # qualified static (`auto __tup_N = Version::SEMVER;`).
        thir = _lower_ctx(
            "from typing import Final\n"
            + _PRELUDE
            + "class Version:\n"
            + "    SEMVER: Final[tuple[Int32, Int32]] = (1, 2)\n"
            + "def f() -> Int32:\n"
            + "    major, minor = Version.SEMVER\n    return major + minor\n")
        fn = _fn(thir, "f")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.source == "" and up.source_expr is not None

    def test_emit_byte_identical(self):
        src = (
            "from typing import Final\n"
            + _PRELUDE
            + "VERSION: Final[tuple[Int32, Int32]] = (1, 2)\n"
            + "class Version:\n"
            + "    SEMVER: Final[tuple[Int32, Int32]] = (3, 4)\n"
            + "def f() -> Int32:\n    a, b = VERSION\n    return a + b\n"
            + "def g() -> Int32:\n    c, d = Version.SEMVER\n    return c - d\n"
            + "def main():\n    print(f(), g())\n"
            + "main()\n"
        )
        compiler, modules = _compile(src)
        entry = _entry(modules)

        def cpp(thir: bool):
            _, out = compiler.generate_code_to_strings(
                entry, options=CodeGenOptions(emit_source_comments=False,
                                              thir_codegen=thir))
            return out

        thir_cpp = cpp(True)
        assert thir_cpp == cpp(False)
        assert "const auto& __tup_1 = VERSION;" in thir_cpp
        assert "auto __tup_1 = Version::SEMVER;" in thir_cpp


def _both_cpp(src: str) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, ast_cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=False))
    compiler2, modules2 = _compile(src)
    entry2 = _entry(modules2)
    _, thir_cpp = compiler2.generate_code_to_strings(
        entry2, options=CodeGenOptions(emit_source_comments=False,
                                       thir_codegen=True))
    assert thir_cpp == ast_cpp
    return ast_cpp


# Borrow-tuple branch-hoist family: a ptr-repr tuple local bound in both
# if arms and read after. The admission's deferred rungs must each REJECT
# (whole-body fallback, byte-identical via the AST path).
_BT_RECORDS = (
    "from tpy import Int32, readonly\n"
    "class Box2:\n"
    "    val: Int32\n"
    "    def __init__(self, v: Int32):\n        self.val = v\n"
)


class TestBorrowTupleHoistRejects:
    def test_const_source_hoist_rejects(self):
        # A readonly element source sets the borrow-decl const bit /
        # readonly result -- the non-const slice must fall back.
        src = (
            _BT_RECORDS
            + "def f(b: readonly[Box2], c: bool) -> Int32:\n"
            + "    if c:\n"
            + "        t = (1, b)\n"
            + "    else:\n"
            + "        t = (2, b)\n"
            + "    return t[0]\n"
            + "f(Box2(1), True)\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        _both_cpp(src)

    def test_call_source_hoist_rejects(self):
        # An owning-call source needs the emplace-slot machinery (deferred).
        src = (
            _BT_RECORDS
            + "def make(b: Box2) -> tuple[Int32, Box2]:\n"
            + "    return (1, b)\n"
            + "def f(b: Box2, c: bool) -> Int32:\n"
            + "    if c:\n"
            + "        t = make(b)\n"
            + "    else:\n"
            + "        t = (2, b)\n"
            + "    return t[0]\n"
            + "f(Box2(1), True)\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        _both_cpp(src)

    def test_walrus_bound_name_rejects_sources(self):
        # _borrow_tuple_binding_sources returns None when a walrus binds the
        # name anywhere -- uncollected sources would make the const/route
        # decision unsound.
        src = (
            "from tpy import Int32\n"
            "def f(c: bool) -> Int32:\n"
            "    t = 0\n"
            "    y = 0\n"
            "    if c:\n"
            "        y = (t := 5)\n"
            "    return t + y\n"
            "f(True)\n"
        )
        compiler, modules = _compile(src)
        entry = _entry(modules)
        fn = [x for x in entry.ast.functions if x.name == "f"][0]
        from .lower.context import _LowerCtx
        from .lower.statements import _borrow_tuple_binding_sources
        lc = _LowerCtx(fn, entry.analyzer, None)
        assert _borrow_tuple_binding_sources("t", lc) is None
        assert _borrow_tuple_binding_sources("y", lc) is not None

    def test_tuple_over_tuple_chain_rejects(self):
        # items[i][N].field admits CONTAINER-element receivers only; a
        # tuple-over-tuple chain (t[0][1].val off a nested tuple name) is an
        # unverified render and must fall back.
        src = (
            _BT_RECORDS
            + "def f(b: Box2) -> Int32:\n"
            + "    t = ((1, b), 2)\n"
            + "    return t[0][1].val\n"
            + "f(Box2(3))\n"
        )
        assert _fn(_lower_ctx(src), "f") is None
        _both_cpp(src)


class TestSetitemMethodRvalueMismatch:
    def test_covariant_method_rvalue_rejects(self):
        # The setitem method-rvalue row is exact-type only: a covariant
        # Box[Tcp].clone() into a dict[str, Box[Conn]] value slot must fall
        # back (the covariant converting-move stays a deferred rung).
        src = (
            "from typing import Protocol\n"
            "from tpy import Int32, dynamic\n"
            "from tplib import Box\n"
            "@dynamic\n"
            "class Conn(Protocol):\n"
            "    def ping(self) -> Int32: ...\n"
            "class Tcp(Conn):\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 1\n"
            "    def ping(self) -> Int32:\n"
            "        return self.n\n"
            "def seed(b: Box[Tcp]) -> None:\n"
            "    d: dict[str, Box[Conn]] = {}\n"
            "    d[\"x\"] = b.clone()\n"
            "seed(Box(Tcp()))\n"
        )
        assert _fn(_lower_ctx(src), "seed") is None
        _both_cpp(src)
