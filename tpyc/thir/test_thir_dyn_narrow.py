"""Polymorphic-isinstance narrowing (dyn-protocol / inheritance subjects):
if-init cast condition, spelled reads, and the excluded-face fallbacks."""
from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_byte_identical, _compile, _entry, _fn, _lower_ctx,
    _lower_ctx_witnessed,
)

_PRELUDE = (
    "from typing import Protocol\n"
    "from tpy import dynamic\n"
    "@dynamic\n"
    "class Pet(Protocol):\n"
    "    def name(self) -> str: ...\n"
    "class Dog(Pet):\n"
    "    def name(self) -> str:\n        return \"dog\"\n"
    "class Cat:\n"
    "    label: str\n"
    "    def __init__(self, label: str) -> None:\n"
    "        self.label = label\n"
    "    def name(self) -> str:\n        return self.label\n"
    "    def purr(self) -> str:\n        return self.label + \"-purr\"\n"
)


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


class TestDynNarrowLowering:
    SRC = (
        _PRELUDE
        + "def structural(p: Pet) -> str:\n"
        + "    if isinstance(p, Cat):\n"
        + "        return \"cat:\" + p.purr()\n"
        + "    return \"other:\" + p.name()\n"
        + "def inherits(p: Pet) -> str:\n"
        + "    if isinstance(p, Dog):\n"
        + "        return \"dog:\" + p.name()\n"
        + "    return \"other\"\n"
    )

    def test_routing_is_non_vacuous(self):
        thir = _lower_ctx(self.SRC)
        for name in ("structural", "inherits"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        # Structural conformer: adapter cast through the @dynamic base.
        assert ("if (Cat* __p_ptr = ::tpy::dyn_adapter_cast<Pet, Cat>(&p); "
                "(__p_ptr != nullptr)) {") in out
        # Inheritance conformer: plain dynamic_cast.
        assert ("if (Dog* __p_ptr = dynamic_cast<Dog*>(&p); "
                "(__p_ptr != nullptr)) {") in out
        # Branch reads render the pre-bound pointer's deref.
        assert "(*__p_ptr).purr()" in out

    def test_elif_link_routes(self):
        # A dyn condition in an elif link: each chain link gates
        # independently through the shared skeleton.
        src = (
            _PRELUDE
            + "from tpy import Int32\n"
            + "def f(p: Pet, k: Int32) -> str:\n"
            + "    if k > 0:\n        return \"k\"\n"
            + "    elif isinstance(p, Cat):\n"
            + "        return p.purr()\n"
            + "    return p.name()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert ("} else if (Cat* __p_ptr = "
                "::tpy::dyn_adapter_cast<Pet, Cat>(&p); "
                "(__p_ptr != nullptr)) {") in out

    def test_while_head_falls_back(self):
        # The while-isinstance position is an excluded rung for the dyn arm
        # (the fresh-reference-local face, not the if-init form).
        src = (
            _PRELUDE
            + "from tpy import Int32\n"
            + "def f(p: Pet) -> Int32:\n"
            + "    n = 0\n"
            + "    while isinstance(p, Cat):\n"
            + "        n += 1\n"
            + "        if n > 3:\n            break\n"
            + "    return n\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_optional_subject_falls_back(self):
        # Pointer-repr Optional subjects spell the cast input differently
        # (bare name, not &name) -- excluded from the slice.
        src = (
            _PRELUDE
            + "def f(p: Pet | None) -> str:\n"
            + "    if p is not None:\n"
            + "        if isinstance(p, Cat):\n"
            + "            return p.purr()\n"
            + "    return \"no\"\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None

    def test_tuple_check_routes_or_chain(self):
        # A tuple check yields a union fact (no alias); the condition
        # routes as the no-init dynamic_cast OR-chain
        # (THIRDynIsinstanceMulti) -- dualgen-verified identical, the
        # structural-conformer cast riding narrow_cast_rhs.
        src = (
            _PRELUDE
            + "def f(p: Pet) -> str:\n"
            + "    if isinstance(p, (Cat, Dog)):\n"
            + "        return \"pet\"\n"
            + "    return \"no\"\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        _assert_byte_identical(src)


class TestDerefViewNarrowIf:
    """The deref-view isinstance if (wave 10): `isinstance(b, Dog)` through
    a Deref wrapper (Box[Pet]) lowers the C++17 if-init cast of the deref
    PAYLOAD pointer; branch member calls carrying deref_narrowed_to read
    `(*__b_ptr)`; post-branch calls revert to the deref chain. The wrapper
    var is never retyped (sema keys the fact under deref_view_key). A
    same-module @dynamic-protocol type-arg is in the F1 slice (both paths
    spell the bare name); imported ones keep rejecting."""

    _SRC = (
        "from typing import Protocol\n"
        "from tpy import Int32, dynamic\n"
        "from tplib.box import Box\n"
        "@dynamic\n"
        "class Pet(Protocol):\n"
        "    def name(self) -> str: ...\n"
        "class Dog(Pet):\n"
        "    def name(self) -> str:\n"
        "        return \"dog\"\n"
        "    def bark(self) -> str:\n"
        "        return \"woof\"\n"
        "class Cat(Pet):\n"
        "    def name(self) -> str:\n"
        "        return \"cat\"\n"
        "def describe(b: Box[Pet]) -> str:\n"
        "    if isinstance(b, Dog):\n"
        "        return \"dog:\" + b.bark()\n"
        "    return \"other:\" + b.name()\n"
        "def main() -> None:\n"
        "    print(describe(Box(Dog())))\n"
        "    print(describe(Box(Cat())))\n"
        "main()\n"
    )

    def test_deref_view_if_routes(self):
        thir, faces = _lower_ctx_witnessed(self._SRC)
        assert _fn(thir, "describe") is not None
        assert faces.get("if.deref_view_narrow", 0) >= 1
        assert faces.get("method.deref_view_narrowed", 0) >= 1
        _assert_byte_identical(self._SRC)

    def test_emit_shapes(self):
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(self._SRC)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        out = hpp + cpp
        assert "__b_ptr = dynamic_cast<Dog*>(&(b.__deref__()));" in out
        assert "(*__b_ptr).bark()" in out
        assert "b.__deref__().name()" in out

    def test_narrowed_call_with_args_defers(self):
        # The witnessed slice is zero-arg methods; an ARG-carrying narrowed
        # call keeps the whole-body fallback.
        src = self._SRC.replace(
            "    def bark(self) -> str:\n        return \"woof\"\n",
            "    def bark(self, n: Int32) -> str:\n"
            "        return \"woof\" * n\n").replace(
            "b.bark()", "b.bark(2)")
        thir, _f = _lower_ctx_witnessed(src)
        assert _fn(thir, "describe") is None
        _assert_byte_identical(src)

    def test_branch_rebind_of_wrapper_defers(self):
        # Reassigning the wrapper LOCAL inside the narrowed branch (the
        # deref_view_rebind_invalidates shape): nothing may keep reading
        # the stale cast pointer -- the body defers whole.
        src = self._SRC.replace(
            "def describe(b: Box[Pet]) -> str:\n"
            "    if isinstance(b, Dog):\n"
            "        return \"dog:\" + b.bark()\n"
            "    return \"other:\" + b.name()\n",
            "def describe() -> str:\n"
            "    b: Box[Pet] = Box(Dog())\n"
            "    if isinstance(b, Dog):\n"
            "        b = Box(Cat())\n"
            "        return \"rebound:\" + b.name()\n"
            "    return \"other:\" + b.name()\n").replace(
            "    print(describe(Box(Dog())))\n"
            "    print(describe(Box(Cat())))\n",
            "    print(describe())\n")
        thir, _f = _lower_ctx_witnessed(src)
        assert _fn(thir, "describe") is None
        _assert_byte_identical(src)

    def test_structural_conformer_adapter_cast(self):
        # The STRUCTURAL flavor (Cat conforms without inheriting): the
        # if-init spells dyn_adapter_cast over the same deref payload.
        src = self._SRC.replace(
            "    if isinstance(b, Dog):\n"
            "        return \"dog:\" + b.bark()\n",
            "    if isinstance(b, Cat):\n"
            "        return \"cat:\" + b.name()\n").replace(
            "class Cat(Pet):\n", "class Cat:\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "describe") is not None
        assert faces.get("if.deref_view_narrow", 0) >= 1
        from ..codegen_cpp.context import CodeGenOptions
        compiler, modules = _compile(src)
        entry = _entry(modules)
        hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        out = hpp + cpp
        assert "::tpy::dyn_adapter_cast<Pet, Cat>(&(b.__deref__()))" in out
        assert "(*__b_ptr).name()" in out
        _assert_byte_identical(src)
