"""THIR F3 tuple rungs (tuple_to_pointer / tuple_to_storage) + tuple
subscript reads/writes."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..codegen_cpp.forms import LocalBinding
from .dump import dump_thir
from .nodes import (
    Form, THIRAssign, THIRBinOp, THIRFieldAccess, THIRFormConvert, THIRName,
    THIRReturn, THIRSubscript, THIRTupleLiteral, THIRTupleMembership,
    THIRTupleUnpack, THIRVarDecl, TupleSourceBind,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn, _lower_ctor, _ctor_tail,
    _lower_ctx_witnessed, _assert_byte_identical,
    _assert_routes_byte_identical, _PRELUDE,
)
from ..codegen_cpp.forms import is_ptr_variant_union
from ..typesys import TupleType, unwrap_readonly, unwrap_ref_type
from .lower.predicates import _param_is_const, _param_is_deep_const

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

    # This site is stricter than the chained compare's duplication check:
    # anything but a name or literal binds, so a `@property` read AND a plain
    # field read both take the temp, and only a name inlines.
    _RECORD = (
        _PRELUDE
        + "class P:\n"
          "    plain: Int32\n"
          "    def __init__(self) -> None:\n"
          "        self.plain = 5\n"
          "    @property\n"
          "    def probe(self) -> Int32:\n"
          "        return self.plain\n"
    )

    def _needle(self, expr: str):
        src = (self._RECORD
               + f"def f() -> bool:\n    p = P()\n    return {expr}\n")
        m = _fn(_lower_ctx(src), "f").body[-1].value
        assert isinstance(m, THIRTupleMembership)
        return m

    def test_property_needle_needs_temp(self):
        assert self._needle("p.probe in (1, 17)").need_temp

    def test_plain_field_needle_also_needs_temp(self):
        # Duplicable in a chained compare, but not trivial enough here.
        assert self._needle("p.plain in (1, 17)").need_temp

    def test_name_needle_inlines(self):
        thir = _lower_ctx(self._RECORD
                          + "def f() -> bool:\n    p = P()\n    n = p.plain\n"
                            "    return n in (1, 17)\n")
        m = _fn(thir, "f").body[-1].value
        assert isinstance(m, THIRTupleMembership) and not m.need_temp

    def test_property_needle_single_element_inlines(self):
        # One element renders the needle once, so there is nothing to bind.
        assert not self._needle("p.probe in (17,)").need_temp

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
# `std::get<N>(t).field` off a storage `auto&&` alias. A standalone bind off a
# borrow-form PARAM routes as the deref-flagged REF_ALIAS decl; Optional-element
# member access (null-check path) and writes stay on the AST path.
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

    def test_standalone_record_element_read_routes(self):
        # `b = t[1]` off a borrow-form tuple PARAM binds the element referent
        # (`Leaf& b = (*std::get<1>(t));`) -- the alias-decl wave's row; the
        # Own-param/storage boundary lives in test_thir_wave_alias_decl.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Leaf]) -> Int32:\n    b = t[1]\n    return b.n\n")
        assert _fn(thir, "f") is not None



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

    def test_pointer_repr_tuple_call_takes_the_auto_slot(self):
        # A pointer-repr tuple (record element) result is outside the
        # VALUE-tuple family, but it is not ineligible: the decl spells
        # `auto` and binds the borrow tuple whole (`decl.btuple_alias` /
        # `call.btuple_slot`), while the source return lifts storage->borrow.
        src = (
            _F3_RECORDS
            + "def pick(h: Holder) -> tuple[Int32, Leaf]:\n    return h.pair\n"
            + "def use(h: Holder) -> Int32:\n"
            + "    t = pick(h)\n"
            + "    return t[0]\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("call.btuple_slot", 0) == 1
        _assert_byte_identical(src)


# --- Widened value-tuple RETURN elements: nested value-tuple / value-Optional ---

# The value-tuple return slot admits, beyond scalar / owned-str elements, a
# NESTED value-tuple element (spelled recursively), a value-`Optional[scalar]`
# element (`None`->`std::nullopt` / a scalar value bare), and a value-
# `Optional[str]` element (a view source wraps `std::string(view)`). Return-slot
# only: the param / decl / subscript-read / bare-name-return sinks stay on the
# narrow value-tuple family (no bare-copy read arm for a widened-element receiver).
class TestWidenedValueTupleReturn:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

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

    def test_record_target_param_source_routes(self):
        # A record (borrow) target off a borrow-form tuple PARAM now routes via
        # the NAME_REF source bind + unwrap_ref/tuple_elem_ref ref-alias arm
        # (see TestStandaloneUnpackRefParamSource); the mixed record+scalar
        # ordering binds the record "ref" and the scalar "value".
        thir = _lower_ctx(
            "from tpy import Int32\n"
            "class Leaf:\n    n: Int32\n"
            "    def __init__(self, n: Int32):\n        self.n = n\n"
            "def f(t: tuple[Leaf, Int32]) -> Int32:\n"
            "    a, b = t\n    return b\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].binds == ("ref", "value")

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


    def test_record_element_param_source_routes(self):
        # A pointer-repr (record-element) tuple PARAM source now routes: the
        # borrow-form param binds NAME_REF and the record element aliases via
        # the ref arm (scalar-first ordering here binds "value" then "ref").
        thir = _lower_ctx(
            _F3_RECORDS
            + "def f(t: tuple[Int32, Leaf]) -> Int32:\n    a, b = t\n    return a\n")
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].binds == ("value", "ref")


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


class TestStandaloneUnpackRefParamSource:
    # `a, b = p` where `p` is an already-borrow-form tuple PARAM
    # (`tuple[Leaf, Leaf]` passes as `const std::tuple<Leaf*, Leaf*>&`): the
    # source binds `auto& __tup = p` (NAME_REF, no tuple_to_pointer lift) and
    # each ref target aliases via unwrap_ref/tuple_elem_ref.
    def test_ref_param_source_routes_name_ref(self):
        thir = _lower_ctx(
            _F3_RECORDS
            + "def show(p: tuple[Leaf, Leaf]) -> Int32:\n"
            + "    a, b = p\n    return a.n + b.n\n")
        fn = _fn(thir, "show")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.source_bind is TupleSourceBind.NAME_REF
        assert up.source_wrap_cpp is None
        assert up.binds == ("ref", "ref")

    def test_ref_param_source_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(
            _F3_RECORDS
            + "def show(p: tuple[Leaf, Leaf]) -> Int32:\n"
            + "    a, b = p\n    return a.n + b.n\n")
        assert faces.get("stmt.tuple_unpack.ref_param_source", 0) >= 1

    def test_ref_param_source_byte_identical(self):
        # Both a read-only (`auto&` still, ref targets) and a mutating body:
        # the element-pointer const-ness lives in the param type, so the
        # NAME_REF render is identical for const and mutable.
        _assert_byte_identical(
            _F3_RECORDS
            + "def show(p: tuple[Leaf, Leaf]) -> None:\n"
            + "    a, b = p\n    print(a.n + b.n)\n"
            + "def bump(p: tuple[Leaf, Leaf]) -> None:\n"
            + "    a, b = p\n    a.n = a.n + 1\n"
            + "def main() -> None:\n"
            + "    x = Leaf(1)\n    y = Leaf(2)\n"
            + "    show((x, y))\n    bump((x, y))\n"
            + "main()\n")

    def test_mixed_record_scalar_order_byte_identical(self):
        # Both mixed orders off a borrow-form param: a record element binds
        # "ref" (alias) and a scalar binds "value" -- the NAME_REF source with a
        # per-element mix. No corpus case covers this exact ordering, so pin the
        # emit here.
        _assert_byte_identical(
            _F3_RECORDS
            + "def recfirst(p: tuple[Leaf, Int32]) -> None:\n"
            + "    a, n = p\n    print(a.n)\n    print(n)\n"
            + "def scalarfirst(p: tuple[Int32, Leaf]) -> None:\n"
            + "    n, a = p\n    print(n)\n    print(a.n)\n"
            + "def main() -> None:\n"
            + "    x = Leaf(1)\n"
            + "    recfirst((x, 5))\n    scalarfirst((5, x))\n"
            + "main()\n")

    def test_storage_local_source_stays_storage_wrap(self):
        # BOUNDARY: a value-tuple STORAGE local source with ref targets keeps
        # the tuple_to_pointer lift (STORAGE_WRAP) -- it must NOT take the
        # borrow-form-param NAME_REF arm.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def use() -> Int32:\n"
            + "    x = Leaf(1)\n    y = Leaf(2)\n    t = (x, y)\n"
            + "    a, b = t\n    return a.n + b.n\n")
        fn = _fn(thir, "use")
        assert fn is not None
        up = next(s for s in fn.body if isinstance(s, THIRTupleUnpack))
        assert up.source_bind is TupleSourceBind.STORAGE_WRAP
        assert up.source_wrap_cpp is not None


class TestStandaloneUnpackOptPtrTarget:
    # `a, b = p` where `p: tuple[T | None, ...]` is a borrow-form param
    # (`const std::tuple<const T*, ...>&`): each optional-record element binds a
    # plain nullable pointer local (`const T* a = std::get<i>(__tup);`), NOT the
    # record ref-alias arm. The None-test / narrowed reads ride the pointer-
    # optional-record local machinery.
    OPT = (
        _F3_RECORDS
        + "def show(p: tuple[Leaf | None, Leaf | None]) -> Int32:\n"
        + "    a, b = p\n"
        + "    if a is not None:\n        return a.n\n    return 0\n")

    def test_opt_ptr_target_routes(self):
        thir = _lower_ctx(self.OPT)
        fn = _fn(thir, "show")
        assert fn is not None
        up = fn.body[0]
        assert isinstance(up, THIRTupleUnpack)
        assert up.source_bind is TupleSourceBind.NAME_REF
        assert up.binds == ("opt_ptr", "opt_ptr")
        # readonly-inferred param -> const element pointers
        assert up.target_cpps == ("const Leaf*", "const Leaf*")

    MUT = (
        _F3_RECORDS
        + "def bump(p: tuple[Leaf | None, Leaf | None]) -> None:\n"
        + "    a, b = p\n"
        + "    if a is not None:\n        a.n = a.n + 1\n")

    def test_opt_ptr_target_mutable_param(self):
        # A mutating body keeps the param non-const -> `Leaf*` (no const).
        thir = _lower_ctx(self.MUT)
        fn = _fn(thir, "bump")
        assert fn is not None
        up = fn.body[0]
        assert up.binds == ("opt_ptr", "opt_ptr")
        assert up.target_cpps == ("Leaf*", "Leaf*")

    def test_source_param_const_axes_agree(self):
        # The `const T*` element spelling keys on `_param_is_const`
        # (signature_const), but what it actually spells is the POINTEE's
        # const-ness -- the deep verdict. decide_param_const splits those two
        # axes for exactly one shape, a pointer-variant union, which a
        # TupleType never is. The arm is exact only while that holds, so pin
        # it: were a tuple param ever to carry signature-const without
        # deep-const, this row would spell a mutable slot `const T*`.
        for src, name, const in ((self.OPT, "show", True),
                                 (self.MUT, "bump", False)):
            _compiler, modules = _compile(src)
            entry = _entry(modules)
            func = next(f for f in entry.ast.functions if f.name == name)
            ptype = func.params[0][1]
            assert isinstance(unwrap_readonly(unwrap_ref_type(ptype)),
                              TupleType)
            assert not is_ptr_variant_union(
                unwrap_readonly(unwrap_ref_type(ptype)))
            assert _param_is_const("p", func, entry.analyzer) is const
            assert _param_is_deep_const("p", func, entry.analyzer) is const

    def test_opt_ptr_face_witnessed(self):
        _thir, faces = _lower_ctx_witnessed(self.OPT)
        assert faces.get("stmt.tuple_unpack.opt_ptr_target", 0) >= 1

    def test_opt_ptr_byte_identical(self):
        # Const (read-only) + mutable + mixed opt/scalar: the emitted pointer
        # spelling tracks the source param's const verdict on both paths.
        _assert_byte_identical(
            _F3_RECORDS
            + "def show(p: tuple[Leaf | None, Leaf | None]) -> None:\n"
            + "    a, b = p\n"
            + "    if a is not None:\n        print(a.n)\n"
            + "    if b is not None:\n        print(b.n)\n"
            + "def bump(p: tuple[Leaf | None, Int32]) -> None:\n"
            + "    a, n = p\n"
            + "    if a is not None:\n        a.n = a.n + n\n"
            + "def main() -> None:\n"
            + "    x = Leaf(1)\n    y = Leaf(2)\n"
            + "    show((x, y))\n    bump((x, 5))\n"
            + "main()\n")

    def test_local_optional_source_stays_ast(self):
        # BOUNDARY: opt_ptr targets ride the NAME_REF *param* source only.
        # The local below is not storage form -- the AST renders it borrow
        # form (`auto t = std::tuple<Leaf*, Leaf*>{&(x), &(y)};` then
        # `auto& __tup_1 = t;`), so no lift is involved; admitting it needs a
        # _borrow_form_tuple_local sibling to _borrow_form_tuple_param.
        thir = _lower_ctx(
            _F3_RECORDS
            + "def use() -> Int32:\n"
            + "    x = Leaf(1)\n    y = Leaf(2)\n"
            + "    t: tuple[Leaf | None, Leaf | None] = (x, y)\n"
            + "    a, b = t\n"
            + "    if a is not None:\n        return a.n\n    return 0\n")
        assert _fn(thir, "use") is None


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

    def test_own_name_source_routes_move_binds(self):
        # RE-PINNED ROUTED (decl-slot track): a NAME source with Own
        # elements now rides the NAME_MOVE / NAME_COPY holder binds
        # (last-use move here -- `auto&& __tup = std::move(t);`).
        src = (_OWN_PAIR
               + "def f(t: tuple[Own[Leaf], Own[Leaf]]) -> Int32:\n"
               + "    a, b = t\n    return a.n + b.n\n"
               + "def main() -> None:\n"
               + "    print(f(mk()))\n"
               + "main()\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        assert fn.body[0].binds == ("move", "move")
        _assert_byte_identical(src)

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

    def test_subscript_source_routes(self):
        # A subscript unpack source binds the same `auto __tup_N =
        # ::tpy::__getitem__(xs, 0);` rvalue capture as a call source; the
        # value-tuple ELEMENT read is admitted only in this position.
        src = ("from tpy import Int32\n"
               "def f(xs: list[tuple[Int32, Int32]]) -> Int32:\n"
               "    a, b = xs[0]\n"
               "    return a + b\n"
               "print(f([(1, 2)]))\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert witnesses.get("subscript.value_tuple_source", 0) >= 1
        _assert_byte_identical(src)

    def test_subscript_tuple_elem_value_position_still_defers(self):
        # BOUNDARY: the same element read in a VALUE position (not the
        # unpack capture) keeps rejecting -- only the tuple-source sink
        # takes the whole tuple bare.
        src = ("from tpy import Int32\n"
               "def f(xs: list[tuple[Int32, Int32]]) -> Int32:\n"
               "    t = xs[0]\n"
               "    return t[0]\n"
               "print(f([(1, 2)]))\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

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


def _gen_witnessed(source: str):
    """Generate through THIR and return (face witnesses, fallback). Resumable
    bodies lower at generation time (the emit hook), so `lower_module`-based
    helpers never see their faces."""
    compiler, modules = _compile(source)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=False, thir_codegen=True))
    return compiler._thir_face_witnesses, compiler._thir_fallback


class TestTupleTernary:
    """Tuple-result ternaries: the render is bare (`((c) ? (a) : (b))`), and
    the lowered form propagates from the arms so a lifting sink still sees a
    storage source."""

    def test_value_tuple_ternary_routes(self):
        src = (_F3_RECORDS
               + "def pick(c: bool) -> Int32:\n"
               + "    a = (1, 2)\n    b = (3, 4)\n"
               + "    t = a if c else b\n"
               + "    return t[0] + t[1]\n"
               + "print(pick(True))\n")
        thir, witnesses = _lower_ctx_witnessed(src)
        assert _fn(thir, "pick") is not None
        assert witnesses.get("ifexpr.tuple", 0) >= 1
        _assert_byte_identical(src)

    def test_borrow_tuple_ternary_frame_write_routes(self):
        # The generator frame write takes the ternary as a borrow-form
        # source: bare `u = ((cond) ? (t) : (t2));`, no tuple_to_pointer.
        src = (_F3_RECORDS
               + "from typing import Iterator\n"
               + "def gen(b: Leaf, c: Leaf, cond: bool)"
               + " -> Iterator[tuple[Int32, Leaf]]:\n"
               + "    t = (1, b)\n    t2 = (2, c)\n"
               + "    u = t if cond else t2\n"
               + "    yield u\n"
               + "def main() -> None:\n    pass\nmain()\n")
        witnesses, fallback = _gen_witnessed(src)
        assert witnesses.get("res.btuple_write", 0) >= 3
        assert witnesses.get("ifexpr.tuple", 0) >= 1
        assert not any(k.startswith("resumable:") for k in fallback)
        _assert_byte_identical(src)

    def test_storage_arm_ternary_frame_write_defers(self):
        # BOUNDARY: an arm reading a STORAGE-form tuple (a record's tuple
        # field alias) makes the whole ternary storage, so the frame write
        # keeps its named reject -- the bare assign would drop the lift.
        src = (_F3_RECORDS
               + "from typing import Iterator\n"
               + "def gen(h: Holder, b: Leaf, cond: bool)"
               + " -> Iterator[tuple[Int32, Leaf]]:\n"
               + "    s = h.pair\n"
               + "    t = (1, b)\n"
               + "    u = s if cond else t\n"
               + "    yield u\n"
               + "def main() -> None:\n    pass\nmain()\n")
        assert _fn(_lower_ctx(src), "gen") is None
        _assert_byte_identical(src)


class TestBorrowTupleAliasDecl:
    _SRC = (
        "from tpy import Int32\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n        self.val = v\n"
        "def pair_of(b: Box) -> tuple[Int32, Box]:\n"
        "    t = (1, b)\n"
        "    r = t\n"
        "    return r\n"
        "def alias(b: Box) -> Int32:\n"
        "    t = (1, b)\n"
        "    r = t\n"
        "    return r[0]\n"
        "def from_call(b: Box) -> Int32:\n"
        "    p = pair_of(b)\n"
        "    return p[0]\n"
        "def main() -> None:\n    print(alias(Box(5)) + from_call(Box(6)))\n"
        "main()\n"
    )

    def test_name_and_call_sources_route(self):
        # A ref-element tuple always spells `auto`, so re-binding one from a
        # name or from a call returning one is the plain pointer-repr copy --
        # the new binding aliases the same elements.
        thir, w = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "alias") is not None
        assert _fn(thir, "from_call") is not None
        assert w.get("decl.btuple_alias", 0) == 3   # r=t twice, p=pair_of(b)
        assert w.get("call.btuple_slot", 0) == 1
        # `return r` on an already-borrow local needs no tuple_to_pointer lift.
        assert w.get("ret.btuple_name", 0) == 1
        _assert_byte_identical(self._SRC)

    def test_storage_tuple_alias_takes_the_other_decl(self):
        # A tuple with no ref elements is NOT this shape: an lvalue storage
        # source binds the AST's `auto&&` durable alias, a different decl --
        # which is what it must route as, never the borrow-tuple family.
        src = (
            "from tpy import Int32\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n        self.val = v\n"
            "def f() -> Int32:\n"
            "    items: list[tuple[Int32, Box]] = [(1, Box(5))]\n"
            "    t = items[0]\n"
            "    return t[0]\n"
            "def main() -> None:\n    print(f())\nmain()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[1]  # body[0] declares `items`
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.STORAGE_TUPLE_ALIAS
        assert decl.form is Form.STORAGE
        # The distinction the pin was written to hold: the borrow-tuple alias
        # family must not claim a ref-element-free tuple.
        assert w.get("decl.btuple_alias", 0) == 0
        _assert_byte_identical(src)

    def test_own_tuple_call_result_still_defers(self):
        # `Own[tuple[..]]` has the same element list but binds OWNING storage
        # (`std::get<1>(t).val`, not `->val`), so it is not this shape -- the
        # owning-ness is only visible on the callee's declared return type.
        src = (
            "from tpy import Int32, Own\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n        self.val = v\n"
            "def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:\n"
            "    return (v, Box(v))\n"
            "def use() -> Int32:\n"
            "    t = make_pair(5)\n"
            "    return t[0] + t[1].val\n"
            "def main() -> None:\n    print(use())\nmain()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("decl.btuple_alias", 0) == 0
        _assert_byte_identical(src)

    def test_per_element_own_call_result_routes_mixed_alias(self):
        # `tuple[Own[A], B]` returns the BORROW form too, binding its owned
        # element by value; the single-binding alias decl now admits it
        # (`decl.mixed_own_alias`) and reads pick `.` vs `->` per element.
        src = (
            "from tpy import Int32, Own\n"
            "class Box:\n"
            "    val: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n        self.val = v\n"
            "def make_mixed(b: Box) -> tuple[Own[Box], Box]:\n"
            "    return (Box(1), b)\n"
            "def use(b: Box) -> Int32:\n"
            "    p = make_mixed(b)\n"
            "    return p[1].val\n"
            "def main() -> None:\n    print(use(Box(7)))\nmain()\n"
        )
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert w.get("decl.mixed_own_alias", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "auto p = make_mixed(b);" in cpp[1]
        assert "std::get<1>(p)->val" in cpp[1]


class TestStorageTupleAliasSourceShapes:
    """The `auto&&` storage-tuple alias over the two source shapes the AST's
    `is_storage_form_source` admits beyond a field read."""

    _BOX = (
        "from tpy import Int32, readonly\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n        self.val = v\n"
    )

    def test_subscript_source_routes(self):
        # A container subscript is unconditionally a storage source, so
        # `t = items[0]` binds the same durable `auto&&` alias a field does.
        src = (self._BOX
               + "def f() -> None:\n"
               + "    items: list[tuple[Int32, Box]] = [(1, Box(5))]\n"
               + "    t = items[0]\n"
               + "    t[1].val = 99\n"
               + "    print(items[0][1].val)\n"
               + "def main() -> None:\n    f()\nmain()\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        decl = fn.body[1]
        assert isinstance(decl, THIRVarDecl)
        assert decl.cpp_local_representation is LocalBinding.STORAGE_TUPLE_ALIAS
        assert decl.form is Form.STORAGE
        _assert_byte_identical(src)

    def test_alias_of_alias_name_source_routes(self):
        # A NAME source is a storage source only once it already aliases
        # storage -- `u = t` after `t = items[0]`. Mutation through the second
        # alias reaches the stored element, so both must bind, not copy.
        src = (self._BOX
               + "def f() -> None:\n"
               + "    items: list[tuple[Int32, Box]] = [(1, Box(3))]\n"
               + "    t = items[0]\n"
               + "    u = t\n"
               + "    u[1].val = 11\n"
               + "    print(items[0][1].val)\n"
               + "def main() -> None:\n    f()\nmain()\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        assert fn is not None
        for i in (1, 2):
            decl = fn.body[i]
            assert isinstance(decl, THIRVarDecl)
            assert (decl.cpp_local_representation
                    is LocalBinding.STORAGE_TUPLE_ALIAS)
        _assert_byte_identical(src)

    def test_plain_name_source_is_not_a_storage_source(self):
        # BOUNDARY: a name that does not already alias storage is not a
        # storage source, so it must not take the alias decl -- mirrors the
        # AST consulting `storage_form_tuple_locals` for the Name shape only.
        src = (self._BOX
               + "def f(t: tuple[Int32, Box]) -> None:\n"
               + "    u = t\n"
               + "    print(u[1].val)\n"
               + "def main() -> None:\n    f((1, Box(2)))\nmain()\n")
        thir = _lower_ctx(src)
        fn = _fn(thir, "f")
        if fn is not None:
            decl = fn.body[0]
            assert isinstance(decl, THIRVarDecl)
            assert (decl.cpp_local_representation
                    is not LocalBinding.STORAGE_TUPLE_ALIAS)
        _assert_byte_identical(src)

    def test_const_subscript_source_stays_ast(self):
        # BOUNDARY: the const verdict for a non-field source would have to come
        # from the AST's `is_const_storage_source`, whose body-global set drives
        # LATER borrow reads. The widening admits non-const sources only, so a
        # readonly container receiver must keep rejecting rather than bind an
        # alias whose const-ness THIR did not derive.
        src = (self._BOX
               + "def f(items: readonly[list[tuple[Int32, Box]]]) -> Int32:\n"
               + "    t = items[0]\n"
               + "    return t[1].val\n"
               + "def main() -> None:\n"
               + "    xs: list[tuple[Int32, Box]] = [(1, Box(5))]\n"
               + "    print(f(xs))\n"
               + "main()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)

    def test_const_alias_of_alias_stays_ast(self):
        # BOUNDARY, the NAME arm's half of the const rejection: `t = h.pair` off
        # a readonly receiver marks `t` const via the FIELD branch, so the
        # SECOND alias (`u = t`) must reject too. Without this the compound
        # shape is the one hole the subscript pin above does not cover -- a
        # const verdict laundered through one hop into a non-const alias.
        src = (self._BOX
               + "class Holder:\n"
               + "    pair: tuple[Int32, Box]\n"
               + "    def __init__(self, b: Box) -> None:\n"
               + "        self.pair = (1, b)\n"
               + "def f(h: readonly[Holder]) -> Int32:\n"
               + "    t = h.pair\n"
               + "    u = t\n"
               + "    return u[1].val\n"
               + "def main() -> None:\n"
               + "    print(f(Holder(Box(5))))\n"
               + "main()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)


class TestBorrowTupleLiteralReturn:
    """`return (n, p)` at a borrow-tuple return slot builds the tuple in place
    -- the per-element `&(...)` lift and the spelled `std::tuple<...>` prefix
    come from the literal builder, so no `tuple_to_pointer` convert wraps it."""

    _SRC = ("from tpy import Int32, readonly\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n        self.x = x\n")

    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=thir))
        return cpp

    def test_param_ref_element_routes(self):
        src = (self._SRC
               + "def f(n: Int32, p: P) -> tuple[Int32, P]:\n"
               + "    return (n, p)\n"
               + "def main() -> None:\n"
               + "    q = P(1)\n"
               + "    t = f(2, q)\n"
               + "    print(t[0], t[1].x)\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("ret.btuple_literal")
        assert "return std::tuple<int32_t, P*>{n, &(p)};" in self._cpp(
            src, thir=True)
        _assert_byte_identical(src)

    def test_readonly_return_spells_const_elements(self):
        # The const-ness of the element pointers comes from the RETURN type's
        # readonly-ness, matching the arg sink's `target_readonly` rule.
        src = (self._SRC
               + "def f(items: list[P]) -> readonly[tuple[P, P]]:\n"
               + "    return (items[0], items[1])\n"
               + "def main() -> None:\n"
               + "    xs = [P(1), P(2)]\n"
               + "    t = f(xs)\n"
               + "    print(t[0].x, t[1].x)\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("ret.btuple_literal")
        assert "std::tuple<const P*, const P*>{" in self._cpp(src, thir=True)
        _assert_byte_identical(src)

    def test_field_element_still_defers(self):
        # BOUNDARY: the literal builder admits NAME and SUBSCRIPT elements
        # only; a FIELD element is a later rung and must keep rejecting.
        src = (self._SRC
               + "class H:\n"
               + "    inner: P\n"
               + "    def __init__(self) -> None:\n        self.inner = P(1)\n"
               + "    def pair(self, other: P) -> tuple[P, P]:\n"
               + "        return (self.inner, other)\n"
               + "def main() -> None:\n"
               + "    h = H()\n"
               + "    q = P(2)\n"
               + "    t = h.pair(q)\n"
               + "    print(t[0].x, t[1].x)\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "pair") is None
        assert not faces.get("ret.btuple_literal")
        _assert_byte_identical(src)

    def test_optional_record_element_routes_as_borrow_tuple(self):
        # A pointer-repr element puts this tuple in the BORROW family from the
        # start -- the two families are mutually exclusive by type
        # classification, so the value-tuple path is never even attempted.
        # What this pins is the resulting FORM: `R*`, not `std::optional<R>`.
        src = ("from tpy import Int32\n"
               "class R:\n    x: Int32\n"
               "    def __init__(self, x: Int32) -> None:\n        self.x = x\n"
               "def f(a: Int32) -> tuple[Int32, R | None]:\n"
               "    return (a, None)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("ret.btuple_literal")
        assert "return std::tuple<int32_t, R*>{a, nullptr};" in self._cpp(
            src, thir=True)
        _assert_byte_identical(src)

    def test_three_element_tuple_routes(self):
        # Arity above the 2 every flipped corpus case uses -- the builder is
        # arity-general, so pin that rather than assume it.
        src = (self._SRC
               + "def f(n: Int32, p: P, m: Int32) -> tuple[Int32, P, Int32]:\n"
               + "    return (n, p, m)\n"
               + "def main() -> None:\n"
               + "    a = P(1)\n"
               + "    t = f(2, a, 3)\n"
               + "    print(t[0], t[1].x, t[2])\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("ret.btuple_literal")
        assert ("std::tuple<int32_t, P*, int32_t>{n, &(p), m}"
                in self._cpp(src, thir=True))
        _assert_byte_identical(src)

    def test_already_pointer_optional_element_passes_bare(self):
        # An Optional-ptr param element is ALREADY a pointer and passes bare
        # (`{n, q}`) -- the same-repr pointer-name row; it used to defer
        # when the builder only knew the addr_of render.
        src = (self._SRC
               + "def f(n: Int32, q: P | None) -> tuple[Int32, P | None]:\n"
               + "    return (n, q)\n"
               + "def main() -> None:\n"
               + "    a = P(1)\n"
               + "    u = f(4, a)\n"
               + "    print(u[0])\n"
               + "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("btuple.elem_optptr")
        assert "{n, q}" in self._cpp(src, thir=True)
        _assert_byte_identical(src)


# --- storage-call tuple decl (decl.storage_call_tuple) ---

class TestStorageCallTupleDecl:
    """An owning tuple call result declared storage-form: `auto t = f(...);`
    (spelled collapsed type when no ref elements) + storage element reads."""

    _SRC = (_PRELUDE
            + "from tpy import Own\n"
            + "class Box:\n"
            + "    val: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.val = v\n")

    def test_own_call_tuple_decl_routes(self):
        src = (self._SRC
               + "def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:\n"
               + "    return (v, Box(v))\n"
               + "def use() -> Int32:\n"
               + "    t = make_pair(5)\n"
               + "    return t[0] + t[1].val\n"
               + "print(use())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("decl.storage_call_tuple")
        _assert_byte_identical(src)

    def test_nested_owned_tuple_decl_routes(self):
        # The body deliberately never reads an element: nested-tuple
        # element reads (`pp[0][0].fd`) are an unported subscript row and
        # would fall the whole body back, so only the decl's storage copy
        # is pinnable here (byte-asserted below).
        src = (self._SRC
               + "def two() -> tuple[tuple[Own[Box], Own[Box]], Int32]:\n"
               + "    a = Box(1)\n"
               + "    b = Box(2)\n"
               + "    return ((a, b), 3)\n"
               + "def use() -> Int32:\n"
               + "    pp = two()\n"
               + "    return 0\n"
               + "print(use())\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("decl.storage_call_tuple")
        _assert_byte_identical(src)

    def test_borrow_tuple_return_decl_not_captured(self):
        # BOUNDARY: the callee returns an ALIAS (borrow-form tuple, no Own
        # anywhere in its signature) -- stamping the decl storage diverged
        # (the per-element-own rebind-alias-return corpus catch). The shape
        # must route via the borrow-decl arm, NOT this face.
        src = (self._SRC
               + "class Holder:\n"
               + "    pair: tuple[Int32, Box]\n"
               + "    def __init__(self, b: Box):\n"
               + "        self.pair = (7, b)\n"
               + "def pick(h: Holder) -> tuple[Int32, Box]:\n"
               + "    return h.pair\n"
               + "def use(h: Holder) -> Int32:\n"
               + "    pr = pick(h)\n"
               + "    pr[1].val = 55\n"
               + "    return pr[0]\n"
               + "h = Holder(Box(5))\n"
               + "print(use(h))\n"
               + "print(h.pair[1].val)\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert not faces.get("decl.storage_call_tuple")
        _assert_byte_identical(src)

    def test_reassigned_target_takes_rebind_slot(self):
        # A reassigned target takes the btuple-rebind-slot decl row
        # (owning slot + emplace), NOT the storage face.
        src = (self._SRC
               + "class Holder:\n"
               + "    pair: tuple[Int32, Box]\n"
               + "    def __init__(self, b: Box):\n"
               + "        self.pair = (7, b)\n"
               + "def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:\n"
               + "    return (v, Box(v))\n"
               + "def pick(h: Holder) -> tuple[Int32, Box]:\n"
               + "    t = make_pair(9)\n"
               + "    t = h.pair\n"
               + "    return t\n"
               + "h = Holder(Box(5))\n"
               + "print(pick(h)[0])\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "pick") is not None
        assert faces.get("decl.btuple_rebind_slot")
        assert not faces.get("decl.storage_call_tuple")
        _assert_byte_identical(src)


class TestBtupleRebindDecl:
    """Reassigned borrow-tuple local decls: ONE fixed borrow shape
    (`std::tuple<..., T*>`) across all bindings -- storage-lvalue init via
    the tuple_to_pointer lift, REF-capture literal directly, owning-call
    init via the optional slot + emplace (that row's routing pin is the
    converted boundary above). Const bindings spell `const T*` from the
    `ensure_borrow_tuple_const` fixpoint and the mixed own-borrow hybrid
    call binds directly (both routing pins below); borrow-call inits and
    owning calls at RESEAT position stay AST."""

    _SRC = (_PRELUDE
            + "class Box:\n"
            + "    val: Int32\n"
            + "    def __init__(self, v: Int32):\n        self.val = v\n"
            + "class Holder:\n"
            + "    pair: tuple[Int32, Box]\n"
            + "    def __init__(self, b: Box):\n"
            + "        self.pair = (7, b)\n")

    def test_subscript_init_lift_routes(self):
        src = (self._SRC
               + "def use(items: list[tuple[Int32, Box]]) -> None:\n"
               + "    t = items[0]\n"
               + "    t[1].val = 99\n"
               + "    t = items[1]\n"
               + "    t[1].val = 88\n"
               + "b = Box(1)\n"
               + "use([(1, b), (2, b)])\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("decl.btuple_lift")
        _assert_byte_identical(src)

    def test_literal_init_routes(self):
        src = (self._SRC
               + "def use(h: Holder, b: Box) -> Int32:\n"
               + "    t = (1, b)\n"
               + "    first = t[1].val\n"
               + "    t = h.pair\n"
               + "    t[1].val = 42\n"
               + "    return first\n"
               + "print(use(Holder(Box(5)), Box(3)))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("decl.btuple_literal")
        _assert_byte_identical(src)

    def test_borrow_call_init_stays_out(self):
        # BOUNDARY: an aliasing (non-owning) tuple-returning call init has
        # no owning rvalue to emplace -- the direct borrow assign is an
        # unported row, so the body stays AST.
        src = (self._SRC
               + "def pick(h: Holder) -> tuple[Int32, Box]:\n"
               + "    return h.pair\n"
               + "def use(h: Holder, h2: Holder) -> Int32:\n"
               + "    t = pick(h)\n"
               + "    first = t[1].val\n"
               + "    t = h2.pair\n"
               + "    return first\n"
               + "print(use(Holder(Box(5)), Holder(Box(9))))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is None
        assert not faces.get("decl.btuple_lift")
        assert not faces.get("decl.btuple_rebind_slot")
        _assert_byte_identical(src)

    def test_owning_call_reseat_stays_out(self):
        # BOUNDARY: an owning call at RESEAT position needs the rebind-slot
        # reuse machinery (the AST's rebind_slots read) -- unported, so any
        # such binding source rejects the whole name.
        src = (self._SRC
               + "from tpy import Own\n"
               + "def make_pair(v: Int32) -> Own[tuple[Int32, Box]]:\n"
               + "    return (v, Box(v))\n"
               + "def use(h: Holder) -> Int32:\n"
               + "    t = h.pair\n"
               + "    first = t[1].val\n"
               + "    t = make_pair(9)\n"
               + "    return first + t[1].val\n"
               + "print(use(Holder(Box(5))))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is None
        _assert_byte_identical(src)

    def test_annotated_const_decl_routes_const(self):
        # Converted fence: `ensure_borrow_tuple_const`'s whole-body fixpoint
        # spells the const borrow decl (`std::tuple<Int32, const Box*>` off
        # const-inferred receivers), so the shape routes byte-identically.
        src = (self._SRC
               + "def f(h: Holder, h2: Holder) -> Int32:\n"
               + "    t: tuple[Int32, Box] = h.pair\n"
               + "    t = h2.pair\n"
               + "    return t[1].val\n"
               + "print(f(Holder(Box(5)), Holder(Box(7))))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("decl.btuple_lift")
        _assert_byte_identical(src)

    def test_mixed_own_call_init_routes_direct_bind(self):
        # Converted fence: the MIXED Own+ref-element call decl binds its
        # own-borrow hybrid render directly (decl.btuple_reassigned -- no
        # slot, no lift; materializing would copy the borrowed half), and
        # the reseat lifts the storage lvalue.
        src = (self._SRC
               + "from tpy import Own, copy\n"
               + "class Pair2:\n"
               + "    pair: tuple[Box, Box]\n"
               + "    def __init__(self, b: Box):\n"
               + "        self.pair = (copy(b), copy(b))\n"
               + "def make_mixed(b: Box) -> tuple[Own[Box], Box]:\n"
               + "    return (Box(1), b)\n"
               + "def use(h: Pair2, b: Box) -> None:\n"
               + "    p = make_mixed(b)\n"
               + "    p = h.pair\n"
               + "    p[1].val = 66\n"
               + "use(Pair2(Box(5)), Box(3))\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "use") is not None
        assert faces.get("decl.btuple_reassigned")
        assert not faces.get("decl.btuple_rebind_slot")
        _assert_byte_identical(src)


class TestBorrowTupleElementSources:
    """Two element sources at a borrow-tuple literal slot: a read off another
    BORROW-form tuple is already the pointer, and a ctor RVALUE at a
    pointer-repr Optional slot takes the `tuple_value_to_borrow` source."""

    SWAP_SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def swap(p: tuple[P, Int32]) -> tuple[Int32, P]:\n"
        "    return (p[1], p[0])\n"
        "def main() -> None:\n"
        "    p = P(1)\n"
        "    t = (p, 10)\n"
        "    result = swap(t)\n"
        "    print(result[0])\n"
        "    p.x = 9\n"
        "    print(result[1].x)\n"
        "main()\n"
    )

    LIT_SRC = (
        "from tpy import Int32\n"
        "class P:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "def main() -> None:\n"
        "    items: list[tuple[P | None, Int32]] = [\n"
        "        (P(1), 10),\n"
        "        (None, 20),\n"
        "    ]\n"
        "    print(len(items))\n"
        "main()\n"
    )

    def test_btuple_subscript_element_passes_through(self):
        thir, faces = _lower_ctx_witnessed(self.SWAP_SRC)
        assert _fn(thir, "swap") is not None
        # Only `p[0]` reaches this row -- `p[1]` is an Int32 at a VALUE
        # slot, which the value-element arm claims earlier.
        assert faces["btuple.elem_btuple_subscript"] == 1

    def test_no_address_of_on_the_passthrough(self):
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(self.SWAP_SRC)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "std::tuple<int32_t, P*>{std::get<1>(p), std::get<0>(p)}" in cpp
        assert "&(std::get<" not in cpp

    def test_swap_byte_identical(self):
        _assert_byte_identical(self.SWAP_SRC)

    def test_container_literal_element_admits_the_rvalue_source(self):
        # A container-literal element is a flush position for the
        # `tuple_value_to_borrow` source tuple, so a ctor rvalue at a
        # pointer-repr Optional slot renders there as it does at a call arg.
        thir, faces = _lower_ctx_witnessed(self.LIT_SRC)
        assert _fn(thir, "main") is not None
        assert faces["btuple.value_to_borrow"] >= 1
        _assert_byte_identical(self.LIT_SRC)

    def test_storage_tuple_source_still_lifts(self):
        # The boundary the pass-through must NOT swallow: a STORAGE-form
        # tuple local (an `auto&&` alias) holds its elements by value, so
        # `std::get<i>(it)` is a `T&` and the address-of is required. The
        # shared `_subscript_yields_borrow_ptr` is what draws that line.
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def swap(p: tuple[P, Int32]) -> tuple[Int32, P]:\n"
            "    return (p[1], p[0])\n"
            "def via_storage(items: list[tuple[P, Int32]]) -> Int32:\n"
            "    for it in items:\n"
            "        r = swap((it[0], it[1]))\n"
            "        return r[0]\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    p = P(1)\n"
            "    print(via_storage([(p, 3)]))\n"
            "main()\n"
        )
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        _hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   thir_codegen=True))
        assert "std::tuple<P*, int32_t>{&(std::get<0>(it)), std::get<1>(it)}" in cpp
        _assert_byte_identical(src)

    def test_rvalue_element_at_a_plain_slot_still_defers(self):
        # The boundary for the container-literal `rvalue_ok` grant: the flag
        # only reaches the borrow-tuple ELEMENT rows. A ctor rvalue at a
        # plain (non-Optional) pointer-repr element slot has no address to
        # lift and no witnessed render, so it keeps rejecting.
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def main() -> None:\n"
            "    rows: list[tuple[P, Int32]] = [(P(1), 2)]\n"
            "    print(len(rows))\n"
            "main()\n"
        )
        # A fence needs a FACE assertion, not byte-identity: a fallback body
        # emits the same C++ by construction, so identity alone cannot tell
        # "still rejecting" from "started routing".
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("btuple.value_to_borrow", 0) == 0
        _assert_byte_identical(src)

    def test_container_element_subscript_still_lifts(self):
        # The boundary: a subscript off a CONTAINER (not a borrow tuple) is
        # an lvalue element, so it keeps the `&(...)` lift.
        src = (
            "from tpy import Int32\n"
            "class P:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "def take(t: tuple[P, Int32]) -> Int32:\n"
            "    return t[1]\n"
            "def main() -> None:\n"
            "    xs = [P(1)]\n"
            "    print(take((xs[0], 3)))\n"
            "main()\n"
        )
        _thir, faces = _lower_ctx_witnessed(src)
        assert faces.get("btuple.elem_btuple_subscript", 0) == 0
        _assert_byte_identical(src)


class TestPtrTupleLiteralCompare:
    """Pointer-repr tuple-LITERAL compare pairs and membership needles
    (binop.tuple_ptr_compare / binop.tuple_ptr_needle): borrow-form literal
    renders (`std::tuple<int32_t, Box*>{1, &(a)}`) through the deref-aware
    `::tpy::tuple_eq` / `tuple_lt` composition and the `tuple_to_storage`
    needle lift. NAME tuple operands stay outside the slice."""

    _SRC = (
        "from tpy import Int32\n"
        "class Box:\n"
        "    val: Int32\n"
        "    def __init__(self, val: Int32) -> None:\n"
        "        self.val = val\n"
        "    def __eq__(self, other: \"Box\") -> bool:\n"
        "        return self.val == other.val\n"
        "    def __lt__(self, other: \"Box\") -> bool:\n"
        "        return self.val < other.val\n"
        "    def __le__(self, other: \"Box\") -> bool:\n"
        "        return self.val <= other.val\n"
        "    def __gt__(self, other: \"Box\") -> bool:\n"
        "        return self.val > other.val\n"
        "    def __ge__(self, other: \"Box\") -> bool:\n"
        "        return self.val >= other.val\n")

    def test_all_six_ops_route(self):
        src = (self._SRC
               + "def f() -> None:\n"
               + "    a = Box(5)\n"
               + "    b = Box(5)\n"
               + "    print((1, a) == (1, b))\n"
               + "    print((1, a) != (1, b))\n"
               + "    print((1, a) < (1, b))\n"
               + "    print((1, a) > (1, b))\n"
               + "    print((1, a) <= (1, b))\n"
               + "    print((1, a) >= (1, b))\n"
               + "f()\n")
        _assert_routes_byte_identical(src)

    def test_membership_needle_lift_routes(self):
        src = (self._SRC
               + "def f() -> None:\n"
               + "    a = Box(5)\n"
               + "    b = Box(5)\n"
               + "    ts = [(1, a)]\n"
               + "    print((1, b) in ts)\n"
               + "    print((2, b) not in ts)\n"
               + "f()\n")
        _assert_routes_byte_identical(src)

    def test_str_element_mix_routes(self):
        src = (self._SRC
               + "def f() -> None:\n"
               + "    a = Box(5)\n"
               + "    b = Box(5)\n"
               + "    print((\"x\", a) == (\"x\", b))\n"
               + "f()\n")
        _assert_routes_byte_identical(src)

    def test_name_tuple_operand_still_defers(self):
        # BOUNDARY: a NAME tuple operand's read carries form conversions
        # the literal pair does not mirror -- the body keeps falling back.
        src = (self._SRC
               + "def f() -> None:\n"
               + "    a = Box(5)\n"
               + "    b = Box(5)\n"
               + "    t1 = (1, a)\n"
               + "    t2 = (1, b)\n"
               + "    print(t1 == t2)\n"
               + "f()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)


class TestValueOptScalarUnpackTargets:
    """Value-opt SCALAR tuple-unpack targets (`for a, b in gen():` on
    `Iterator[tuple[Int32 | None, ...]]`): the plain typed copy bind
    (`std::optional<int32_t> a = std::get<0>(tup);`)."""

    def test_generator_optional_pair_unpack_routes(self):
        src = ("from typing import Iterator\n"
               "from tpy import Int32\n"
               "def gen(items: list[Int32])"
               " -> Iterator[tuple[Int32 | None, Int32 | None]]:\n"
               "    for it in items:\n"
               "        yield (it, None)\n"
               "def f() -> None:\n"
               "    items = [Int32(1), Int32(2)]\n"
               "    for a, b in gen(items):\n"
               "        if a is not None:\n"
               "            print(a)\n"
               "f()\n")
        _assert_routes_byte_identical(src)

    def test_items_narrowed_target_deref_routes(self):
        # A narrowed value-opt target read at a VALUE sink (`total += v`)
        # must deref -- the binding registration in the for-head unpack
        # arm; without it the read renders the bare optional.
        src = ("from tpy import Int32\n"
               "def f(d: dict[str, Int32 | None]) -> Int32:\n"
               "    total = 0\n"
               "    for k, v in d.items():\n"
               "        if v is not None and len(k) > 0:\n"
               "            total += v\n"
               "    return total\n"
               "def go() -> None:\n"
               "    d: dict[str, Int32 | None] = {\"a\": 1, \"b\": None}\n"
               "    print(f(d))\n"
               "go()\n")
        _assert_routes_byte_identical(src)

    def test_standalone_narrowed_target_deref_routes(self):
        # The STANDALONE unpack sibling: a fresh value-opt target's
        # narrowed read at a value sink derefs (`(*a)`) via the same
        # binding registration in the standalone target loop.
        src = ("from tpy import Int32\n"
               "def f(p: tuple[Int32 | None, Int32]) -> Int32:\n"
               "    a, b = p\n"
               "    if a is not None:\n"
               "        return a + b\n"
               "    return b\n"
               "def go() -> None:\n"
               "    print(f((3, 4)))\n"
               "    print(f((None, 9)))\n"
               "go()\n")
        _assert_routes_byte_identical(src)


class TestTupleNameCallArgs:
    """Tuple NAMES at matching call slots: the own-element move
    (`consume(std::move(t))`, move.own_tuple -- the movable/last-use
    verdict rides _is_move_source and the move audit) and the borrow
    ptr-opt tuple decl local passed bare (the sync-body bare-names
    widening)."""

    def test_own_tuple_name_moves_at_last_use(self):
        src = ("from tpy import Int32, Own\n"
               "class A:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def make_pair() -> tuple[Own[A], Own[A]]:\n"
               "    return (A(1), A(2))\n"
               "def consume(p: tuple[Own[A], Own[A]]) -> Int32:\n"
               "    a, b = p\n"
               "    return a.n + b.n\n"
               "def f() -> None:\n"
               "    t = make_pair()\n"
               "    print(consume(t))\n"
               "f()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces.get("move.own_tuple", 0) >= 1
        _assert_byte_identical(src)

    def test_borrow_opt_tuple_local_passes_bare(self):
        src = ("from tpy import Int32\n"
               "class T:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def make_pair(a: T, b: T) -> tuple[T | None, T | None]:\n"
               "    return (a, None)\n"
               "def consume(p: tuple[T | None, T | None]) -> Int32:\n"
               "    x, y = p\n"
               "    if x is not None:\n"
               "        return x.n\n"
               "    return -1\n"
               "def f() -> None:\n"
               "    a = T(1)\n"
               "    b = T(2)\n"
               "    pair = make_pair(a, b)\n"
               "    print(consume(pair))\n"
               "f()\n")
        _assert_routes_byte_identical(src)
