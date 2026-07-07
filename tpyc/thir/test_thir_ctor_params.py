"""Ctor param-gate widening: String / value-repr Optional / Ptr / union /
Own[...] param TYPES route the ctor when every USE is per-site admitted or
the param is unused; an unhandled use (or the mutated/reassigned String and
reassign-copy shapes) keeps the whole ctor on the AST path."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from .testutil import _compile, _entry


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

    def test_optval_mil_use_stays_ast(self):
        # `self.x = p` into a value-repr Optional field: the AST hoists
        # `x(p)`, a MIL arm this cell does not open -> whole ctor AST.
        src = (_I32
               + "class C:\n    x: Int32 | None\n    y: Int32\n"
               + "    def __init__(self, p: Int32 | None) -> None:\n"
               + "        self.x = p\n        self.y = 1\n")
        ctor, reason = _lower_ctor_reason(src, "C")
        assert ctor is None
        assert reason.startswith("ctor.mil_field")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_optval_narrowed_read_stays_ast(self):
        # A narrowed read renders `(*p)` on the AST path -- the name-read
        # guard rejects it, so the body statement (and the ctor) falls back.
        src = _ctor_src("Int32 | None",
                        "        if p is not None:\n"
                        "            self.y = p + 1\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is None
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

    def test_own_str_body_read_stays_ast(self):
        # An Own[str] name read has no arm (the AST may move a movable
        # own-param at last use) -- the name-read guard rejects it.
        src = _ctor_src("Own[str]",
                        "        x = p\n        print(x)\n",
                        prelude=self._PRE)
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_own_str_mil_use_stays_ast(self):
        # `self.s = p` (str field): the AST hoists `s(std::move(p))`; no str
        # MIL arm in this cell -> whole ctor AST, never a demote.
        src = ("from tpy import Int32, Own\n"
               "class C:\n    s: str\n    y: Int32\n"
               "    def __init__(self, p: Own[str]) -> None:\n"
               "        self.s = p\n        self.y = 1\n")
        ctor, reason = _lower_ctor_reason(src, "C")
        assert ctor is None
        assert reason.startswith("ctor.mil_field")
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

    def test_mixed_value_union_print_stays_ast(self):
        src = _ctor_src("str | Int32", "        print(p)\n")
        ctor, _ = _lower_ctor_reason(src, "C")
        assert ctor is None
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
