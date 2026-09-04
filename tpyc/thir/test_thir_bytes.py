"""THIR F6 S6: bytes / BytesView values + the bytes tail (subscript / slices /
iteration / concat / aug-assign, increment 46)."""

from __future__ import annotations

import io

from ..codegen_cpp.context import CodeGenOptions
from .emit import emit_thir_body
from .testutil import _emit_expr
from .nodes import (
    Form, PrintForm, THIRAssign, THIRBinOp, THIRBytesLiteral, THIRCall,
    THIRForEach, THIRFormConvert, THIRName, THIRParamCopy, THIRStrSlice,
    THIRSubscript, THIRVarDecl,
)
from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _compile, _entry, _lower, _lower_ctx, _fn, _assert_byte_identical,
    _assert_routes_byte_identical, _lower_ctx_witnessed, _thir_ctx,
    _thir_ctx_witnessed,
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

    def test_cross_type_coercion_routes_identity(self):
        # The bytes->BytesView coerce is identity (a bytes value IS the
        # span); a literal source flips to the static bytes_literal span.
        thir = _lower(
            "from tpy import BytesView\n"
            'def g() -> BytesView:\n    return b"ab"\n'
            "def h(a: bytes) -> BytesView:\n    return a\n")
        assert _fn(thir, "g") is not None
        assert _fn(thir, "h") is not None

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

    def test_reassigned_bytes_param_copy_routes(self):
        # An owned-bytes param reassign hoists the AST's owned-copy prologue
        # (param_needs_copy_for_reassign) with the view-family respell: the
        # view param lands in an owned local; body reads keep their
        # view-form renders (the owned local converts at every view sink).
        src = 'def f(a: bytes) -> None:\n    a = b"other"\n    print(a)\n'
        fn = _fn(_lower(src), "f")
        assert fn is not None
        copy = fn.body[0]
        assert isinstance(copy, THIRParamCopy)
        assert (copy.name, copy.cpp_type) == ("a", "std::vector<uint8_t>")
        assert copy.init_cpp == "::tpy::bytes_copy(__param_a)"
        _assert_routes_byte_identical(src)

    def test_bytearray_param_len_routes(self):
        # len() over a bytearray param routes byte-identically now (the len
        # widening covers the bytearray container).
        src = "def f(a: bytearray) -> None:\n    print(len(a))\n"
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_own_bytes_slot_view_param_materializes(self):
        # An Own[bytes] slot at a FREE call materializes the owned copy from
        # a view-form bytes param (`sink(::tpy::bytes_copy(a))` -- the S6
        # convert row, the free-call twin of the method ladder's).
        src = (
            "from tpy import Own\n"
            "def sink(b: Own[bytes]) -> None:\n    print(b)\n"
            "def f(a: bytes) -> None:\n    sink(a)\n")
        thir = _lower(src)
        f = _fn(thir, "f")
        assert f is not None
        buf = io.StringIO()
        emit_thir_body(buf, f)
        assert "sink(::tpy::bytes_copy(a))" in buf.getvalue()
        _assert_byte_identical(src)



