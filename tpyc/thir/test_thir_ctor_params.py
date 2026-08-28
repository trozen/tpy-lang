"""Ctor param-gate widening: String / value-repr Optional / Ptr / union /
Own[...] param TYPES route the ctor when every USE is per-site admitted or
the param is unused; an unhandled use (or the mutated/reassigned String and
reassign-copy shapes) keeps the whole ctor on the AST path."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from .testutil import _compile, _ctor_tail, _entry


def _lower_ctor_reason(source: str, record_name: str):
    """(THIRConstructor | None, first-reject reason | None) for one record."""
    from .lower import iter_module_constructors, lower_constructor
    compiler, modules = _compile(source)
    entry = _entry(modules)
    with activate_compiler(compiler):
        for rec, init, self_type in iter_module_constructors(
                entry.ast, entry.analyzer):
            if rec.name == record_name:
                compiler._thir_reject_reason = None
                ctor = lower_constructor(rec, init, entry.analyzer,
                                         self_type=self_type)
                return ctor, compiler._thir_reject_reason
    return None, "record_not_found"


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


_I32 = "from tpy import Int32\n"


def _ctor_src(param: str, body: str, prelude: str = _I32) -> str:
    return (prelude
            + "class C:\n    y: Int32\n"
            + f"    def __init__(self, p: {param}) -> None:\n"
            + "        self.y = 1\n"
            + body)


class TestOptvalParams:
    def test_unused_optval_param_routes(self):
        src = _ctor_src("Int32 | None", "")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_optval_mil_use_routes(self):
        # `self.x = p` into a value-repr Optional field: the AST hoists
        # `x(p)`, mirrored by the mil.optional_value_copy arm (same-typed
        # optional param, bare whole-optional copy).
        src = (_I32
               + "class C:\n    x: Int32 | None\n    y: Int32\n"
               + "    def __init__(self, p: Int32 | None) -> None:\n"
               + "        self.x = p\n        self.y = 1\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_optval_narrowed_read_routes(self):
        # A narrowed read of a value-repr Optional[cheap scalar] param unwraps
        # `(*p)` (deref-on-narrow), so the ctor body routes byte-identically.
        src = _ctor_src("Int32 | None",
                        "        if p is not None:\n"
                        "            self.y = p + 1\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_optval_bigint_block_local_read_routes(self):
        # A BigInt block-local first-declared inside the narrowed branch
        # (`x = p`, the if hoists nothing) is a value block-local -- it lowers
        # in place, byte-identically to the AST read.
        src = _ctor_src("int | None",
                        "        if p is not None:\n"
                        "            self.y = 1\n"
                        "            x = p\n"
                        "            print(x)\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_optval_bytes_unused_routes(self):
        src = _ctor_src("bytes | None", "")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestStringParams:
    _PRE = "from tpy import Int32, String\n"

    def test_string_param_body_reads_route(self):
        # Bare reads of a `const std::string&` param render like an owned
        # local (decl copy, print, compare) -- byte-identical.
        src = _ctor_src("String",
                        "        x = p\n        print(x)\n        print(p)\n"
                        "        if p == \"hi\":\n            self.y = 2\n",
                        prelude=self._PRE)
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_string_concat_routes(self):
        src = _ctor_src("String",
                        "        q = p + \"!\"\n        print(q)\n",
                        prelude=self._PRE)
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_mutated_string_param_stays_ast(self):
        # `name += ...` on a String param: the AST writes through the const
        # ref (ill-formed C++, BUGS.md) -- keep the whole shape AST-owned.
        src = _ctor_src("String",
                        "        p += \"!\"\n        print(p)\n",
                        prelude=self._PRE)
        ctor, reason = _lower_ctor_reason(src, "C")
        assert ctor is None
        assert reason == "ctor.param_mutated_string"

    def test_reassigned_string_param_stays_ast(self):
        src = _ctor_src("String",
                        "        p = \"x\"\n        print(p)\n",
                        prelude=self._PRE)
        ctor, reason = _lower_ctor_reason(src, "C")
        assert ctor is None
        assert reason == "ctor.param_reassign_copy"


class TestReassignCopyParams:
    def test_reassigned_bigint_param_stays_ast(self):
        # Regression guard: gen_body emits the `__param_` copy prologue for a
        # ctor body while the ctor signature never renames -- before the
        # ctor.param_reassign_copy reject THIR routed this body and silently
        # dropped the prologue line.
        src = ("from tpy import Int32\n"
               "class A:\n    x: Int32\n"
               "    def __init__(self, n: int) -> None:\n"
               "        n = n + 1\n        self.x = 7\n")
        ctor, reason = _lower_ctor_reason(src, "A")
        assert ctor is None
        assert reason == "ctor.param_reassign_copy"
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_reassigned_scalar_param_still_routes(self):
        # By-value params reassign in place on both paths -- no prologue.
        src = ("from tpy import Int32\n"
               "class A:\n    x: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        n = n + 1\n        self.x = n\n")
        ctor, _ = _lower_ctor_reason(src, "A")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestOwnParams:
    _PRE = "from tpy import Int32, Own\n"

    def test_unused_own_str_param_routes(self):
        src = _ctor_src("Own[str]", "", prelude=self._PRE)
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_own_str_body_read_routes(self):
        # An Own[str] param's bare read is STORAGE (the signature spells the
        # owned `std::string` by value -- `_own_viewfam_param`): the decl init
        # and print render the plain name, no view->owned wrap, no move
        # (`seed_param_locals` never seeds value payloads movable).
        src = _ctor_src("Own[str]",
                        "        x = p\n        print(x)\n",
                        prelude=self._PRE)
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_own_str_mil_use_moves(self):
        # `self.s = p` (str field) at the param's LAST USE: the own-param move
        # is type-agnostic, so the view-family field takes it like any other.
        src = ("from tpy import Int32, Own\n"
               "class C:\n    s: str\n    y: Int32\n"
               "    def __init__(self, p: Own[str]) -> None:\n"
               "        self.s = p\n        self.y = 1\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _ctor_tail(ctor) == " : s(std::move(p)), y(1) {}\n"
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_own_container_mil_move_routes(self):
        # Composed with the container cell's MIL name row: the admitted
        # `Own[list[T]]` param moves into the field (`xs(std::move(p))`).
        src = ("from tpy import Int32, Own\n"
               "class C:\n    xs: list[Int32]\n"
               "    def __init__(self, p: Own[list[Int32]]) -> None:\n"
               "        self.xs = p\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestUnionAndPtrParams:
    def test_unused_mixed_value_union_param_routes(self):
        # `str | Int32` fails the U1 scalar-member slice as a READ, but the
        # param TYPE alone cannot diverge -- unused, the ctor routes.
        src = _ctor_src("str | Int32", "")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_mixed_value_union_print_routes_str_visitor(self):
        # A union-typed NAME print arg streams via the `::tpy::__str__`
        # visitor (the union-returns wave's PrintForm.STR row), so the
        # ctor routes now.
        src = _ctor_src("str | Int32", "        print(p)\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_unused_nonvalue_ptr_param_routes(self):
        # A Ptr pointee outside _eligible_ptr_value (StrView) -- the bare
        # `T*` param spells AST-side; unused, the ctor routes.
        src = ("from tpy import Int32, Ptr, StrView\n"
               "class C:\n    y: Int32\n"
               "    def __init__(self, q: Ptr[StrView]) -> None:\n"
               "        self.y = 5\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_nonvalue_ptr_mil_use_stays_ast(self):
        src = ("from tpy import Int32, Ptr, StrView\n"
               "class C:\n    q: Ptr[StrView]\n    y: Int32\n"
               "    def __init__(self, q: Ptr[StrView]) -> None:\n"
               "        self.q = q\n        self.y = 1\n")
        ctor, reason = _lower_ctor_reason(src, "C")
        assert ctor is None
        assert reason.startswith("ctor.mil_field")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    _PTR_DATA = ("from tpy import Int32, Ptr\n"
                 "class Data:\n    value: Int32\n"
                 "    def __init__(self, v: Int32) -> None:\n"
                 "        self.value = v\n")

    def test_ptr_null_ctor_mil_routes(self):
        # `self.p = Ptr[Data]()`: the MIL slot is a storage sink, so the
        # null ctor threads STORAGE use and renders the typed nullptr
        # (`p(static_cast<Data*>(nullptr))`, ctor.ptr_null).
        src = (self._PTR_DATA
               + "class C:\n    p: Ptr[Data]\n"
               + "    def __init__(self) -> None:\n"
               + "        self.p = Ptr[Data]()\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        assert "p(static_cast<Data*>(nullptr))" in _cpp(src, thir=True)

    def test_nonvalue_ptr_null_ctor_mil_stays_ast(self):
        # BOUNDARY: a Ptr pointee outside _eligible_ptr_value (StrView)
        # never reaches the STORAGE thread -- the MIL field gate itself
        # keeps the ctor on the AST path.
        src = ("from tpy import Int32, Ptr, StrView\n"
               "class C:\n    q: Ptr[StrView]\n"
               "    def __init__(self) -> None:\n"
               "        self.q = Ptr[StrView]()\n")
        ctor, reason = _lower_ctor_reason(src, "C")
        assert ctor is None
        assert reason == "ctor.mil_field.ptr.call"
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
