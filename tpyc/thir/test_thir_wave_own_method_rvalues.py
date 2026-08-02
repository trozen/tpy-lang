"""Own-returning METHOD rvalues at two more sinks (wave 3b of the
mechanical residue): the borrow-param arg temp and the ctor MIL field.

Both reuse `_method_rvalue_f1_record` (the shared disjunct from the
owning-reseat wave); the renders are existing machinery -- the
create-lend-drop ArgTemp and the MIL direct construct.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _thir_fallbacks(source, extra_lib_dirs=None):
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_COUNTER = (
    "from tpy import Int32\n"
    "from tplib import Rc\n"
    "class Counter:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "    def value(self) -> Int32:\n"
    "        return self.n\n"
)


class TestMethodRvalueArgTemp:
    SRC = _COUNTER + (
        "def read_rc(c: Rc[Counter]) -> Int32:\n"
        "    return c.get().value()\n"
        "def main() -> None:\n"
        "    print(read_rc(Rc.new(Counter(3))))\n"
        "main()\n"
    )

    def test_own_static_rvalue_arg_hoists_temp(self):
        # `read_rc(Rc.new(Counter(3)))` -> the create-lend-drop temp
        # (`Rc<Counter> __tmp_1 = Rc<Counter>::new_<Counter>(...);`), NOT
        # an inline render -- only NATIVE record calls render inline.
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "__tmp_1 = Rc<Counter>::new_<Counter>(Counter(3));" in cpp
        assert "read_rc(__tmp_1)" in cpp

    def test_witnessed_at_record_rvalue_temp(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert wit.get("argtemp.record_rvalue", 0) >= 1


class TestMarkerPositionStaysInline:
    def test_qualified_callee_arg_renders_inline(self, tmp_path):
        # The ospath-divergence pin: an Own-returning call as the arg of a
        # MODULE-QUALIFIED callee renders INLINE (the qualcall loop has no
        # temp machinery) -- the hoist is a free-call-position row only.
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "sub.py").write_text(
            "from tpy import Int32, Own\n"
            "class Rec:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def make(n: Int32) -> Own[Rec]:\n"
            "    return Rec(n)\n"
            "def use(r: Rec) -> Int32:\n"
            "    return r.n\n"
        )
        src = ("from pkg import sub\nfrom tpy import Int32\n"
               "def f() -> None:\n"
               "    print(sub.use(sub.make(Int32(9))))\n")
        hpp, cpp = _assert_routes_byte_identical(
            src, extra_lib_dirs=[tmp_path])
        assert ("::tpyapp::pkg::sub::use(::tpyapp::pkg::sub::make(9))"
                in cpp)
        assert "__tmp_1" not in cpp


class TestMilRecordMethodRvalue:
    SRC = (
        "from tpy import Int32\n"
        "from tplib import Rc\n"
        "class Val:\n"
        "    x: Int32\n"
        "    def __init__(self, x: Int32) -> None:\n"
        "        self.x = x\n"
        "class Holder:\n"
        "    shared: Rc[Val]\n"
        "    def __init__(self) -> None:\n"
        "        self.shared = Rc.new(Val(0))\n"
        "def main() -> None:\n"
        "    h = Holder()\n"
        "    print(h.shared.get().x)\n"
        "main()\n"
    )

    def test_mil_direct_construct_routes(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "shared(Rc<Val>::new_<Val>(Val(0)))" in hpp + cpp

    def test_mil_witnessed(self):
        # Constructors lower through the generator drive (lower_constructor),
        # not lower_module, so the witness is read off the full-emit run.
        compiler, modules = _compile(self.SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False,
                                   comment_line_numbers=False,
                                   thir_codegen=True))
        wit = compiler._thir_face_witnesses
        assert wit.get("mil.record_method_rvalue", 0) >= 1

    def test_borrow_returning_method_mil_keeps_rejecting(self):
        # A BORROW-returning method at the MIL field slot is not an rvalue
        # source -- the direct-construct row must not claim it (the copy of
        # a live object's member is the value-position design stop).
        src = (
            "from tpy import Int32, Own\n"
            "class Val:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Src:\n"
            "    v: Val\n"
            "    def __init__(self, v: Own[Val]) -> None:\n"
            "        self.v = v\n"
            "    def peek(self) -> Val:\n"
            "        return self.v\n"
            "class Holder:\n"
            "    mine: Val\n"
            "    def __init__(self, s: Src) -> None:\n"
            "        self.mine = s.peek()\n"
        )
        fell = _thir_fallbacks(src)
        assert any(k.startswith("ctor:") for k in fell), fell