class TestBytesValuesEmit:
    def _cpp(self, src: str):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
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

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC)
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
        cpp = self._cpp(src)
        assert "std::vector<uint8_t> u = ::tpy::bytes_copy(a);" in cpp
        assert cpp == self._cpp(src)


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

    def test_slice_owned_return_routes(self):
        # An owned-bytes RETURN of a slice arrives as the bytesview_to_bytes
        # coerce, whose lambda IS the unconditional bytes_copy wrap -> the S6
        # view->owned FormConvert, in every position.
        src = "def f(b: bytes) -> bytes:\n    return b[1:3]\n"
        _assert_routes_byte_identical(src)
        ret = _fn(_lower(src), "f").body[0]
        assert isinstance(ret.value, THIRFormConvert)
        assert ret.value.form is Form.STORAGE
        assert (_emit_expr(ret.value)
                == "::tpy::bytes_copy(::tpy::bytes_slice(b, "
                   "::tpy::BasicSlice{1, 3}))")

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

    def test_bytearray_operand_and_result_route(self):
        # bytes + bytearray resolves to the same native bytes_concat dunder;
        # the bytearray operand reads bare (its vector converts to the
        # helper's span param). A bytearray RECEIVER makes the RESULT a
        # bytearray and pins the literal operand to the OWNED render -- the
        # bytes receiver's overload takes the view one.
        src = ("def f(a: bytes, m: bytearray) -> bytes:\n"
               "    return a + m\n"
               "def g(m: bytearray) -> None:\n"
               '    bb = m + b"cd"\n'
               "    print(bb)\n"
               "    print(m * 2)\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        out = hpp + cpp
        assert "return (::tpy::bytes_concat(a, m));" in out
        assert ('std::vector<uint8_t> bb = (::tpy::bytes_concat(m, '
                '::tpy::bytes_literal_owned("cd", 2)));') in out
        assert ("::tpy::ByteArrayPrinter((::tpy::bytes_repeat(m, 2)))"
                in out)
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("binop.bytearray_operand", 0) >= 1
        assert w.get("binop.bytearray_result", 0) >= 1

    def test_list_concat_keeps_its_own_arm(self):
        # BOUNDARY (no over-capture): the bytearray rows are keyed on the
        # bytearray RESULT plus the resolved bytes helper, so a container
        # result still takes the container ladder.
        src = ("from tpy import Int32\n"
               "def f(a: list[Int32], b: list[Int32]) -> None:\n"
               "    print(a + b)\n"
               "f([1], [2])\n")
        _assert_routes_byte_identical(src)
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("binop.list_concat", 0) >= 1
        assert not w.get("binop.bytearray_result")
        assert not w.get("binop.bytearray_operand")

    def test_repeat_both_directions_route(self):
        # bytes repeat is commutative in the surface (`b * n` and `n * b`), and
        # __mul__/__rmul__ both resolve to ::tpy::bytes_repeat with the bytes
        # pinned to the receiver slot -- the reverse (count-first) operand order
        # takes the else-branch of the repeat gate. Both route byte-identically.
        fwd = 'def f(a: bytes) -> None:\n    print(a * 3)\n'
        rev = 'def f(a: bytes) -> None:\n    print(3 * a)\n'
        for src in (fwd, rev):
            assert _fn(_lower(src), "f") is not None
            _assert_byte_identical(src)

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
    def _cpp(self, src: str):
        compiler, modules = _compile(src)
        entry = _entry(modules)
        _, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
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

    def test_emit_arms(self):
        cpp = self._cpp(self.SRC)
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


class TestBytearrayStorageCallDecl:
    """The bytearray rows of the storage-call decl arm: single-assignment
    routes (function scope AND branch-first); a reassigned local must fall
    back -- bytearray is a `_storage_call_ret` family but sits OUTSIDE
    `_storage_call_container`, so the reassign/escape guards name it
    explicitly (a plain-copy decl would silently diverge from the AST's
    pointer-rebind form)."""

    def test_branch_first_decl_routes(self):
        src = ("def f(c: bool) -> None:\n"
               "    if c:\n"
               "        ba = bytearray(b\"ab\")\n"
               "        print(len(ba))\n")
        assert _fn(_lower(src), "f") is not None
        _, cpp = _assert_byte_identical(src)
        # Single-assignment: a plain by-value slot, no rebind machinery.
        assert ("std::vector<uint8_t> ba = ::tpy::bytes_copy("
                "::tpy::bytes_literal(\"ab\", 2));") in cpp
        assert "__slot_" not in cpp

    def test_reassigned_routes(self):
        # The reassigned owning-call bind takes the two-slot rebind
        # machinery, like the other reference containers.
        src = ("def f() -> None:\n"
               "    ba = bytearray(b\"ab\")\n"
               "    ba = bytearray(b\"cd\")\n"
               "    print(len(ba))\n")
        assert _fn(_lower(src), "f") is not None
        _, cpp = _assert_byte_identical(src)
        assert "std::optional<std::vector<uint8_t>> __slot_2;" in cpp
        assert "std::vector<uint8_t>* ba = &__slot_1;" in cpp
        assert ("ba = &*(__slot_2 = ::tpy::bytes_copy("
                "::tpy::bytes_literal(\"cd\", 2)));") in cpp
        assert "::tpy::__len__((*ba))" in cpp

    def test_branch_reassigned_routes(self):
        src = ("def f(c: bool) -> None:\n"
               "    if c:\n"
               "        ba = bytearray(b\"ab\")\n"
               "        ba = bytearray(b\"cd\")\n"
               "        print(len(ba))\n")
        assert _fn(_lower(src), "f") is not None


class TestBytearrayRefAlias:
    """`bytearray` is a reference type, so a name/field alias binds
    `std::vector<uint8_t>&` -- the same family-blind REF_ALIAS decl the other
    ref containers take. The span-borrow shape that keeps `bytearray` out of
    other arms is a PARAM/arg-slot fact, not a decl one."""

    _HOLDER = (
        "from tpy import Int32\n"
        "class Holder:\n"
        "    data: bytearray\n"
        "    def __init__(self, data: bytearray):\n"
        "        self.data = data\n"
    )

    def test_name_alias_routes(self):
        # The alias must ALIAS: the append through `x` has to be visible on
        # `ba`, so a copy here would be a CPython-parity bug.
        src = ("def f() -> None:\n"
               "    ba = bytearray(b\"ab\")\n"
               "    x = ba\n"
               "    x.append(99)\n"
               "    print(len(ba))\n"
               "f()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("decl.bytearray_alias", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert "std::vector<uint8_t>& x = ba;" in cpp[1]

    def test_field_alias_routes(self):
        src = (self._HOLDER
               + "def f(h: Holder) -> None:\n"
               + "    y = h.data\n"
               + "    y.append(7)\n"
               + "    print(len(h.data))\n"
               + "def main() -> None:\n"
               + "    f(Holder(bytearray(b\"xy\")))\n"
               + "main()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("decl.bytearray_alias", 0) == 1
        _assert_routes_byte_identical(src)

    def test_reassigned_alias_stays_ast(self):
        # BOUNDARY: a reassigned alias cannot bind a reference -- it takes the
        # pointer-rebind form, a render this arm does not carry.
        src = ("def f(a: bytearray, b: bytearray, go: bool) -> None:\n"
               "    x = a\n"
               "    if go:\n"
               "        x = b\n"
               "    print(len(x))\n"
               "def main() -> None:\n"
               "    f(bytearray(b\"a\"), bytearray(b\"bc\"), True)\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")


class TestBytearrayRvalueCtorArg:
    """A `bytearray()` rvalue at a plain `bytearray` CTOR slot passes bare."""

    _HOLDER = (
        "from tpy import Int32, Own\n"
        "class Holder:\n"
        "    data: bytearray\n"
        "    def __init__(self, data: bytearray):\n"
        "        self.data = data\n"
        "class OwnHolder:\n"
        "    data: bytearray\n"
        "    def __init__(self, data: Own[bytearray]):\n"
        "        self.data = data\n"
        "def take(b: bytearray) -> Int32:\n"
        "    return len(b)\n"
    )

    def test_ctor_rvalue_routes(self):
        src = (self._HOLDER
               + "def f() -> None:\n"
               + "    h = Holder(bytearray(b\"xy\"))\n"
               + "    print(len(h.data))\n"
               + "f()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("ctor.bytearray_rvalue", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert ("Holder h = Holder(::tpy::bytes_copy("
                "::tpy::bytes_literal(\"xy\", 2)));" in cpp[1])

    def test_free_call_rvalue_takes_the_argtemp(self):
        # The row stays ctor-scoped: at a FREE call the AST hoists
        # `std::vector<uint8_t> __tmp_N = ...;` and passes the temp, which is
        # the ArgTemp row's job -- the bare pass-through must NOT claim it.
        src = (self._HOLDER
               + "def f() -> None:\n"
               + "    print(take(bytearray(b\"xy\")))\n"
               + "f()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert w.get("ctor.bytearray_rvalue", 0) == 0
        assert w.get("argtemp.container_call", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert ("std::vector<uint8_t> __tmp_1 = ::tpy::bytes_copy("
                "::tpy::bytes_literal(\"xy\", 2));" in cpp[1])
        assert "take(__tmp_1)" in cpp[1]

    def test_own_ctor_slot_stays_ast(self):
        # BOUNDARY: an `Own[bytearray]` slot is the move family, not this
        # bare-rvalue bind.
        src = (self._HOLDER
               + "def f() -> None:\n"
               + "    h = OwnHolder(bytearray(b\"xy\"))\n"
               + "    print(len(h.data))\n"
               + "f()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.ctor_arg.own_record_nonf1")


class TestBytesNeDerivedNegation:
    """`!=` resolved via `__eq__` derives by negation: the emit must wrap
    `(!(::tpy::bytes_eq(x, y)))` -- a THIR-only drop of the `!` shipped
    unwitnessed until a routed body exposed it (os_urandom)."""

    def test_bytes_ne_negates(self):
        src = ("def f() -> None:\n"
               "    x = b\"a\"\n"
               "    y = b\"b\"\n"
               "    print(x != y)\n"
               "    print(x == y)\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_bytes_ne_condition_position(self):
        # The emit is position-agnostic, but pin the condition position
        # too -- the corpus site and the pin above are both print args.
        src = ("def f() -> None:\n"
               "    x = b\"a\"\n"
               "    y = b\"b\"\n"
               "    if x != y:\n        print(\"ne\")\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)


class TestBytesMembershipCallReceiver:
    """`needle in <bytes-returning call>`: the call haystack renders inline
    as the first `bytes_contains[_sub]` operand
    (`bytes_contains_sub(sock.recv(n), bytes_literal_owned(...))`)."""

    def test_call_haystack_routes(self):
        src = ("def data() -> bytes:\n"
               "    return b\"User-Agent: x\"\n"
               "def f() -> None:\n"
               "    print(b\"User-Agent\" in data())\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)

    def test_name_haystack_still_routes(self):
        src = ("def f() -> None:\n"
               "    hay = b\"abc\"\n"
               "    print(b\"b\" in hay)\n")
        assert _fn(_lower(src), "f") is not None
        _assert_byte_identical(src)


class TestBytesSliceFieldWrite:
    """`recv.b = x[1:3]` at an owned-`bytes` field -- the str slice row's
    twin, landing on the other side of the family split: the sema coerce
    carries no materialization for bytes, so the view-form slice takes the
    ordinary `::tpy::bytes_copy` STORAGE convert."""

    def test_slice_field_write_routes(self):
        # Both receiver shapes the row admits: an unbound `self.b` inside a
        # method, and a record LOCAL's field off a call-receiver slice.
        src = ("class Holder:\n"
               "    b: bytes\n"
               "    def __init__(self):\n"
               "        self.b = b\"\"\n"
               "    def set_b(self, x: bytes) -> None:\n"
               "        self.b = x[1:3]\n"
               "def mk() -> bytes:\n"
               "    return b\"abcdef\"\n"
               "def f() -> None:\n"
               "    h = Holder()\n"
               "    h.b = mk()[0:2]\n"
               "    h.set_b(b\"hello\")\n"
               "    print(len(h.b))\n")
        _assert_routes_byte_identical(src)
        _ctx, faces, fell = _thir_ctx_witnessed(src)
        # Both writes come through the new classifier row, not a neighbour
        # family that happens to render the same text.
        assert faces.get("field_write.bytes_slice") == 2, faces
        assert not fell, fell

    def test_stepped_slice_field_write_routes(self):
        # A STEPPED slice source already renders owned; the field write must
        # not double-wrap it (the row keys the source's own form).
        src = ("class Holder:\n"
               "    b: bytes\n"
               "    def __init__(self):\n"
               "        self.b = b\"\"\n"
               "    def set_b(self, x: bytes) -> None:\n"
               "        self.b = x[0:4:2]\n"
               "def f() -> None:\n"
               "    h = Holder()\n"
               "    h.set_b(b\"hello\")\n"
               "    print(len(h.b))\n")
        _assert_routes_byte_identical(src)

    def test_ancestor_subobject_field_write_routes(self):
        # `Base.b = ...` inside a derived method -- the unbound-self receiver
        # the row ORs in. Ablating that OR drops this shape alone.
        src = ("class Base:\n"
               "    b: bytes\n"
               "    def __init__(self):\n"
               "        self.b = b\"\"\n"
               "class Derived(Base):\n"
               "    n: int\n"
               "    def __init__(self):\n"
               "        Base.__init__(self)\n"
               "        self.n = 0\n"
               "    def set_b(self, x: bytes) -> None:\n"
               "        Base.b = x[1:3]\n"
               "    def reset(self) -> None:\n"
               "        Base.b = b\"zz\"\n"
               "def f() -> None:\n"
               "    d = Derived()\n"
               "    d.set_b(b\"hello\")\n"
               "    d.reset()\n"
               "    print(len(d.b))\n")
        _assert_routes_byte_identical(src)

    def test_bytearray_field_from_slice_stays_ast(self):
        # `bytearray` is a REFERENCE type on a different axis: its coercion
        # (`bytesview_to_bytearray`) has no disposition and `is_bytes_type`
        # excludes it, so the field write keeps rejecting.
        src = ("class Holder:\n"
               "    ba: bytearray\n"
               "    def __init__(self):\n"
               "        self.ba = bytearray()\n"
               "    def set_ba(self, x: bytes) -> None:\n"
               "        self.ba = x[1:3]\n"
               "def f() -> None:\n"
               "    h = Holder()\n"
               "    h.set_ba(b\"hello\")\n"
               "    print(len(h.ba))\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")

    def test_view_field_from_slice_stays_ast(self):
        # A `BytesView` FIELD is the view side of the family -- no owned sink,
        # so no copy row claims it.
        src = ("from tpy import BytesView\n"
               "class Holder:\n"
               "    v: BytesView\n"
               "    def __init__(self):\n"
               "        self.v = b\"\"\n"
               "    def set_v(self, x: bytes) -> None:\n"
               "        self.v = x[1:3]\n"
               "def f(x: bytes) -> None:\n"
               "    h = Holder()\n"
               "    h.set_v(x)\n"
               "    print(len(h.v))\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:assign.field_write_shape")


class TestBytearrayValueSlotDecl:
    """The bytearray VALUE decl slot's two DECIDED init shapes: a
    bytesview_to_bytearray-coerced view source materializes
    (`std::vector<uint8_t> ba = ::tpy::bytes_copy(<view>);` -- the coerce
    chokepoint's explicit materialize=True convert; bytearray is a reference
    type, so the family alone cannot pick view-copy over object-move), and
    the owned dunder rvalue (`bb = ba + b"cd"`) takes the fresh vector by
    value. The bytes-param IDENTITY coercion keeps rejecting (its AST oracle
    is wrong-code -- BUGS.md, span-vs-vector&)."""

    def test_view_slice_init_materializes(self):
        src = ("def make() -> bytes:\n"
               "    return b\"abcd\"\n"
               "def f() -> None:\n"
               "    ba: bytearray = make()[1:3]\n"
               "    print(len(ba))\n"
               "f()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("decl.bytearray_view_copy", 0) == 1
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert ("std::vector<uint8_t> ba = ::tpy::bytes_copy("
                "::tpy::bytes_slice(make(), ::tpy::BasicSlice{1, 3}));"
                in cpp)

    def test_owned_dunder_rvalue_takes_the_slot(self):
        # `bb = ba + b"cd"` -- the fresh concat vector lands in the value
        # slot; the coerce row is NOT the one that fires.
        src = ("def f() -> None:\n"
               "    ba = bytearray(b\"ab\")\n"
               "    bb = ba + b\"cd\"\n"
               "    print(len(bb))\n"
               "f()\n")
        hpp, cpp = _assert_routes_byte_identical(src)
        assert ('std::vector<uint8_t> bb = (::tpy::bytes_concat(ba, '
                '::tpy::bytes_literal_owned("cd", 2)));') in hpp + cpp
        _thir, w = _lower_ctx_witnessed(src)
        assert w.get("decl.bytearray_owned_rvalue", 0) == 1
        assert not w.get("decl.bytearray_view_copy")

    def test_bytes_param_identity_coercion_keeps_rejecting(self):
        # BOUNDARY: `ba: bytearray = p` off a bytes PARAM -- the identity
        # bytes_to_bytearray coercion whose AST oracle is ill-formed C++
        # (BUGS.md). THIR's reject is protecting the broken oracle; do not
        # admit until the AST-first fix lands.
        src = ("def f(p: bytes) -> None:\n"
               "    ba: bytearray = p\n"
               "    print(len(ba))\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.var_decl:decl.slot_type")

    def test_name_alias_stays_on_alias_cascade(self):
        # BOUNDARY: `ba2 = ba` keeps the REF_ALIAS bind -- the value-slot
        # row must not claim an aliasable name init.
        src = ("def f() -> None:\n"
               "    ba = bytearray(b\"ab\")\n"
               "    ba2 = ba\n"
               "    ba2.append(99)\n"
               "    print(len(ba))\n"
               "f()\n")
        thir, w = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert w.get("decl.bytearray_alias", 0) == 1
        assert not w.get("decl.bytearray_view_copy")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "std::vector<uint8_t>& ba2 = ba;" in cpp
