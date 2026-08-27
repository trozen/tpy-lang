"""Record lvalues, borrow-returning record calls and borrow-tuple literals
at a MODULE-QUALIFIED (marker) callee's argument slots, plus the `@inline`
driver/expansion pair.

The marker arg gate had only the bare-NAME record row
(`_record_pass_through_arg`); a field read, a `T&`-returning call and a
tuple literal all fell the whole body back even though the AST renders each
of them bare in place. Corpus witness: `str/fstr_decompose`.
"""

from .testutil import (_compile, _entry, _lower_ctx, _lower_ctx_witnessed,
                       _fn, _assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _thir_ctx,
                       _thir_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions

_HELPER = (
    "from tpy import Int32, Own\n"
    "class H:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "def take(h: H) -> Int32:\n"
    "    return h.n\n"
    "def take_own(h: Own[H]) -> Int32:\n"
    "    return h.n\n"
    "def sink(t: tuple[H, Int32]) -> Int32:\n"
    "    return t[0].n + t[1]\n"
)

_PRELUDE = (
    "import mrecarg_helper\n"
    "from mrecarg_helper import H\n"
    "from tpy import Int32\n"
    "class Owner:\n"
    "    _h: H\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self._h = H(n)\n"
    "    def get_h(self) -> H:\n"
    "        return self._h\n"
)


def _write_helper(tmp_path):
    (tmp_path / "mrecarg_helper.py").write_text(_HELPER)


def _cpp(src, tmp_path, thir):
    """The entry module's hpp + cpp: an inline method body lands in the
    header, so a cpp-only lens cannot see the renders under test."""
    compiler, modules = _compile(src, extra_lib_dirs=[tmp_path])
    hpp, cpp = compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                thir_codegen=thir))
    return hpp + cpp


class TestRecordLvalueAtMarkerRecordSlot:
    _SRC = _PRELUDE + (
        "def via_field(o: Owner) -> Int32:\n"
        "    return mrecarg_helper.take(o._h)\n"
        "def via_name(h: H) -> Int32:\n"
        "    return mrecarg_helper.take(h)\n"
        "def main() -> None:\n"
        "    o = Owner(3)\n"
        "    print(via_field(o))\n"
        "    print(via_name(H(7)))\n"
        "main()\n"
    )

    def test_field_read_routes(self, tmp_path):
        _write_helper(tmp_path)
        thir, faces = _lower_ctx_witnessed(self._SRC,
                                           extra_lib_dirs=[tmp_path])
        assert _fn(thir, "via_field") is not None
        assert faces.get("arg.record_field_marker")
        out = _cpp(self._SRC, tmp_path, thir=True)
        assert "::tpyapp::mrecarg_helper::take(o._h)" in out
        assert out == _cpp(self._SRC, tmp_path, thir=False)
        _ctx, fell = _thir_ctx(self._SRC, extra_lib_dirs=[tmp_path])
        assert fell == {}, fell

    _RET_SRC = _PRELUDE + (
        "class Svc:\n"
        "    _h: H\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self._h = H(n)\n"
        "    def get_h(self) -> H:\n"
        "        return self._h\n"
        "    def log(self) -> Int32:\n"
        "        return mrecarg_helper.take(self.get_h())\n"
        "    def log_field(self) -> Int32:\n"
        "        return mrecarg_helper.take(self._h)\n"
        "def main() -> None:\n"
        "    print(Svc(4).log())\n"
        "    print(Svc(5).log_field())\n"
        "main()\n"
    )

    def test_borrow_returning_call_and_self_field_route(self, tmp_path):
        _write_helper(tmp_path)
        thir, faces = _lower_ctx_witnessed(self._RET_SRC,
                                           extra_lib_dirs=[tmp_path])
        assert faces.get("arg.record_borrow_ret_marker")
        assert faces.get("arg.record_field_marker")
        out = _cpp(self._RET_SRC, tmp_path, thir=True)
        assert "::tpyapp::mrecarg_helper::take(this->get_h())" in out
        assert "::tpyapp::mrecarg_helper::take(this->_h)" in out
        assert out == _cpp(self._RET_SRC, tmp_path, thir=False)
        _ctx, fell = _thir_ctx(self._RET_SRC, extra_lib_dirs=[tmp_path])
        assert fell == {}, fell

    _OWN_SRC = _PRELUDE + (
        "def own_field(o: Owner) -> Int32:\n"
        "    return mrecarg_helper.take_own(o._h)\n"
        "def main() -> None:\n"
        "    print(own_field(Owner(3)))\n"
        "main()\n"
    )

    def test_field_at_own_slot_stays_ast(self, tmp_path):
        # BOUNDARY: the SAME field read at an `Own[H]` slot copies through a
        # `__tmp_N` on the AST path -- the row must be keyed on the borrow
        # `Ref[record]` slot, never on "record-typed slot"
        # (test_thir_callargs.py's own-slot fences say the same for the free
        # ladder). Identity is the claim here: the body falls back.
        _write_helper(tmp_path)
        compiler, modules = _compile(self._OWN_SRC, extra_lib_dirs=[tmp_path])
        from ..compilation_context import activate_compiler
        from .lower import lower_module
        entry = _entry(modules)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
        assert _fn(thir, "own_field") is None
        assert (_cpp(self._OWN_SRC, tmp_path, thir=True)
                == _cpp(self._OWN_SRC, tmp_path, thir=False))

    _OWN_RET_SRC = _PRELUDE + (
        "class Svc2:\n"
        "    _h: H\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self._h = H(n)\n"
        "    def get_h(self) -> H:\n"
        "        return self._h\n"
        "    def go(self) -> Int32:\n"
        "        return mrecarg_helper.take_own(self.get_h())\n"
        "def main() -> None:\n"
        "    print(Svc2(4).go())\n"
        "main()\n"
    )

    def test_borrow_returning_call_at_own_slot_stays_ast(self, tmp_path):
        # BOUNDARY: same call source, `Own[H]` slot -- the AST copies the
        # `T&` result through a temp, which neither the gate row nor the
        # BORROW_BIND render leg mirrors.
        _write_helper(tmp_path)
        assert (_cpp(self._OWN_RET_SRC, tmp_path, thir=True)
                == _cpp(self._OWN_RET_SRC, tmp_path, thir=False))
        _ctx, fell = _thir_ctx(self._OWN_RET_SRC, extra_lib_dirs=[tmp_path])
        _assert_rejects_at(fell, "body:expr.method_call",
                           "method.qualcall.arg.own")


