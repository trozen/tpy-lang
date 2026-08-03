"""Long-tail isinstance rows: the STATIC type-param family
(`isinstance(x, Animal)` on a bounded-T subject -> the
isinstance_static<M, decltype(x)>() trait, no extraction alias) and the
module-level `Any` GLOBAL subject (the value-typed global reads bare, so
the D15 typeid machinery renders it like a local). The deref-view
init-statement family stays AST."""

from __future__ import annotations

from .testutil import (
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
    _assert_routes_byte_identical,
)


class TestIsinstanceLongTail:
    def test_typeparam_static_isinstance_routes(self):
        src = ("from tpy import Int32\n"
               "class Animal:\n"
               "    def __init__(self) -> None:\n"
               "        pass\n"
               "class Dog(Animal):\n"
               "    def __init__(self) -> None:\n"
               "        pass\n"
               "def classify[T: Dog](x: T) -> Int32:\n"
               "    code = 0\n"
               "    if isinstance(x, Animal):\n"
               "        code += 1\n"
               "    return code\n"
               "def main() -> None:\n"
               "    print(classify(Dog()))\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "classify") is not None
        assert faces.get("cond.isinstance_static", 0) == 1
        cpp = _assert_routes_byte_identical(src)
        assert ("if (::tpy::isinstance_static<Animal, decltype(x)>())"
                in cpp[0])

    def test_any_global_isinstance_routes(self):
        src = ("from typing import Any\n"
               "g: Any = 5\n"
               "def main() -> None:\n"
               "    if isinstance(g, int):\n"
               "        print(g + 1)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert ("g.value.has_value() && g.value.type() == "
                "typeid(::tpy::BigInt)" in cpp[1])

    def test_deref_view_isinstance_stays_ast(self):
        # The Box deref-view family (the C++17 if-init dynamic_cast) keeps
        # falling back.
        src = ("from typing import Protocol\n"
               "from tpy import dynamic\n"
               "from tplib import Box\n"
               "@dynamic\n"
               "class Pet(Protocol):\n"
               "    def name(self) -> str: ...\n"
               "class Dog:\n"
               "    def name(self) -> str:\n"
               "        return \"dog\"\n"
               "class Cat:\n"
               "    def name(self) -> str:\n"
               "        return \"cat\"\n"
               "def f() -> str:\n"
               "    b: Box[Pet] = Box(Dog())\n"
               "    if isinstance(b, Dog):\n"
               "        return \"was-dog\"\n"
               "    return b.name()\n"
               "def main() -> None:\n"
               "    print(f())\n"
               "main()\n")
        assert _fn(_lower_ctx(src), "f") is None
        _assert_byte_identical(src)
