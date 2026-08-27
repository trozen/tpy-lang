"""Macro-authored staticmethods: record-owned via `self_type` alone.

`FragmentParser.parse_fragment` decides method-ness by "first param named
self", so an `ast.quote_fun` fragment without one parses as a FREE function;
`quote_fun`'s docstring then prescribes setting `is_staticmethod = True`. The
result reaches `_check_callable_structure` with `is_method` clear -- the same
construct a hand-written `@staticmethod` spells with the flag set.
"""
from __future__ import annotations

MACRO_MOD = '''# tpy: macro_module
"""Adds a staticmethod through the quote_fun + is_staticmethod spelling."""
from tpyc.macro_api import ClassInfo, class_macro, ast


@class_macro
def addstatic(cls: ClassInfo) -> None:
    f = ast.quote_fun("""
def field_count() -> Int32:
    return {n}
""".format(n=len(cls.fields)))
    f.is_staticmethod = True
    cls.add_method(f)
'''

SRC = (
    "from tpy import Int32\n"
    "from macrostatic import addstatic\n"
    "@addstatic\n"
    "class Point:\n"
    "    x: Int32\n"
    "    y: Int32\n"
    "    def __init__(self, x: Int32, y: Int32) -> None:\n"
    "        self.x = x\n"
    "        self.y = y\n"
    "    @staticmethod\n"
    "    def origin_dim() -> Int32:\n"
    "        return 2\n"
    "def main() -> None:\n"
    "    print(Point.field_count() + Point.origin_dim())\n"
    "main()\n")


def _with_macro(tmp_path):
    (tmp_path / "macrostatic.py").write_text(MACRO_MOD)
    return [tmp_path]


class TestMacroStaticmethodRoutes:

    def test_macro_static_routes_byte_identical(self, tmp_path):
        # ROUTING pin: `_assert_routes_byte_identical` fails unless EVERY body
        # lowered -- byte-identity alone is satisfied by a fallback here.
        from .testutil import _assert_routes_byte_identical
        _hpp, cpp = _assert_routes_byte_identical(
            SRC, extra_lib_dirs=_with_macro(tmp_path))
        assert "int32_t Point::field_count() {" in cpp or \
               "int32_t Point::field_count() {" in _hpp

    def test_macro_static_witnesses_its_face(self, tmp_path):
        from .testutil import _lower_ctx_witnessed
        _thir, wit = _lower_ctx_witnessed(
            SRC, extra_lib_dirs=_with_macro(tmp_path))
        assert wit.get("fn.macro_staticmethod", 0) == 1


COLLIDE_MAIN = (
    "from typing import overload\n"
    "from tpy import Int32\n"
    "from macrostatic import addstatic\n"
    "@overload\n"
    "def field_count(a: Int32) -> Int32: ...\n"
    "@overload\n"
    "def field_count(a: str) -> Int32: ...\n"
    "def field_count(a: Int32 | str) -> Int32:\n"
    "    return 1\n"
    "@addstatic\n"
    "class Point:\n"
    "    x: Int32\n"
    "    def __init__(self, x: Int32) -> None:\n"
    "        self.x = x\n"
    "def main() -> None:\n"
    "    print(Point.field_count() + field_count(1))\n"
    "main()\n")


class TestMacroStaticOverloadLookup:
    """The overload gate must resolve a macro static against the OWNING
    RECORD, not the free-function registry: a same-named free overload set is
    not this callable's, and the free lookup would also return None for a
    record-owned name that IS overloaded (a silent skip)."""

    def test_same_named_free_overload_set_does_not_gate_the_static(
            self, tmp_path):
        from .testutil import _rejects_at, _thir_ctx
        ctx, fb = _thir_ctx(COLLIDE_MAIN,
                            extra_lib_dirs=_with_macro(tmp_path))
        names = {f.name for f in ctx.thir_functions.values()}
        assert "field_count" in names
        # Only the FREE overload impl stays back, and for its own reason.
        # Matched on the landmark: the arity gate may record a blocking
        # shape after it, which a whole-key test would stop seeing.
        assert not _rejects_at(fb, "body:sig.overload_set.arity"), fb


class TestFreeCallableWithFlagStillRejects:
    """The surviving raise: `is_staticmethod` with NO owning record. Only
    `self_type` makes the macro spelling record-owned, so a free callable
    carrying the flag has nothing to resolve its method facts against."""

    def test_free_function_with_staticmethod_flag_rejects(self):
        import pytest
        from .testutil import _compile, _entry
        from ..compilation_context import activate_compiler
        from .lower.functions import _check_callable_structure
        from .fallback import ThirUnsupported
        compiler, modules = _compile(
            "from tpy import Int32\n"
            "def helper() -> Int32:\n"
            "    return 3\n"
            "def main() -> None:\n"
            "    print(helper())\n"
            "main()\n")
        entry = _entry(modules)
        func = next(f for f in entry.ast.functions if f.name == "helper")
        func.is_staticmethod = True
        with activate_compiler(compiler):
            with pytest.raises(ThirUnsupported) as ei:
                _check_callable_structure(func, entry.analyzer, None)
        assert ei.value.reason == "sig.staticmethod_flag"