class TestBorrowTupleLiteralAtMarkerSlot:
    _SRC = _PRELUDE + (
        "def pass_pair(h: H, n: Int32) -> Int32:\n"
        "    return mrecarg_helper.sink((h, n))\n"
        "def main() -> None:\n"
        "    print(pass_pair(H(1), 2))\n"
        "main()\n"
    )

    def test_lvalue_element_tuple_literal_routes(self, tmp_path):
        _write_helper(tmp_path)
        thir, faces = _lower_ctx_witnessed(self._SRC,
                                           extra_lib_dirs=[tmp_path])
        assert _fn(thir, "pass_pair") is not None
        assert faces.get("arg.btuple_literal_marker")
        out = _cpp(self._SRC, tmp_path, thir=True)
        assert ("std::tuple<::tpyapp::mrecarg_helper::H*, int32_t>"
                "{&(h), n}") in out
        assert out == _cpp(self._SRC, tmp_path, thir=False)
        _ctx, fell = _thir_ctx(self._SRC, extra_lib_dirs=[tmp_path])
        assert fell == {}, fell

    _RVALUE_SRC = _PRELUDE + (
        "def pass_rvalue(n: Int32) -> Int32:\n"
        "    return mrecarg_helper.sink((H(n), n))\n"
        "def main() -> None:\n"
        "    print(pass_rvalue(2))\n"
        "main()\n"
    )

    def test_rvalue_element_takes_the_value_to_borrow_helper(self, tmp_path):
        # The rvalue element rides the `tuple_value_to_borrow` source tuple
        # (its full-expression lifetime covers the call) -- the shape the
        # fstr corpus case needs, with the 2+-element BRACE init.
        _write_helper(tmp_path)
        thir, faces = _lower_ctx_witnessed(self._RVALUE_SRC,
                                           extra_lib_dirs=[tmp_path])
        assert _fn(thir, "pass_rvalue") is not None
        assert faces.get("arg.btuple_literal_marker")
        out = _cpp(self._RVALUE_SRC, tmp_path, thir=True)
        assert ("::tpy::tuple_value_to_borrow<std::tuple<"
                "::tpyapp::mrecarg_helper::H*, int32_t>>(std::tuple<"
                "::tpyapp::mrecarg_helper::H, int32_t>{"
                "::tpyapp::mrecarg_helper::H(n), n})") in out
        assert out == _cpp(self._RVALUE_SRC, tmp_path, thir=False)

    _VALUE_SRC = _PRELUDE + (
        "def pass_values(a: Int32, b: Int32) -> Int32:\n"
        "    return mrecarg_helper.take_pair((a, b))\n"
        "def main() -> None:\n"
        "    print(pass_values(1, 2))\n"
        "main()\n"
    )

    def test_value_tuple_literal_unaffected(self, tmp_path):
        # BLAST-SWEEP neighbour: a VALUE-element tuple slot has no
        # pointer-repr element, so it keeps its own (already landed) row --
        # the new gate row must not claim it.
        (tmp_path / "mrecarg_helper.py").write_text(
            _HELPER
            + "def take_pair(t: tuple[Int32, Int32]) -> Int32:\n"
              "    return t[0] + t[1]\n")
        thir, faces = _lower_ctx_witnessed(self._VALUE_SRC,
                                           extra_lib_dirs=[tmp_path])
        assert _fn(thir, "pass_values") is not None
        assert not faces.get("arg.btuple_literal_marker")
        assert (_cpp(self._VALUE_SRC, tmp_path, thir=True)
                == _cpp(self._VALUE_SRC, tmp_path, thir=False))


