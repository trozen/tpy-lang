"""Value-variant narrowing over element bindings: a ptr-variant-TYPED union
(`Dog | Cat`) reaching a narrowing subject through a binding that is NOT a
pointer variant.

A container element binds the storage form (`std::variant<Cat, Dog>`), so the
extraction is `std::get<Dog>(x)` -- no `*std::get<Dog*>`. The verdict is the
BINDING's (`ctx.ptr_variant_locals` membership), which is why these route only
now that the mirror is known complete; the subject spelling additionally derefs
when the binding is a pointer (a resumable pointer-form loop var).
"""

from __future__ import annotations

import io

from .emit import emit_thir_body
from .testutil import _lower_ctx, _fn, _assert_byte_identical, _compile, _entry
from ..codegen_cpp import CodeGenOptions


def _body(thir, name: str) -> str:
    buf = io.StringIO()
    emit_thir_body(buf, _fn(thir, name))
    return buf.getvalue()


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp


_PETS = (
    "from typing import Iterator\n"
    "from tpy import Int32, readonly\n"
    "class Dog:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n        self.n = n\n"
    "class Cat:\n"
    "    m: Int32\n"
    "    def __init__(self, m: Int32) -> None:\n        self.m = m\n"
)


class TestElementBinding:
    def test_isinstance_over_element_extracts_by_value(self):
        src = _PETS + (
            "def f(xs: list[Dog | Cat]) -> Int32:\n"
            "    total = 0\n"
            "    for x in xs:\n"
            "        if isinstance(x, Dog):\n"
            "            total += x.n\n"
            "        else:\n"
            "            total += x.m\n"
            "    return total\n")
        body = _body(_lower_ctx(src), "f")
        # The element is the VALUE variant: no `Dog*` template arg, no deref.
        assert "if (std::holds_alternative<Dog>(x)) {" in body
        assert "auto& __x = std::get<Dog>(x);" in body
        _assert_byte_identical(src)

    def test_match_over_element_extracts_by_value(self):
        src = _PETS + (
            "def f(xs: list[Dog | Cat]) -> Int32:\n"
            "    total = 0\n"
            "    for x in xs:\n"
            "        match x:\n"
            "            case Dog():\n"
            "                total += x.n\n"
            "            case Cat():\n"
            "                total += x.m\n"
            "    return total\n")
        body = _body(_lower_ctx(src), "f")
        # The union match tier is INDEX-keyed; the value binding drops the `*`.
        assert "auto& __case_0 = std::get<1>(__match_subject_1);" in body
        _assert_byte_identical(src)

    def test_param_binding_still_extracts_by_pointer(self):
        # The other side of the same verdict: a PARAM is a pointer variant, so
        # the same union narrows through `*std::get<Dog*>`.
        src = _PETS + (
            "def f(p: Dog | Cat) -> Int32:\n"
            "    if isinstance(p, Dog):\n"
            "        return p.n\n"
            "    return p.m\n")
        body = _body(_lower_ctx(src), "f")
        assert "if (std::holds_alternative<Dog*>(p)) {" in body
        assert "auto& __p = *std::get<Dog*>(p);" in body
        _assert_byte_identical(src)

    def test_readonly_source_element_routes(self):
        src = _PETS + (
            "def f(xs: readonly[list[Dog | Cat]]) -> Int32:\n"
            "    total = 0\n"
            "    for x in xs:\n"
            "        if isinstance(x, Cat):\n"
            "            total += x.m\n"
            "        else:\n"
            "            total += x.n\n"
            "    return total\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)

    def test_mixed_scalar_member_element_routes(self):
        src = _PETS + (
            "def f(xs: list[Int32 | Dog]) -> Int32:\n"
            "    total = 0\n"
            "    for x in xs:\n"
            "        if isinstance(x, Dog):\n"
            "            total += x.n\n"
            "        else:\n"
            "            total += x\n"
            "    return total\n")
        assert _fn(_lower_ctx(src), "f") is not None
        _assert_byte_identical(src)


class TestPointerBoundSubject:
    def test_generator_pointer_loop_var_derefs_the_subject(self):
        # A resumable pointer-form loop var is a `T*` frame local, so the
        # isinstance CONDITION reads through it -- `holds_alternative<Dog>((*x))`.
        # The extraction alias comes from the AST skeleton and always derefed;
        # the condition is the half that diverged before the subject spelling
        # learned the pointer binding.
        src = _PETS + (
            "def g(xs: list[Dog | Cat]) -> Iterator[Int32]:\n"
            "    for x in xs:\n"
            "        if isinstance(x, Dog):\n"
            "            yield x.n\n"
            "        else:\n"
            "            yield x.m\n"
            "def main(xs: list[Dog | Cat]) -> None:\n"
            "    for v in g(xs):\n        print(v)\n"
            "main([Dog(1), Cat(2)])\n")
        cpp = _cpp(src, thir=True)
        assert "if (std::holds_alternative<Dog>((*x))) {" in cpp
        assert cpp == _cpp(src, thir=False)
