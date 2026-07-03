"""THIR F6 S6: bytes / BytesView values + the bytes tail (subscript / slices /
iteration / concat / aug-assign, increment 46)."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .emit import _emit_expr
from .nodes import (
    Form, PrintForm, THIRAssign, THIRBinOp, THIRBytesLiteral, THIRCall,
    THIRForEach, THIRFormConvert, THIRName, THIRStrSlice, THIRSubscript,
    THIRVarDecl,
)
from .testutil import (
    _compile, _entry, _lower, _lower_ctx, _fn,
)

# --- bytes / BytesView values (F6 S6) ---


class TestBytesValues:
    def test_view_local_from_literal(self):
        # Literal init, no owned-forcing usage -> BytesView local; the literal
        # renders its static-storage span form (BORROW), incl. on a
        # reassignment (the binding, not the init type, decides the render).
        thir = _lower(
            'def f() -> None:\n    v = b"ab"\n    print(v)\n    v = b"cd"\n'
            "    print(v)\n")
        body = _fn(thir, "f").body
        decl = body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.resolved_type.to_cpp() == "std::span<const uint8_t>"
        assert isinstance(decl.init, THIRBytesLiteral) and decl.init.form is Form.BORROW
        reassign = body[2]
        assert isinstance(reassign, THIRAssign)
        assert isinstance(reassign.value, THIRBytesLiteral)
        assert reassign.value.form is Form.BORROW

    def test_owned_local_init_from_view_wraps(self):
        # u is forced owned (reassigned from an owned call result), so its
        # decl init off the view param copies explicitly (::tpy::bytes_copy).
        thir = _lower(
            "def mk() -> bytes:\n"
            '    return b"own"\n'
            "def f(a: bytes) -> None:\n"
            "    u = a\n    u = mk()\n    print(u)\n")
        f = _fn(thir, "f")
        decl = f.body[0]
        assert isinstance(decl, THIRVarDecl)
        assert decl.resolved_type.to_cpp() == "std::vector<uint8_t>"
        assert isinstance(decl.init, THIRFormConvert)
        assert decl.init.form is Form.STORAGE
        assert _emit_expr(decl.init) == "::tpy::bytes_copy(a)"
        # mk's owned-bytes call result is STORAGE -- the reassign stays bare.
        reassign = f.body[1]
        assert isinstance(reassign.value, THIRCall)
        assert reassign.value.form is Form.STORAGE

    def test_return_param_wraps_return_literal_owned(self):
        thir = _lower(
            "def f(a: bytes) -> bytes:\n    return a\n"
            'def g() -> bytes:\n    return b"lit"\n')
        f_ret = _fn(thir, "f").body[0]
        assert isinstance(f_ret.value, THIRFormConvert)
        inner = f_ret.value.value
        assert isinstance(inner, THIRName) and inner.form is Form.BORROW
        assert _emit_expr(f_ret.value) == "::tpy::bytes_copy(a)"
        g_ret = _fn(thir, "g").body[0]
        assert isinstance(g_ret.value, THIRBytesLiteral) and g_ret.value.form is Form.STORAGE

    def test_cross_type_coercion_ineligible(self):
        # A literal or bytes value at a BytesView return arrives wrapped in
        # the cross-type view coercion (position-dependent) -> AST path,
        # mirroring the str cross-type deferral.
        thir = _lower(
            "from tpy import BytesView\n"
            'def g() -> BytesView:\n    return b"ab"\n'
            "def h(a: bytes) -> BytesView:\n    return a\n")
        assert _fn(thir, "g") is None
        assert _fn(thir, "h") is None

    def test_compare_and_len_route(self):
        # bytes __eq__ is a @native free-function dunder (no cpp_template) --
        # the native binop arm renders gen_call_from_fi's shape.
        thir = _lower(
            "from tpy import Int32\n"
            "def f(a: bytes, b: bytes) -> bool:\n    return a == b\n"
            "def g(a: bytes) -> Int32:\n    return len(a)\n"
            'def h(a: bytes) -> bool:\n    return a == b"ab"\n')
        cmp_ret = _fn(thir, "f").body[0]
        assert isinstance(cmp_ret.value, THIRBinOp)
        assert _emit_expr(cmp_ret.value) == "(::tpy::bytes_eq(a, b))"
        len_ret = _fn(thir, "g").body[0]
        assert isinstance(len_ret.value, THIRCall)
        assert len_ret.value.native_name == "tpy::__len__"
        # A literal compare operand renders OWNED (no target in compare position).
        lit_cmp = _fn(thir, "h").body[0].value
        assert isinstance(lit_cmp.right, THIRBytesLiteral) and lit_cmp.right.form is Form.STORAGE

    def test_bytes_args_pass_through(self):
        # Value args pass bare; a literal into a bytes/BytesView slot takes the
        # static-span pin (BORROW), incl. through the view coercion wrap.
        thir = _lower(
            "from tpy import BytesView\n"
            "def use_owned(b: bytes) -> None:\n    print(b)\n"
            "def use_view(v: BytesView) -> None:\n    print(v)\n"
            "def f(a: bytes) -> None:\n"
            "    use_owned(a)\n"
            '    use_owned(b"oo")\n'
            '    use_view(b"vv")\n')
        f = _fn(thir, "f")
        assert isinstance(f.body[0].expr.args[0], THIRName)
        lit_owned = f.body[1].expr.args[0]
        assert isinstance(lit_owned, THIRBytesLiteral) and lit_owned.form is Form.BORROW
        lit_view = f.body[2].expr.args[0]
        assert isinstance(lit_view, THIRBytesLiteral) and lit_view.form is Form.BORROW

    def test_print_arg_form(self):
        thir = _lower('def f(a: bytes) -> None:\n    print(a, b"x")\n')
        args = _fn(thir, "f").body[0].args
        assert args[0].print_form is PrintForm.BYTES
        assert args[1].print_form is PrintForm.BYTES
        assert isinstance(args[1].expr, THIRBytesLiteral) and args[1].expr.form is Form.STORAGE

    def test_reassigned_bytes_param_ineligible(self):
        # An owned-bytes param reassign hoists the AST's owned-copy prologue
        # (param_needs_copy_for_reassign).
        thir = _lower(
            'def f(a: bytes) -> None:\n    a = b"other"\n    print(a)\n')
        assert _fn(thir, "f") is None

    def test_bytearray_param_ineligible(self):
        # bytearray is a reference type on a different axis.
        thir = _lower("def f(a: bytearray) -> None:\n    print(len(a))\n")
        assert _fn(thir, "f") is None

    def test_own_bytes_slot_ineligible(self):
        # An Own[bytes] slot materializes an owned copy at the call boundary.
        thir = _lower(
            "from tpy import Own\n"
            "def sink(b: Own[bytes]) -> None:\n    print(b)\n"
            "def f(a: bytes) -> None:\n    sink(a)\n")
        assert _fn(thir, "f") is None



class TestBytesValuesEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import BytesView, Int32\n"
        "def mk() -> bytes:\n"
        '    return b"own"\n'
        "def use_view(v: BytesView) -> None:\n"
        "    print(v, len(v))\n"
        "def ret_copy(b: bytes) -> bytes:\n"
        "    return b\n"
        "def eq_test(b: bytes) -> bool:\n"
        '    return b == b"yes"\n'
        "def chain(a: bytes) -> None:\n"
        "    v = a\n"
        "    u = v\n"
        "    u = mk()\n"
        "    print(u, v)\n"
        "def lits() -> None:\n"
        '    v = b"ab"\n'
        '    e = b""\n'
        '    print(v, e, b"raw\\x00z")\n'
        "def main() -> None:\n"
        '    use_view(b"vv")\n'
        '    use_view(b"")\n'
        '    print(ret_copy(b"q"), eq_test(b"yes"))\n'
        '    chain(b"c")\n'
        "    lits()\n"
        "main()\n"
    )

    def test_routed(self):
        thir = _lower(self.SRC)
        for name in ("mk", "use_view", "ret_copy", "eq_test", "chain", "lits",
                     "main"):
            assert _fn(thir, name) is not None, name

    def test_bytes_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
        # The view->owned STORAGE convert (::tpy::bytes_copy) -- reachable for
        # the first time in this cell -- at both sinks:
        assert "return ::tpy::bytes_copy(b);" in cpp                # return
        assert "std::vector<uint8_t> u = ::tpy::bytes_copy(v);" in cpp  # decl init
        # Literal renders: span pin at view sinks, owned elsewhere; empty forms.
        assert 'use_view(::tpy::bytes_literal("vv", 2));' in cpp
        assert "use_view(std::span<const uint8_t>{});" in cpp
        assert 'std::span<const uint8_t> v = ::tpy::bytes_literal("ab", 2);' in cpp
        assert "std::span<const uint8_t> e = std::span<const uint8_t>{};" in cpp
        assert '::tpy::BytesPrinter(::tpy::bytes_literal_owned("raw\\000z", 5))' in cpp
        # The native-dunder compare and the BytesPrinter wrap.
        assert '(::tpy::bytes_eq(b, ::tpy::bytes_literal_owned("yes", 3)))' in cpp
        assert "::tpy::BytesPrinter(u)" in cpp

    def test_owned_init_wrap_byte_identical(self):
        # The minimal shape exercising the bytes arm of the view->owned
        # convert (`::tpy::bytes_copy`) -- previously UNREACHABLE in emit.py.
        src = (
            "def mk() -> bytes:\n"
            '    return b"own"\n'
            "def f(a: bytes) -> None:\n"
            "    u = a\n"
            "    u = mk()\n"
            "    print(u)\n"
            'f(b"q")\n'
        )
        thir = _lower(src)
        f = _fn(thir, "f")
        assert f is not None
        assert isinstance(f.body[0].init, THIRFormConvert)
        cpp = self._cpp(src, thir=True)
        assert "std::vector<uint8_t> u = ::tpy::bytes_copy(a);" in cpp
        assert cpp == self._cpp(src, thir=False)


# --- the bytes tail: subscript / slices / iteration / concat / aug-assign ---


class TestBytesTailGate:
    def test_subscript_routes(self):
        # b[i] -> UInt8 via the @native free-function dunder bytes_getitem
        # (not the containers' checked ::tpy::__getitem__ template).
        thir = _lower(
            "from tpy import Int32, UInt8, BytesView\n"
            "def f(a: bytes, i: Int32) -> UInt8:\n    return a[i]\n"
            "def g(v: BytesView) -> UInt8:\n    return v[0]\n")
        f_ret = _fn(thir, "f").body[0]
        assert isinstance(f_ret.value, THIRSubscript)
        assert f_ret.value.form is Form.VALUE
        assert _emit_expr(f_ret.value) == "::tpy::bytes_getitem(a, i)"
        g_ret = _fn(thir, "g").body[0]
        assert _emit_expr(g_ret.value) == "::tpy::bytes_getitem(v, 0)"

    def test_subscript_nonname_receiver_ineligible(self):
        # The receiver gate is name-only (mirrors the str twin).
        thir = _lower(
            "from tpy import UInt8\n"
            "def mk() -> bytes:\n"
            '    return b"own"\n'
            "def f() -> UInt8:\n    return mk()[0]\n")
        assert _fn(thir, "f") is None

    def test_slice_view_local_routes(self):
        # Non-stepped slice -> ::tpy::bytes_slice, a span VIEW result (BORROW)
        # binding a view local; stepped -> ::tpy::bytes_stepped_slice, an
        # owned vector (STORAGE).
        thir = _lower(
            "def f(b: bytes) -> None:\n"
            "    t = b[1:3]\n    print(len(t))\n"
            "    u = b[::2]\n    print(len(u))\n")
        body = _fn(thir, "f").body
        view_decl = body[0]
        assert isinstance(view_decl.init, THIRStrSlice)
        assert view_decl.init.form is Form.BORROW
        assert not view_decl.init.stepped
        assert (_emit_expr(view_decl.init)
                == "::tpy::bytes_slice(b, ::tpy::BasicSlice{1, 3})")
        stepped_decl = body[2]
        assert isinstance(stepped_decl.init, THIRStrSlice)
        assert stepped_decl.init.form is Form.STORAGE
        assert stepped_decl.init.stepped

    def test_slice_owned_decl_wraps(self):
        # A slice off a call receiver binds OWNED (sema resolves a view of a
        # temporary owned); the coerce-less decl init takes the S6 BORROW wrap
        # -> ::tpy::bytes_copy around the slice.
        thir = _lower(
            "def mk() -> bytes:\n"
            '    return b"abcdef"\n'
            "def f() -> None:\n"
            "    t = mk()[0:2]\n    print(len(t))\n")
        decl = _fn(thir, "f").body[0]
        assert isinstance(decl.init, THIRFormConvert)
        assert (_emit_expr(decl.init)
                == "::tpy::bytes_copy(::tpy::bytes_slice(mk(), "
                   "::tpy::BasicSlice{0, 2}))")

    def test_slice_owned_return_ineligible(self):
        # An owned-bytes RETURN of a slice arrives as the bytesview_to_bytes
        # coerce -- the deferred cross-type bytes-coercion cell -> AST.
        thir = _lower("def f(b: bytes) -> bytes:\n    return b[1:3]\n")
        assert _fn(thir, "f") is None

    def test_iteration_routes(self):
        # bytes/BytesView iterate as NativeIterable[UInt8] -- the value-scalar
        # typed-copy loop var; a pending bytes local resolves first.
        thir = _lower(
            "from tpy import Int32, BytesView\n"
            "def f(a: bytes) -> Int32:\n"
            "    n = 0\n"
            "    for x in a:\n        n += Int32(x)\n"
            "    return n\n"
            "def g(v: BytesView) -> None:\n"
            "    for x in v:\n        print(x)\n"
            "def h() -> None:\n"
            '    b = b"abc"\n'
            "    for x in b:\n        print(x)\n")
        for name in ("f", "g", "h"):
            fn = _fn(thir, name)
            assert fn is not None, name
            loop = next(s for s in fn.body if isinstance(s, THIRForEach))
            assert loop.elem_type.to_cpp() == "uint8_t"

    def test_concat_routes(self):
        # a + b -> the template-less @native __add__ (::tpy::bytes_concat),
        # an owned bytes result (STORAGE; paren-wrapped in value position).
        # A literal operand renders OWNED (the resolved overload's param is
        # owned bytes, never BytesView).
        thir = _lower(
            "from tpy import BytesView\n"
            "def f(a: bytes, b: bytes) -> bytes:\n    return a + b\n"
            "def g(a: bytes, v: BytesView) -> None:\n"
            '    t = a + b"x" + v\n'
            "    print(len(t))\n")
        f_ret = _fn(thir, "f").body[0]
        assert isinstance(f_ret.value, THIRBinOp)
        assert f_ret.value.form is Form.STORAGE
        assert _emit_expr(f_ret.value) == "(::tpy::bytes_concat(a, b))"
        g_decl = _fn(thir, "g").body[0]
        assert g_decl.resolved_type.to_cpp() == "std::vector<uint8_t>"
        nested = g_decl.init
        assert isinstance(nested, THIRBinOp)
        lit = nested.left.right
        assert isinstance(lit, THIRBytesLiteral) and lit.form is Form.STORAGE
        assert (_emit_expr(nested)
                == '(::tpy::bytes_concat((::tpy::bytes_concat(a, '
                   '::tpy::bytes_literal_owned("x", 1))), v))')

    def test_concat_bytearray_operand_ineligible(self):
        # bytes + bytearray also resolves to the native bytes_concat dunder,
        # but a bytearray operand is not a bytes-slice value.
        thir = _lower(
            "def f(a: bytes, m: bytearray) -> bytes:\n    return a + m\n")
        assert _fn(thir, "f") is None

    def test_aug_assign_routes(self):
        # t += v on an owned-bytes LOCAL desugars to the concat-and-assign
        # (t = ::tpy::bytes_concat(t, v); no parens -- the statement-RHS
        # shape) -- there is no bytes in-place append.
        thir = _lower(
            'def f(v: bytes) -> None:\n    t = b"go"\n    t += v\n'
            "    print(len(t))\n")
        aug = _fn(thir, "f").body[1]
        assert isinstance(aug, THIRAssign)
        assert isinstance(aug.value, THIRBinOp)
        assert not aug.value.paren_wrap
        assert aug.value.form is Form.STORAGE
        assert _emit_expr(aug.value) == "::tpy::bytes_concat(t, v)"

    def test_aug_assign_param_target_ineligible(self):
        # An aug-assigned bytes PARAM skips the AST's owned-copy prologue and
        # rebinds the span param to the concat's dying temporary (the silent-
        # dangle bytes face of the str aug-assign-param bug, BUGS.md) --
        # rejected, not reproduced.
        thir = _lower(
            "def f(a: bytes, b: bytes) -> None:\n    a += b\n    print(len(a))\n")
        assert _fn(thir, "f") is None


class TestBytesTailEmit:
    def _cpp(self, src: str, thir: bool):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False, thir_codegen=thir))
        return cpp

    SRC = (
        "from tpy import BytesView, Int32, UInt8, basic_slice\n"
        "class Holder:\n"
        "    data: bytes\n"
        "    def __init__(self, data: bytes) -> None:\n"
        "        self.data = data\n"
        "def mk() -> bytes:\n"
        '    return b"abcdef"\n'
        "def sub(b: bytes, i: Int32) -> UInt8:\n"
        "    return b[i]\n"
        "def sub_safe(b: bytes) -> Int32:\n"
        "    n = 0\n"
        "    for i in range(len(b)):\n"
        "        n += Int32(b[i])\n"
        "    return n\n"
        "def slices(b: bytes, sl: basic_slice) -> None:\n"
        "    t = b[1:3]\n"
        "    print(len(t))\n"
        "    u = b[1:5:2]\n"
        "    print(len(u), b[sl])\n"
        "def field_ops(h: Holder) -> None:\n"
        "    t = h.data[1:3]\n"
        "    print(len(t))\n"
        "    for x in h.data:\n"
        "        print(x)\n"
        "def iter_view(v: BytesView) -> Int32:\n"
        "    n = 0\n"
        "    for x in v:\n"
        "        n += Int32(x)\n"
        "    return n\n"
        "def cat(a: bytes, b: bytes) -> bytes:\n"
        "    return a + b\n"
        "def aug(v: bytes) -> None:\n"
        '    t = b"go"\n'
        "    t += v\n"
        '    t += b"end"\n'
        "    print(len(t))\n"
        "def main() -> None:\n"
        '    print(sub(b"abc", 1), sub_safe(b"abc"))\n'
        '    slices(b"hello", basic_slice(1, 3))\n'
        '    field_ops(Holder(b"hello"))\n'
        '    print(iter_view(b"abc"), len(cat(b"ab", b"cd")))\n'
        '    aug(b"zz")\n'
        "main()\n"
    )

    def test_routed(self):
        # `main` stays AST (the `Holder(...)` ctor-rvalue call arg is the
        # deferred gen_call_arg ownership frontier); every bytes-tail shape
        # routes. `_lower_ctx`: the F1-record receiver (`h.data`) resolves
        # through the active compiler.
        thir = _lower_ctx(self.SRC)
        for name in ("mk", "sub", "sub_safe", "slices", "field_ops",
                     "iter_view", "cat", "aug"):
            assert _fn(thir, name) is not None, name

    def test_bytes_tail_byte_identical(self):
        assert self._cpp(self.SRC, thir=True) == self._cpp(self.SRC, thir=False)

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC, thir=True)
        # Subscript: the native dunder, and the bounds-safe operator[] branch.
        assert "return ::tpy::bytes_getitem(b, i);" in cpp
        assert "b[static_cast<std::size_t>(i)]" in cpp
        # Slices: view, stepped-owned, slice-var index, field receiver.
        assert ("std::span<const uint8_t> t = "
                "::tpy::bytes_slice(b, ::tpy::BasicSlice{1, 3});") in cpp
        assert ("std::vector<uint8_t> u = "
                "::tpy::bytes_stepped_slice(b, ::tpy::Slice{1, 5, 2});") in cpp
        assert "::tpy::bytes_slice(b, sl)" in cpp
        assert "::tpy::bytes_slice(h.data, ::tpy::BasicSlice{1, 3})" in cpp
        # Iteration: the begin/end loop binds a uint8_t typed copy.
        assert "uint8_t x = *__beg_0;" in cpp
        assert "auto& __obj_0 = h.data;" in cpp
        # Concat: paren-wrapped in value position, owned-literal operands;
        # aug-assign emits the unwrapped concat-and-assign.
        assert "return (::tpy::bytes_concat(a, b));" in cpp
        assert "t = ::tpy::bytes_concat(t, v);" in cpp
        assert ('t = ::tpy::bytes_concat(t, '
                '::tpy::bytes_literal_owned("end", 3));') in cpp