class TestInlineMethodDriverAndExpansion:
    """`@inline` bodies are never ANALYZED by sema and never EMITTED by
    codegen, so the THIR feed must not attempt them (it would walk a body
    with empty `expr_types`); the call site renders the substituted body in
    place, inheriting the consumer's use."""

    _SRC = (
        "from tpy import Int32, inline\n"
        "def sink(n: Int32) -> None:\n"
        "    print(n)\n"
        "class Box:\n"
        "    v: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.v = v\n"
        "    @inline\n"
        "    def emit(self) -> None:\n"
        "        sink(self.v)\n"
        "def main() -> None:\n"
        "    b = Box(3)\n"
        "    b.emit()\n"
        "main()\n"
    )

    def test_inline_method_contributes_no_fallback(self):
        _ctx, fell = _thir_ctx(self._SRC)
        assert fell == {}, fell

    def test_expansion_renders_in_place(self):
        thir, faces = _lower_ctx_witnessed(self._SRC)
        assert faces.get("call.fstr_expansion")
        out = "".join(_assert_routes_byte_identical(self._SRC))
        assert "sink(b.v);" in out
        # The @inline method itself has no C++ definition on either path.
        assert "::emit(" not in out

    def test_non_inline_sibling_still_takes_the_method_arm(self):
        # BOUNDARY: the same body WITHOUT @inline is an ordinary method --
        # emitted, called normally, and never routed through the expansion
        # arm.
        src = self._SRC.replace("    @inline\n", "")
        thir, faces = _lower_ctx_witnessed(src)
        assert not faces.get("call.fstr_expansion")
        out = "".join(_assert_routes_byte_identical(src))
        assert "b.emit();" in out


class TestNativeRecordCtorPositions:
    """A plain `@native` record ctor whose `__init__` carries only a
    `native_name` falls through `_gen_call` to the RECORD branch, which
    emits `native_name(args)` -- the same render the ctor lowering already
    spells. Opened at the DECL/value slot and the ctor MIL only."""

    _SRC = (
        "from tpy.extern import native\n"
        "from tpy import Int32\n"
        "@native('mylog::NR')\n"
        "class NR:\n"
        "    @native('mylog::NR')\n"
        "    def __init__(self, x: Int32) -> None: ...\n"
        "class Holder:\n"
        "    _h: NR\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self._h = NR(x)\n"
        "def make(x: Int32) -> None:\n"
        "    h = NR(x)\n"
        "    print(1)\n"
        "def main() -> None:\n"
        "    make(2)\n"
        "    Holder(3)\n"
        "main()\n"
    )

    def test_decl_slot_and_mil_route(self):
        _ctx, faces, fell = _thir_ctx_witnessed(self._SRC)
        assert faces.get("ctor.native_plain")
        assert faces.get("mil.native_ctor")
        assert fell == {}, fell
        out = "".join(_assert_routes_byte_identical(self._SRC))
        assert "::mylog::NR h = ::mylog::NR(x);" in out
        assert "_h(::mylog::NR(x))" in out

    _NESTED_SRC = (
        "from tpy.extern import native\n"
        "from tpy import Int32\n"
        "@native('mylog::NR')\n"
        "class NR:\n"
        "    @native('mylog::NR')\n"
        "    def __init__(self, x: Int32) -> None: ...\n"
        "@native('mylog::use_nr')\n"
        "def use_nr(r: NR) -> Int32: ...\n"
        "def go(x: Int32) -> Int32:\n"
        "    return use_nr(NR(x))\n"
        "def main() -> None:\n"
        "    print(go(2))\n"
        "main()\n"
    )

    def test_nested_arg_position_stays_ast(self):
        # BOUNDARY: the same ctor rvalue at a NESTED ARG position keeps
        # rejecting -- its construction/temp semantics are outside the
        # ctor-rvalue arg slice (test_thir_callargs.py's
        # test_native_record_ctor_stays_ast fences the same shape).
        thir = _lower_ctx(self._NESTED_SRC)
        assert _fn(thir, "go") is None
        _assert_byte_identical(self._NESTED_SRC)

    _NATIVE_C_SRC = (
        "from tpy.extern import native\n"
        "from tpy import Int32\n"
        "@native('CPod', binding='C')\n"
        "class CPod:\n"
        "    x: Int32\n"
        "    @native('CPod')\n"
        "    def __init__(self, x: Int32) -> None: ...\n"
        "def make(x: Int32) -> None:\n"
        "    p = CPod(x)\n"
        "    print(p.x)\n"
        "def main() -> None:\n"
        "    make(2)\n"
        "main()\n"
    )

    def test_native_c_aggregate_ctor_stays_ast(self):
        # BOUNDARY: `@native_c` emits the aggregate `CPod{args}` brace init
        # from a DIFFERENT AST arm; the row excludes it (no witness).
        _ctx, faces, fell = _thir_ctx_witnessed(self._NATIVE_C_SRC)
        assert fell == {"body:expr.call": 1}, fell
        assert not faces.get("ctor.native_plain")
        _assert_byte_identical(self._NATIVE_C_SRC)
